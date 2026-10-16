"""The Dashboard overview (the design's Dashboard view): what each card and tile shows.

Statuses are grouped the way the Requests status tags are (dashboard.STATUS_TAGS): the
design has four — Pending, In Progress, Deployed, Withdraw (our Returned) — and ours
adds Stopped for the outcomes it has no tag for (rejected, failed, rolled back,
withdrawn), so every request counts somewhere.

Times are left as datetimes: the page renders them in the viewer's own zone, and the
activity chart buckets its days in the browser for the same reason — a deployment at
01:00 in Dhaka is still yesterday in UTC.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session, joinedload, selectinload

from app.auth import can_access_management
from app.models.checklist import ChecklistItem
from app.models.client import Client
from app.models.client_version_status import ClientVersionStatus
from app.models.deployment_execution import DeploymentExecution
from app.models.deployment_request import DeploymentEnvironment, DeploymentRequest, RequestStatus, RequestType
from app.models.seeder_command import SeederCommand
from app.models.user import User, UserRole

GROUPS = {
    "pending": (RequestStatus.pending_intake, RequestStatus.submitted, RequestStatus.pending_approval, RequestStatus.approved),
    "progress": (RequestStatus.claimed, RequestStatus.in_progress),
    "deployed": (RequestStatus.completed,),
    "returned": (RequestStatus.returned,),
    "stopped": (RequestStatus.rejected, RequestStatus.failed, RequestStatus.rolled_back, RequestStatus.withdrawn),
}
GROUP_LABELS = {
    "pending": "Pending", "progress": "In progress", "deployed": "Deployed",
    "returned": "Returned", "stopped": "Stopped",
}
_GROUP_OF = {status: group for group, statuses in GROUPS.items() for status in statuses}
# Needs attention: what's still waiting on someone, by stage, oldest first.
_ATTENTION = ("pending", "progress", "returned")
ACTIVITY_DAYS = 14


@dataclass
class HealthRow:
    key: str
    ok: bool
    label: str
    detail: str
    href: str | None


def group_of(status: RequestStatus) -> str:
    return _GROUP_OF.get(status, "stopped")


def pct(part: int, whole: int) -> int:
    return round(part * 100 / whole) if whole else 0


def ago(value: datetime | None, now: datetime) -> str:
    """The design's relative time: "just now", "12m ago", "3h ago", "5d ago"."""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    seconds = (now - value).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    days = int(seconds // 86400)
    return f"{days}d ago" if days < 30 else value.strftime("%d %b")


def build(db: Session, user: User, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    requests = (
        db.query(DeploymentRequest)
        .options(
            joinedload(DeploymentRequest.client),
            joinedload(DeploymentRequest.requester),
            joinedload(DeploymentRequest.executions).joinedload(DeploymentExecution.executor),
        )
        .all()
    )
    by_group = {g: [r for r in requests if group_of(r.status) == g] for g in GROUPS}
    total = len(requests)
    open_count = len(by_group["pending"]) + len(by_group["progress"])

    def created(r):
        return r.created_at.replace(tzinfo=timezone.utc) if r.created_at and r.created_at.tzinfo is None else r.created_at

    attention = sorted(
        (r for g in _ATTENTION for r in by_group[g]),
        key=lambda r: (_ATTENTION.index(group_of(r.status)), created(r) or now),
    )
    oldest_pending = min((created(r) for r in by_group["pending"] if r.created_at), default=None)

    # Deployments: completed requests with their execution's completion time.
    deployed = sorted(
        (r for r in by_group["deployed"] if r.finished_at),
        key=lambda r: r.finished_at, reverse=True,
    )

    def finished(r):
        f = r.finished_at
        return f.replace(tzinfo=timezone.utc) if f.tzinfo is None else f

    week = [r for r in deployed if now - finished(r) < timedelta(days=7)]
    window = [r for r in deployed if now - finished(r) < timedelta(days=ACTIVITY_DAYS + 1)]
    deployers = Counter(r.current_executor.name for r in deployed if r.current_executor)

    types = [
        {
            "type": t,
            "n": sum(1 for r in requests if r.request_type == t),
            "open": sum(1 for r in requests if r.request_type == t and group_of(r.status) in ("pending", "progress")),
        }
        for t in RequestType
    ]
    systems = {
        env.value: sum(1 for r in requests if r.environment == env) for env in DeploymentEnvironment
    }
    live_open = sum(1 for r in requests if r.environment == DeploymentEnvironment.live and group_of(r.status) in ("pending", "progress"))

    clients = db.query(Client).options(selectinload(Client.system_urls)).all()
    active_clients = [c for c in clients if c.is_active]

    def has_both_urls(c):
        envs = {u.environment for u in c.system_urls}
        return DeploymentEnvironment.test in envs and DeploymentEnvironment.live in envs

    missing_urls = [c for c in active_clients if not has_both_urls(c)]

    names = {c.id: c.name for c in clients}
    releases = db.query(ClientVersionStatus).all()
    drift = sorted(
        (
            {"client": names.get(r.client_id, "—"), "test": r.test_current_version, "live": r.live_current_version}
            for r in releases
            if not r.test_current_version or not r.live_current_version or r.test_current_version != r.live_current_version
        ),
        key=lambda d: d["client"].lower(),
    )

    items = db.query(ChecklistItem).options(selectinload(ChecklistItem.types)).filter(ChecklistItem.is_active.is_(True)).all()
    per_type = {t: sum(1 for i in items if any(a.request_type == t for a in i.types)) for t in RequestType}
    users = db.query(User).all()
    with_access = [u for u in users if u.password_hash]
    roles = Counter(u.role for u in with_access)
    seeders = db.query(SeederCommand).count()

    is_admin = user.role == UserRole.admin
    may_seed = user.role in (UserRole.admin, UserRole.devops)
    health = [
        HealthRow(
            "clients", not missing_urls, "Client URLs",
            f"{len(missing_urls)} active client{'s' if len(missing_urls) != 1 else ''} without Test/Live URL"
            if missing_urls else "All active clients have Test and Live URLs",
            "/clients",
        ),
        HealthRow(
            "checklist", all(per_type.values()), "Management checklist",
            f"{len(items)} active items · Standard {per_type[RequestType.standard]} · "
            f"DB {per_type[RequestType.db_dump_restore]} · Test.local {per_type[RequestType.test_local]}",
            "/management/checklists" if can_access_management(user) else None,
        ),
        HealthRow(
            "users", bool(with_access), "Users",
            f"{len(with_access)} with login access of {len(users)}"
            + (" · " + ", ".join(f"{_role_label(r)} {n}" for r, n in sorted(roles.items(), key=lambda kv: kv[0].value)) if roles else ""),
            "/admin/users" if is_admin else None,
        ),
        HealthRow(
            "seeders", seeders > 0, "Seeder collection",
            f"{seeders} seeder{'s' if seeders != 1 else ''} stored",
            "/seeder-collection" if may_seed else None,
        ),
    ]

    return {
        "now": now,
        "first_name": (user.name or "").split(" ")[0],
        "total": total,
        "open_count": open_count,
        "by_group": by_group,
        "group_labels": GROUP_LABELS,
        "oldest_pending": oldest_pending,
        "attention": attention,
        "deployed": deployed,
        "deployed_this_week": len(week),
        "activity": {
            "days": ACTIVITY_DAYS,
            "deployments": [
                {"at": finished(r).strftime("%Y-%m-%dT%H:%M:%SZ"), "system": r.environment.value if r.environment else "test"}
                for r in window
            ],
            "topDeployers": [[name, n] for name, n in deployers.most_common(3)],
        },
        "types": types,
        "systems": systems,
        "live_open": live_open,
        "clients_total": len(clients),
        "clients_active": len(active_clients),
        "clients_missing_urls": len(missing_urls),
        "releases": releases,
        "aligned": len(releases) - len(drift),
        "drift": drift,
        "health": health,
    }


def _role_label(role: UserRole) -> str:
    return {"team_lead": "team lead", "devops": "devops"}.get(role.value, role.value)
