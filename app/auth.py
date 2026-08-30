"""Session-cookie login: password hashing, current-user resolution, and role guards.

Deliberately simple for an internal tool — no OAuth/SSO, no JWT, no "remember me": a
signed session cookie (Starlette's SessionMiddleware, wired in app/main.py) holding just
`user_id`. Login access itself is opt-in per user, granted by an admin (app/routers/admin.py)
or bootstrapped via `python -m app.cli create-admin` — most CRM-synced employees have no
password set and simply can't log in.
"""

import bcrypt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models.deployment_request import DELETABLE_REQUEST_STATUSES, EDITABLE_REQUEST_STATUSES, RequestType
from app.models.user import User, UserRole

SESSION_USER_ID_KEY = "user_id"

# Paths a logged-in-but-must-change-password user is still allowed to hit, so the forced
# redirect in require_login() below doesn't loop them out of the one place they can
# actually fix it (or log out and try a different account).
PASSWORD_CHANGE_EXEMPT_PATHS = {"/change-password", "/logout"}


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


class NotAuthenticatedError(Exception):
    """Raised by require_login() when there's no valid session, or the user must change
    their password first — caught by the exception handler registered in app/main.py,
    which redirects to `redirect_to` (a raised HTTPException can't itself carry a
    redirect, so a plain exception + handler is how this app does it)."""

    def __init__(self, redirect_to: str = "/login"):
        self.redirect_to = redirect_to


def require_login(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get(SESSION_USER_ID_KEY)
    if user_id is None:
        raise NotAuthenticatedError("/login")

    user = db.get(User, user_id)
    if user is None or user.password_hash is None:
        # Account was deleted, or login access was revoked, since the cookie was issued.
        request.session.clear()
        raise NotAuthenticatedError("/login")

    if user.must_change_password and request.url.path not in PASSWORD_CHANGE_EXEMPT_PATHS:
        raise NotAuthenticatedError("/change-password")

    return user


def require_admin(current_user: User = Depends(require_login)) -> User:
    if current_user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


def require_devops(current_user: User = Depends(require_login)) -> User:
    """Gate for the Seeder Collection tab (app/routers/seeder_collection.py):
    admin or devops only — everyone else gets 403, and the nav link is
    hidden for them too (base.html)."""
    if current_user.role not in (UserRole.admin, UserRole.devops):
        raise HTTPException(status_code=403, detail="Devops access required")
    return current_user


def _is_deploy_team_member(user: User, settings: Settings) -> bool:
    # The whole app is already scoped to this one team's deploy tasks (deployable-tasks
    # only ever pulls hall/machine-group task_api_deployable_hall_id /
    # task_api_deployable_machine_group_id — see task_source.py), so "belongs to the
    # team" is exactly this same machine_group_id, reused rather than re-hardcoded.
    return user.machine_group_id == settings.task_api_deployable_machine_group_id


def can_approve_deployment_request(current_user: User, deployment_request) -> bool:
    """Whether current_user may approve/reject this specific request: an admin, or a
    team_lead who belongs to the *requester's own team* — not the deploy team
    (MG-00013/"Team Rajib", see require_deploy_team_member below). Those are two
    different axes on purpose: Team Rajib is who actually executes every deployment
    regardless of who requested it, but each team's own lead is who signs off on their
    own team's requests — a lead from an unrelated team has no business approving here.

    Deliberately checked by role + shared machine_group_id, not by looking up
    Team.leader_user_id: that field is only ever backfilled by sync_team_leads() at sync
    time, and is left unset if that sync ran before the team itself existed locally (see
    sync_team_leads()'s docstring) — a real gap, not hypothetical, confirmed by finding a
    team lead correctly promoted to `team_lead` whose own Team.leader_user_id was still
    NULL. Matching on machine_group_id directly is self-healing across re-syncs instead
    of depending on that one field having been populated in the right order.
    """
    if current_user.role == UserRole.admin:
        return True
    if current_user.role != UserRole.team_lead:
        return False
    requester = deployment_request.requester
    if requester is None or requester.machine_group_id is None:
        return False
    return current_user.machine_group_id == requester.machine_group_id


def can_delete_request(current_user: User, deployment_request) -> bool:
    """Whether current_user may delete this specific request: an admin, or the original
    requester — and only while it's still in DELETABLE_REQUEST_STATUSES (app/models/
    deployment_request.py). Once a deploy team member has actually started executing it
    (in_progress) or it's reached a terminal state (completed/failed/rolled_back), it's
    execution history, not a mistake to undo — deleting it would silently break the
    audit trail this tool exists for, so neither an admin nor the requester can at that
    point (there's no override; if a real correction is needed once execution has
    started, that's an operational conversation, not a delete button)."""
    if deployment_request.status not in DELETABLE_REQUEST_STATUSES:
        return False
    if current_user.role == UserRole.admin:
        return True
    return current_user.id == deployment_request.requested_by


def can_edit_request(current_user: User, deployment_request) -> bool:
    """Whether current_user may edit this specific request: an admin, or the original
    requester — and only while it's a `standard` request still in
    EDITABLE_REQUEST_STATUSES (app/models/deployment_request.py). Once a team lead has
    actually decided on it (approved or rejected) it's a recorded decision, not a draft —
    neither the requester nor an admin can edit it at that point, the same "no override"
    stance can_delete_request takes once execution has started. db_dump_restore/test_local
    requests are never editable regardless of status: they're created straight into
    `approved` and have no real pre-decision window."""
    if deployment_request.request_type != RequestType.standard:
        return False
    if deployment_request.status not in EDITABLE_REQUEST_STATUSES:
        return False
    if current_user.role == UserRole.admin:
        return True
    return current_user.id == deployment_request.requested_by


def can_edit_client_version_status(current_user: User, row, environment) -> bool:
    """Whether current_user may correct row's `{environment}_current_version`
    — checked PER COLUMN, not per row: a user who only ever confirmed this
    client's Test deploy can fix Test but not Live on the same row, and vice
    versa. Admins and devops can edit either, on any row."""
    if current_user.role in (UserRole.admin, UserRole.devops):
        return True
    recorded_by = getattr(row, f"{environment.value}_recorded_by")
    return current_user.id == recorded_by


def require_deploy_team_member(
    current_user: User = Depends(require_login), settings: Settings = Depends(get_settings)
) -> User:
    """Only an admin, or a member of the deploy team (MG-00013 / "Team Rajib" today, via
    `task_api_deployable_machine_group_id`), may mark a request deployed."""
    if current_user.role == UserRole.admin or _is_deploy_team_member(current_user, settings):
        return current_user
    raise HTTPException(status_code=403, detail="Only a member of the deploy team (or an admin) can deploy")
