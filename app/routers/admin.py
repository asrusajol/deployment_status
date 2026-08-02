"""Admin-only user management: grant/reset login access, change roles.

Deliberately doesn't create new User rows — every User already comes from the CRM sync
(app/services/sync.py). This only ever adds a password/role to an existing row.
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import hash_password, require_admin
from app.database import get_db
from app.models.user import User, UserRole

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")

MIN_PASSWORD_LENGTH = 8


@router.get("/users")
def list_users(request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    users = db.query(User).order_by(User.name).all()
    return templates.TemplateResponse(
        request, "admin_users.html", {"users": users, "roles": list(UserRole), "error": None, "current_user": admin}
    )


def _get_user_or_404(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.post("/users/{user_id}/set-password")
def set_password(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    username: str = Form(""),
    new_password: str = Form(...),
):
    user = _get_user_or_404(db, user_id)
    username = username.strip()

    if not user.username and not username:
        return _rerender_with_error(
            request, db, admin, "This user has no username yet — set one before granting access."
        )
    if len(new_password) < MIN_PASSWORD_LENGTH:
        return _rerender_with_error(request, db, admin, f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")

    if username:
        existing = db.query(User).filter(User.username == username, User.id != user.id).one_or_none()
        if existing is not None:
            return _rerender_with_error(request, db, admin, f'Username "{username}" is already taken.')
        user.username = username

    # Same operation whether this is the first time (granting access) or the Nth
    # (resetting a forgotten password) — either way, the admin now knows this password,
    # so the user must pick their own on next login.
    user.password_hash = hash_password(new_password)
    user.must_change_password = True
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/set-role")
def set_role(
    user_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
    role: UserRole = Form(...),
):
    user = _get_user_or_404(db, user_id)
    user.role = role
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


def _rerender_with_error(request: Request, db: Session, admin: User, error: str):
    users = db.query(User).order_by(User.name).all()
    return templates.TemplateResponse(
        request,
        "admin_users.html",
        {"users": users, "roles": list(UserRole), "error": error, "current_user": admin},
        status_code=400,
    )
