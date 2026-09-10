from fastapi import Depends, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.auth import SESSION_USER_ID_KEY, NotAuthenticatedError
from app.config import get_settings
from app.database import get_db
from app.routers.admin import router as admin_router
from app.routers.auth import router as auth_router
from app.routers.clients import router as clients_router
from app.routers.dashboard import router as dashboard_router
from app.routers.release_tracker import router as release_tracker_router
from app.ws import manager

settings = get_settings()
app = FastAPI(title=settings.app_name)
# https_only driven by SESSION_COOKIE_HTTPS_ONLY (app/config.py) — off by default for
# local dev / any deployment not yet behind TLS, flip on once nginx is terminating HTTPS
# in front of this app (see README's "Production deployment").
app.add_middleware(
    SessionMiddleware, secret_key=settings.session_secret_key, https_only=settings.session_cookie_https_only
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(dashboard_router)
app.include_router(release_tracker_router)
app.include_router(clients_router)


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


@app.websocket("/ws/requests")
async def requests_ws(websocket: WebSocket) -> None:
    """Live-update ping for /requests (request_list.html) — see app/ws.py. Not a data
    channel: the only thing ever sent over it is a bare "changed" string; the client
    just reloads the page on receipt, so there's nothing here worth encrypting beyond
    whatever TLS the reverse proxy already terminates (README's "Production deployment").
    """
    # A lightweight session check, not the full require_login() — a WebSocket can't be
    # redirected to /login the way a normal request is, and the only consequence of
    # letting a stale/borderline session through here is one extra "reload the page"
    # ping; the reload itself still goes through full require_login() regardless.
    if websocket.session.get(SESSION_USER_ID_KEY) is None:
        await websocket.close(code=1008)
        return

    await manager.connect(websocket)
    try:
        while True:
            # Nothing meaningful ever arrives from the client — this just blocks until
            # the browser closes the tab/connection, which raises WebSocketDisconnect.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)
