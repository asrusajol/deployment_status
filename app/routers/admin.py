"""Admin-only user management: grant/reset login access, change roles.

The Users page (the design's editable grid) posts every row to /users/save at once.

Deliberately doesn't create new User rows — every User already comes from the CRM sync
(app/services/sync.py). This only ever adds a password/role to an existing row.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import hash_password, require_admin
from app.database import get_db
from app.models.user import User, UserRole
from app.static_version import STATIC_VERSION
from app import toast

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["static_version"] = STATIC_VERSION
templates.env.globals["take_toasts"] = toast.take

MIN_PASSWORD_LENGTH = 8


@router.get("/users")
def list_users(request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    users = db.query(User).order_by(User.name).all()
    return templates.TemplateResponse(
        request, "admin_users.html", {"users": users, "roles": list(UserRole), "error": None, "current_user": admin}
    )


@router.post("/users/save")
async def save_users(request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """The grid's one Save. Checks every row before changing any, so a bad row (a short
    password, a taken username) saves nothing and the admin fixes it and saves again.

    "Active" is login access: on with a new password grants (or resets) it, off removes
    the password. A switch the page shows locked for that role leaves the value alone —
    Management and Checklist Audit are always on for admins, Show Others only means
    something for admins (see User.can_manage_other_returns)."""
    form = await request.form()
    try:
        ids = [int(v) for v in form.getlist("user_ids")]
    except ValueError:
        raise HTTPException(status_code=400, detail="Bad user id")
    users = {u.id: u for u in db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
    if len(users) != len(set(ids)):
        raise HTTPException(status_code=404, detail="User not found")

    errors: list[str] = []
    plans = []
    claimed_usernames: dict[str, int] = {}
    for user_id in ids:
        user = users[user_id]
        try:
            role = UserRole(form.get(f"role-{user_id}", user.role.value))
        except ValueError:
            raise HTTPException(status_code=400, detail="Unknown role")
        active = form.get(f"active-{user_id}") is not None
        password = str(form.get(f"password-{user_id}", ""))
        username = str(form.get(f"username-{user_id}", "")).strip()
        if user.username:
            username = ""   # only a user without one can be given one here

        if password:
            if not active:
                errors.append(f"{user.name}: switch Active on to give access with this password, or clear it.")
            elif len(password) < MIN_PASSWORD_LENGTH:
                errors.append(f"{user.name}: password must be at least {MIN_PASSWORD_LENGTH} characters.")
        if active and not user.password_hash and not password:
            errors.append(f"{user.name}: set a password to give login access.")
        if active and (password or not user.password_hash) and not user.username and not username:
            errors.append(f"{user.name}: set a username before giving login access.")
        if username:
            taken = db.query(User).filter(User.username == username, User.id != user.id).one_or_none()
            if taken is not None or claimed_usernames.get(username, user.id) != user.id:
                errors.append(f'{user.name}: username "{username}" is already taken.')
            claimed_usernames[username] = user.id
        if not active and user.password_hash and user.id == admin.id:
            errors.append("You can't remove your own login access.")
        plans.append((user, role, active, password, username))

    if errors:
        return _rerender_with_error(request, db, admin, " ".join(errors))

    for user, role, active, password, username in plans:
        user.role = role
        if role == UserRole.admin:
            user.can_manage_other_returns = form.get(f"show_others-{user.id}") is not None
        else:
            user.can_access_management = form.get(f"management-{user.id}") is not None
            user.can_view_checklist_audit = form.get(f"audit-{user.id}") is not None
        if username:
            user.username = username
        if not active:
            user.password_hash = None
        elif password:
            # The admin now knows this password, so the user must pick their own at next login.
            user.password_hash = hash_password(password)
            user.must_change_password = True
    db.commit()
    toast.toast(request, "User settings saved")
    return RedirectResponse(url="/admin/users", status_code=303)


def _get_user_or_404(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _rerender_with_error(request: Request, db: Session, admin: User, error: str):
    users = db.query(User).order_by(User.name).all()
    return templates.TemplateResponse(
        request,
        "admin_users.html",
        {"users": users, "roles": list(UserRole), "error": error, "current_user": admin},
        status_code=400,
    )
