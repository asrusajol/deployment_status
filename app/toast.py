"""The design's toast — a short confirmation after an action ("Request #12 approved").

Actions here post a form and redirect, so the message waits in the session and the
next page shows it once (base.html and login.html pop it). Call toast() only after the
change has been committed: a refused or failed action must never confirm itself.
"""

from starlette.requests import Request

_KEY = "toasts"


def toast(request: Request, message: str) -> None:
    request.session[_KEY] = [*request.session.get(_KEY, []), message]


def toast_now(request: Request, message: str) -> None:
    """For the page being rendered by this same request (no redirect in between) — e.g.
    the theme switch, which theme_middleware handles outside the session."""
    request.state.toasts = [*getattr(request.state, "toasts", []), message]


def take(request: Request) -> list[str]:
    """The waiting messages, removed so they show once. The page renders before the
    session cookie is written, so taking them in the template sticks."""
    waiting = request.session.pop(_KEY, []) if "session" in request.scope else []
    return [*waiting, *getattr(request.state, "toasts", [])]
