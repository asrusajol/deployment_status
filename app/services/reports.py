"""Read-only queries for reporting (project_plan.md, Section 8)."""

from collections import defaultdict

from sqlalchemy.orm import Session

from app.models.user import User

UNASSIGNED_LABEL = "Unassigned / unknown team"


def users_by_team(db: Session) -> dict[str, list[str]]:
    """Group all users by their team name, alphabetically, with unassigned last.

    A user falls under UNASSIGNED_LABEL if machine_group_id is null, or if it doesn't
    resolve to a known Team — that's expected to happen occasionally since the link
    isn't a hard foreign key (see the comment on User.machine_group_id) — rather than
    being silently dropped from the report.
    """
    grouped: dict[str, list[str]] = defaultdict(list)
    for user in db.query(User).order_by(User.name).all():
        team_name = user.team.name if user.team is not None else UNASSIGNED_LABEL
        grouped[team_name].append(user.name)
    return dict(sorted(grouped.items(), key=lambda kv: (kv[0] == UNASSIGNED_LABEL, kv[0])))
