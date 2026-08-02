from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.auth import NotAuthenticatedError
from app.config import get_settings
from app.database import get_db
from app.routers.admin import router as admin_router
from app.routers.auth import router as auth_router
from app.routers.dashboard import router as dashboard_router

settings = get_settings()
app = FastAPI(title=settings.app_name)
# session_cookie/https_only left at their Starlette defaults for now — this is an
# internal tool served over plain HTTP in dev; set https_only=True once it's behind TLS.
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret_key)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(dashboard_router)


@app.exception_handler(NotAuthenticatedError)
def not_authenticated_handler(_request: Request, exc: NotAuthenticatedError) -> RedirectResponse:
    # require_login() (app/auth.py) raises this instead of an HTTPException specifically
    # so a protected page bounces to /login (or /change-password) rather than showing a
    # bare 401 — there's no JSON API here for a 401 status code to be useful to.
    return RedirectResponse(url=exc.redirect_to, status_code=303)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name}


@app.get("/health/db")
def health_db(db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "reachable"}
