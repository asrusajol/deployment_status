"""Login, logout, and self-service password change (app/auth.py has the session/hashing
primitives this uses)."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import SESSION_USER_ID_KEY, hash_password, require_login, verify_password
from app.database import get_db
from app.models.user import User
from app.static_version import STATIC_VERSION

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["static_version"] = STATIC_VERSION

MIN_PASSWORD_LENGTH = 8


@router.get("/login")
def login_form(request: Request):
    if request.session.get(SESSION_USER_ID_KEY) is not None:
        return RedirectResponse(url="/dashboard")
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login_submit(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(...),
    password: str = Form(...),
):
    username = username.strip()
    user = db.query(User).filter(User.username == username).one_or_none()
    # Same generic error whether the username doesn't exist, has no login access yet, or
    # the password is wrong — don't help an attacker (or a confused user) narrow it down.
    if user is None or user.password_hash is None or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Invalid username or password."}, status_code=401
        )

    request.session[SESSION_USER_ID_KEY] = user.id
    destination = "/change-password" if user.must_change_password else "/dashboard"
    return RedirectResponse(url=destination, status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@router.get("/change-password")
def change_password_form(request: Request, current_user: User = Depends(require_login)):
    return templates.TemplateResponse(
        request,
        "change_password.html",
        {"error": None, "forced": current_user.must_change_password, "current_user": current_user},
    )


@router.post("/change-password")
def change_password_submit(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    def error(message: str):
        return templates.TemplateResponse(
            request,
            "change_password.html",
            {"error": message, "forced": current_user.must_change_password, "current_user": current_user},
            status_code=400,
        )

    if not current_user.password_hash or not verify_password(current_password, current_user.password_hash):
        return error("Current password is incorrect.")
    if len(new_password) < MIN_PASSWORD_LENGTH:
        return error(f"New password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if new_password != confirm_password:
        return error("New password and confirmation don't match.")

    current_user.password_hash = hash_password(new_password)
    current_user.must_change_password = False
    db.commit()
    return RedirectResponse(url="/dashboard", status_code=303)
