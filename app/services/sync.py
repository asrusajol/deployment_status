"""Sync local mirror tables from the in-house CRM API (project_plan.md, Section 6).

Run on-demand today via `python -m app.cli sync-users` / `sync-teams` — `sync-users`
chains sync_users() -> sync_user_contacts() -> sync_team_leads() to fully populate the
User table from all three CRM endpoints (Machines, general Users, supervisor Users) in
one command. Wiring this into an APScheduler daily job is still open — project_plan.md,
Section 11 (Phase 3).
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.bitbucket_main_branch_status import BitbucketMainBranchStatus
from app.models.deployable_task import DeployableTask
from app.models.team import Team
from app.models.user import User, UserRole
from app.services.task_source import TaskSourceProvider


@dataclass
class SyncResult:
    created: int
    updated: int

    @property
    def total(self) -> int:
        return self.created + self.updated


def sync_users(db: Session, provider: TaskSourceProvider) -> SyncResult:
    """Upsert every active user/employee from the CRM API into the local User table.

    Matches on source_system_id (the CRM's custom_id) rather than name or email —
    names aren't guaranteed unique, and email isn't in this feed at all (Section 6).
    New local users default to the `developer` role; this function never changes an
    existing user's role — see sync_team_leads() below for the one thing that does.
    """
    synced_at = datetime.now(timezone.utc)
    created = updated = 0

    for info in provider.list_users():
        user = db.query(User).filter(User.source_system_id == info.source_system_id).one_or_none()
        if user is None:
            user = User(source_system_id=info.source_system_id, role=UserRole.developer)
            db.add(user)
            created += 1
        else:
            updated += 1

        user.name = info.name
        user.machine_group_id = info.machine_group_id
        user.last_synced_at = synced_at
        if info.email:
            user.email = info.email

    db.commit()
    return SyncResult(created=created, updated=updated)


def sync_teams(db: Session, provider: TaskSourceProvider) -> SyncResult:
    """Upsert every team/machine-group from the CRM API into the local Team table.

    Matches on `id` directly (the CRM's own MachineGroups.id) rather than a separate
    source_system_id lookup, since Team.id *is* that id by design — see app/models/team.py.
    """
    synced_at = datetime.now(timezone.utc)
    created = updated = 0

    for info in provider.list_teams():
        team = db.get(Team, info.id)
        if team is None:
            team = Team(id=info.id)
            db.add(team)
            created += 1
        else:
            updated += 1

        team.source_system_id = info.source_system_id
        team.name = info.name
        team.last_synced_at = synced_at

    db.commit()
    return SyncResult(created=created, updated=updated)


@dataclass
class TeamLeadSyncResult:
    matched: int
    promoted: int


def sync_team_leads(db: Session, provider: TaskSourceProvider) -> TeamLeadSyncResult:
    """Promote users in the CRM's "Team Leads" userGroup to `team_lead`, and record each
    one as their own team's leader.

    Matches by source_system_id (custom_id) against users already synced from Machines
    via sync_users() — run this after sync_users(), not standalone: a team lead with no
    matching Machine-derived user is intentionally skipped rather than creating a new
    user from this feed alone (the CRM's Users feed includes non-factory staff who were
    never part of the Machines roster in the first place, e.g. admins, sales).

    Only ever promotes `developer` -> `team_lead`, never demotes — same "don't overwrite
    a role downward" rule as sync_users(), whether the existing role came from a manual
    assignment or an earlier run of this same function. Also backfills email/username,
    since the Machines feed doesn't carry either.

    Also sets Team.leader_user_id for the team the lead's own machine_group_id (already
    populated by sync_users(), from the same Machines record) resolves to — run this after
    sync_teams() too, or the team may not exist locally yet to attach a leader to (skipped
    rather than created, same reasoning as an unmatched user above).
    """
    leads_by_custom_id = {lead.source_system_id: lead for lead in provider.list_team_leads()}
    if not leads_by_custom_id:
        return TeamLeadSyncResult(matched=0, promoted=0)

    matched_users = db.query(User).filter(User.source_system_id.in_(leads_by_custom_id.keys())).all()
    promoted = 0
    for user in matched_users:
        lead = leads_by_custom_id[user.source_system_id]
        if lead.email:
            user.email = lead.email
        if lead.username:
            user.username = lead.username
        if user.role == UserRole.developer:
            user.role = UserRole.team_lead
            promoted += 1
        if user.machine_group_id is not None:
            team = db.get(Team, user.machine_group_id)
            if team is not None:
                team.leader_user_id = user.id

    db.commit()
    return TeamLeadSyncResult(matched=len(matched_users), promoted=promoted)


@dataclass
class ContactSyncResult:
    matched: int
    backfilled: int


def sync_user_contacts(db: Session, provider: TaskSourceProvider) -> ContactSyncResult:
    """Backfill email/username for every active user from the CRM's general Users feed.

    Unlike sync_team_leads(), this covers everyone — developers, QA, DevOps, team leads
    alike — not just supervisors, since Machines carries neither field for anyone.
    Never touches role; that's exclusively sync_team_leads()'s job. Same skip-if-unmatched
    reasoning as sync_team_leads() applies here too.
    """
    contacts_by_custom_id = {c.source_system_id: c for c in provider.list_user_contacts()}
    if not contacts_by_custom_id:
        return ContactSyncResult(matched=0, backfilled=0)

    matched_users = db.query(User).filter(User.source_system_id.in_(contacts_by_custom_id.keys())).all()
    backfilled = 0
    for user in matched_users:
        contact = contacts_by_custom_id[user.source_system_id]
        changed = False
        if contact.email and user.email != contact.email:
            user.email = contact.email
            changed = True
        if contact.username and user.username != contact.username:
            user.username = contact.username
            changed = True
        if changed:
            backfilled += 1

    db.commit()
    return ContactSyncResult(matched=len(matched_users), backfilled=backfilled)


def _parse_due_date(value: str | None):
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


@dataclass
class DeployableTaskSyncResult:
    created: int
    updated: int

    @property
    def total(self) -> int:
        return self.created + self.updated


def sync_deployable_tasks(db: Session, provider: TaskSourceProvider) -> DeployableTaskSyncResult:
    """Upsert currently-PLANNED "Deployment Test system"/"Deployment Live System" operations,
    keyed by the CRM's own operation id.

    Sourced from /get-orders, a flat list with no gate/readiness concept (see
    DeployableTaskInfo in task_source.py) — this just mirrors what's currently PLANNED,
    matching the established never-delete upsert pattern used by the other sync_*()
    functions (project_plan.md, Section 12).
    """
    synced_at = datetime.now(timezone.utc)
    created = updated = 0

    for info in provider.list_deployable_tasks():
        task = db.get(DeployableTask, info.operation_id)
        if task is None:
            task = DeployableTask(id=info.operation_id)
            db.add(task)
            created += 1
        else:
            updated += 1

        task.order_id = info.order_id
        task.task_id = info.task_id
        task.order_name = info.order_name
        task.client_name = info.client_name
        task.item_custom_id = info.item_custom_id
        task.item_name = info.item_name
        task.pos_id = info.pos_id
        task.target = info.target
        task.target_status = info.target_status
        task.assigned_developer_custom_id = info.assigned_developer_custom_id
        task.assigned_developer_name = info.assigned_developer_name
        task.due_date = _parse_due_date(info.due_date)
        task.last_synced_at = synced_at

    db.commit()
    return DeployableTaskSyncResult(created=created, updated=updated)


def sync_bitbucket_main_status(db: Session, provider) -> None:
    """Upserts the single BitbucketMainBranchStatus row (id=1) — a cache, not
    a history table. version_changed_at only advances when the fetched
    version actually differs from what's stored; last_synced_at bumps every
    time regardless (ops/liveness diagnostic only). See
    docs/superpowers/specs/2026-08-27-release-tracker-redesign.md.
    """
    status_info = provider.get_main_branch_status()
    now = datetime.now(timezone.utc)

    row = db.get(BitbucketMainBranchStatus, 1)
    if row is None:
        row = BitbucketMainBranchStatus(id=1)
        db.add(row)
        version_changed = status_info.version is not None
    else:
        # Only a real, non-null new version counts as "changed" — a fetch
        # that comes back None (malformed/renamed release.json response)
        # should not stamp version_changed_at fresh alongside a NULL
        # version, which would misleadingly look like "main just changed to
        # nothing" rather than "we failed to fetch main's version this
        # cycle".
        version_changed = status_info.version is not None and status_info.version != row.version

    row.version = status_info.version
    row.pr_number = status_info.pr_number
    row.last_synced_at = now
    if version_changed:
        row.version_changed_at = now
    db.commit()
