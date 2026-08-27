"""Operational commands for the deployment tracker. Run with `python -m app.cli <command>`."""

import argparse
import getpass

from app.auth import hash_password
from app.config import get_settings
from app.database import SessionLocal
from app.models.bitbucket_main_branch_status import BitbucketMainBranchStatus
from app.models.deployable_task import DeployableTask
from app.models.user import User, UserRole
from app.services.bitbucket_source import BitbucketCloudProvider
from app.services.reports import users_by_team
from app.services.sync import (
    sync_bitbucket_main_status,
    sync_deployable_tasks,
    sync_team_leads,
    sync_teams,
    sync_user_contacts,
    sync_users,
)
from app.services.task_source import InHouseTaskSourceProvider

MIN_PASSWORD_LENGTH = 8


def _report_sync(label: str, result) -> None:
    print(f"Synced {result.total} {label} from the CRM API ({result.created} new, {result.updated} updated).")


def cmd_sync_users(_args: argparse.Namespace) -> None:
    # Three CRM endpoints, in sequence: Machines (roster) -> general Users (contact info
    # for everyone) -> supervisor-filtered Users (team lead promotion). See app/services/sync.py.
    settings = get_settings()
    provider = InHouseTaskSourceProvider(settings)
    db = SessionLocal()
    try:
        _report_sync("users", sync_users(db, provider))

        contact_result = sync_user_contacts(db, provider)
        print(
            f"Backfilled contact info for {contact_result.matched} users from the CRM's "
            f"general Users feed ({contact_result.backfilled} had email/username updated)."
        )

        lead_result = sync_team_leads(db, provider)
        print(
            f"Matched {lead_result.matched} team leads by custom_id against the CRM's "
            f"supervisor Users feed ({lead_result.promoted} newly promoted from developer to team_lead)."
        )
    finally:
        db.close()


def cmd_sync_teams(_args: argparse.Namespace) -> None:
    settings = get_settings()
    provider = InHouseTaskSourceProvider(settings)
    db = SessionLocal()
    try:
        _report_sync("teams", sync_teams(db, provider))
    finally:
        db.close()


def cmd_users_by_team(_args: argparse.Namespace) -> None:
    db = SessionLocal()
    try:
        grouped = users_by_team(db)
        for team_name, names in grouped.items():
            print(f"\n{team_name} ({len(names)})")
            for name in names:
                print(f"  - {name}")
    finally:
        db.close()


def cmd_deployable_tasks(_args: argparse.Namespace) -> None:
    # Meant to be run frequently (e.g. every 5 minutes via cron) — see README for the
    # crontab line. Flat list of currently-PLANNED deploy operations (no readiness-gate
    # concept — see DeployableTaskInfo in task_source.py).
    settings = get_settings()
    provider = InHouseTaskSourceProvider(settings)
    db = SessionLocal()
    try:
        result = sync_deployable_tasks(db, provider)
        print(f"Synced {result.total} deploy operations ({result.created} new, {result.updated} updated).")

        planned = (
            db.query(DeployableTask)
            .filter(DeployableTask.target_status == "PLANNED")
            .order_by(DeployableTask.target, DeployableTask.due_date)
            .all()
        )
        for label, target in (("DEPLOY TO TEST", "test"), ("DEPLOY TO LIVE", "live")):
            matching = [t for t in planned if t.target == target]
            if not matching:
                continue
            print(f"\n{label} ({len(matching)})")
            for t in matching:
                dev = t.assigned_developer_name or "unassigned"
                client = t.client_name or "internal"
                # order_id shown alongside task_id since task_id (the order number) isn't
                # guaranteed unique across orders — see DeployableTask's docstring.
                print(f"  {t.task_id} (order #{t.order_id})  {t.item_name or '?'}  — {client} — assigned: {dev}")
    finally:
        db.close()


def cmd_sync_bitbucket_main(_args: argparse.Namespace) -> None:
    # Meant to be run every 5 minutes via cron, same as deployable-tasks — see
    # README's crontab section. Refreshes the single cached row deploy_request()
    # snapshots from when marking a Standard Deployment request as deployed.
    settings = get_settings()
    provider = BitbucketCloudProvider(settings)
    db = SessionLocal()
    try:
        sync_bitbucket_main_status(db, provider)
        status = db.query(BitbucketMainBranchStatus).first()
        print(f"Synced main branch status: version={status.version} pr={status.pr_number}")
    finally:
        db.close()


def cmd_create_admin(args: argparse.Namespace) -> None:
    # Bootstraps the very first admin — every other admin/password afterwards is managed
    # through the /admin/users web UI (app/routers/admin.py), which needs an admin already
    # logged in to reach it. Only ever promotes/grants access on an *existing* CRM-synced
    # user (matched by username or CRM custom_id); it never creates a new User row, since
    # this app's whole roster is meant to come from sync-users.
    db = SessionLocal()
    try:
        user = (
            db.query(User)
            .filter((User.username == args.identifier) | (User.source_system_id == args.identifier))
            .one_or_none()
        )
        if user is None:
            print(
                f"No user found with username or custom_id '{args.identifier}'. "
                "Run `sync-users` first if this is a fresh install."
            )
            return

        username = args.username or user.username
        if not username:
            print(f"{user.name} has no username yet — pass --username to set one (required to log in).")
            return
        if username != user.username:
            existing = db.query(User).filter(User.username == username, User.id != user.id).one_or_none()
            if existing is not None:
                print(f"Username '{username}' is already taken by {existing.name}.")
                return

        password = args.password or getpass.getpass(f"New password for {user.name}: ")
        if len(password) < MIN_PASSWORD_LENGTH:
            print(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
            return

        user.username = username
        user.role = UserRole.admin
        user.password_hash = hash_password(password)
        user.must_change_password = True
        db.commit()
        print(f"{user.name} (username={username}) is now an admin and can log in — they'll be asked to set their own password on first login.")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_users_parser = subparsers.add_parser(
        "sync-users", help="Pull the user/employee roster from the CRM API into the local DB"
    )
    sync_users_parser.set_defaults(func=cmd_sync_users)

    sync_teams_parser = subparsers.add_parser(
        "sync-teams", help="Pull the team/machine-group roster from the CRM API into the local DB"
    )
    sync_teams_parser.set_defaults(func=cmd_sync_teams)

    users_by_team_parser = subparsers.add_parser(
        "users-by-team", help="Print all local users grouped by their team"
    )
    users_by_team_parser.set_defaults(func=cmd_users_by_team)

    deployable_tasks_parser = subparsers.add_parser(
        "deployable-tasks",
        help="Sync deploy-test/deploy-live operations from the CRM and report what's ready to request",
    )
    deployable_tasks_parser.set_defaults(func=cmd_deployable_tasks)

    sync_bitbucket_main_parser = subparsers.add_parser(
        "sync-bitbucket-main",
        help="Refresh the cached shopfloor-suite main-branch release version + latest merged PR",
    )
    sync_bitbucket_main_parser.set_defaults(func=cmd_sync_bitbucket_main)

    create_admin_parser = subparsers.add_parser(
        "create-admin",
        help="Bootstrap the first admin by granting login access to an existing CRM-synced user",
    )
    create_admin_parser.add_argument("identifier", help="Existing user's username or CRM custom_id")
    create_admin_parser.add_argument("--username", help="Set/override the username (required if not already set)")
    create_admin_parser.add_argument("--password", help="New password (omit to be prompted securely)")
    create_admin_parser.set_defaults(func=cmd_create_admin)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
