import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# Statuses considered "active" for the duplicate-submission guard in project_plan.md Section 7.
# Checked in the service layer (not a DB constraint), since a hard unique constraint on
# (task_id, version) would also block a legitimate re-request after one has already
# completed, failed, or been rolled back.
ACTIVE_REQUEST_STATUSES = ("submitted", "pending_approval", "approved", "claimed", "in_progress")


class RequestStatus(str, enum.Enum):
    pending_intake = "pending_intake"  # matches the deployment-request-intake skill's stopgap output
    submitted = "submitted"
    pending_approval = "pending_approval"
    approved = "approved"
    rejected = "rejected"
    claimed = "claimed"
    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"
    rolled_back = "rolled_back"


# Statuses a request can still be deleted from (can_delete_request() in app/auth.py) —
# anything up to and including a decision (approved/rejected), but not once execution has
# actually started. `claimed`/`in_progress` mean a DeploymentExecution row already exists
# for this request (require_deploy_team_member()'s start/deploy routes create one), and
# `completed`/`failed`/`rolled_back` are historical record at that point — deleting any of
# those would silently break the audit trail this whole tool exists for, so they're
# excluded on purpose, not just left off by omission.
DELETABLE_REQUEST_STATUSES = (
    RequestStatus.pending_intake,
    RequestStatus.submitted,
    RequestStatus.pending_approval,
    RequestStatus.approved,
    RequestStatus.rejected,
)


# The web UI's four-stage flow (Submit -> Pending Team Lead Approval -> Pending Deployment
# -> Deployed) maps directly onto four of the RequestStatus values above — a request
# created via the form starts at `pending_approval` (skipping the bare `submitted` state,
# which exists only for the older intake-skill stopgap format), moves to `approved` once a
# team lead signs off, and to `completed` once DevOps marks it deployed. `rejected` is the
# other off-ramp from `pending_approval`. See app/routers/dashboard.py.
class DeploymentEnvironment(str, enum.Enum):
    test = "test"
    live = "live"


class RequestType(str, enum.Enum):
    # The original CRM-task-driven flow: Task ID + client + environment + git
    # branch/commit/version, gated on team-lead approval.
    standard = "standard"
    # No approval required (see project_plan.md's approval gate, Section 3) — the
    # requester just needs a dump pulled and either restored somewhere else or handed
    # back to them; still goes through the same Approved -> deploy-team-marks-Completed
    # steps as everything else, just skipping the pending_approval stage.
    db_dump_restore = "db_dump_restore"
    # Also no approval required — deploying a branch to one of the internal *.test.local
    # boxes (crm.test.local, tmp.test.local, vop.test.local, ...) rather than a real
    # client system, so it doesn't need a team lead's sign-off either.
    test_local = "test_local"


class DeploymentRequest(Base):
    __tablename__ = "deployment_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_type: Mapped[RequestType] = mapped_column(
        Enum(RequestType), nullable=False, default=RequestType.standard
    )
    task_id: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    # `DeployableTask.item_name` (e.g. "Interface"), copied at request-creation time —
    # same reasoning as task_id: the source DeployableTask row can be re-synced/removed
    # later, so this is a snapshot, not a live lookup. Comma-joined the same way task_id
    # is when a request combines multiple orders (create_request() in
    # app/routers/dashboard.py). Only ever set for `standard` requests — db_dump_restore
    # and test_local aren't sourced from deployable_tasks at all.
    module_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"), nullable=True)
    # Freeform: the older intake skill fills this with things like "CRM Live"; for a
    # `test_local` request it holds the target host (e.g. "crm.test.local") instead.
    server: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Which system this deploys to — distinct from the freeform `server` field above.
    # This is the clean, dropdown-driven test/live split the dashboard groups current
    # status by; only ever set for `standard` requests — `test_local` requests carry
    # their target in `server` instead, since a *.test.local host isn't a client's
    # test/live system. Nullable at the DB level for migration safety on any
    # pre-existing rows; the standard web form always requires it.
    environment: Mapped[DeploymentEnvironment | None] = mapped_column(Enum(DeploymentEnvironment), nullable=True)
    git_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    commit_hash: Mapped[str | None] = mapped_column(String(100), nullable=True)
    version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    changes_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # `db_dump_restore`-only fields. Exactly one of restore_source / share_with_requestor
    # is set (enforced in the router, not the DB) — either the dump gets restored
    # somewhere else, or it's just handed back to the requester.
    dump_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    restore_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    share_with_requestor: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[RequestStatus] = mapped_column(Enum(RequestStatus), default=RequestStatus.pending_intake)
    created_at: Mapped[datetime] = mapped_column(DateTime)

    client = relationship("Client")
    requester = relationship("User", foreign_keys=[requested_by])
    approvals = relationship("Approval", back_populates="request")
    executions = relationship("DeploymentExecution", back_populates="request")
