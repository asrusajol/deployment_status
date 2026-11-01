"""Search, column sort and column filters for the Requests list (the design's Search box
and column header menus), and the rows of its CSV export.

All of it runs in SQL before the page is cut, because the list is paginated: filtering
or sorting only the rows on screen would silently miss every other page.

Columns are keyed as the Requests table's script keys them (its header text, slugged),
so a key from the page — `sort=status`, `f-client=…`, `cols=task-id,…` — means the same
column everywhere.
"""

import csv
import io
from dataclasses import dataclass, field

from sqlalchemy import String, case, cast, false, or_
from sqlalchemy.orm import Query, aliased

from app.models.client import Client
from app.models.deployment_execution import DeploymentExecution
from app.models.deployment_request import DeploymentRequest, RequestType
from app.models.user import User

# Label, in table order. Action isn't a data column and has no key here.
COLUMNS = {
    "type": "Type",
    "task-id": "Task ID",
    "module-name": "Module Name",
    "client": "Client",
    "system": "System",
    "url": "URL",
    "version": "Version",
    "branch-commit": "Branch / Commit",
    "changes": "Changes",
    "requested-by": "Requested By",
    "requested-at": "Requested At",
    "status": "Status",
    "deployed-at": "Deployed At",
}

# Columns a free-text search or "contains" filter can't sensibly match (timestamps).
_NOT_TEXT = {"requested-at", "deployed-at"}


@dataclass
class ListQuery:
    q: str = ""
    sort: str | None = None
    desc: bool = False
    filters: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_params(cls, params) -> "ListQuery":
        sort = params.get("sort")
        filters = {
            name[2:]: value.strip()
            for name, value in params.items()
            if name.startswith("f-") and name[2:] in COLUMNS and name[2:] not in _NOT_TEXT and value.strip()
        }
        return cls(
            q=(params.get("q") or "").strip(),
            sort=sort if sort in COLUMNS else None,
            desc=params.get("dir") == "desc",
            filters=filters,
        )

    def params(self) -> dict[str, str]:
        """The query-string pairs that reproduce this view — carried by pagination,
        the page-size form and the CSV link."""
        out = {}
        if self.q:
            out["q"] = self.q
        if self.sort:
            out["sort"] = self.sort
            out["dir"] = "desc" if self.desc else "asc"
        for key, value in self.filters.items():
            out[f"f-{key}"] = value
        return out


class _Joins:
    """The outer joins a column needs, made once per query."""

    def __init__(self, query: Query):
        self.client = aliased(Client)
        self.requester = aliased(User)
        self.execution = aliased(DeploymentExecution)
        self.query = (
            query.outerjoin(self.client, DeploymentRequest.client_id == self.client.id)
            .outerjoin(self.requester, DeploymentRequest.requested_by == self.requester.id)
            # At most one execution per request (unique request_id), so no duplicate rows.
            .outerjoin(self.execution, self.execution.request_id == DeploymentRequest.id)
        )

    def sort_expr(self, key: str):
        return {
            "type": DeploymentRequest.request_type,
            "task-id": DeploymentRequest.task_id,
            "module-name": DeploymentRequest.module_name,
            "client": self.client.name,
            "system": DeploymentRequest.environment,
            "url": DeploymentRequest.server,
            "version": DeploymentRequest.version,
            "branch-commit": DeploymentRequest.git_branch,
            "changes": DeploymentRequest.changes_description,
            "requested-by": self.requester.name,
            "requested-at": DeploymentRequest.created_at,
            "status": DeploymentRequest.status,
            "deployed-at": self.execution.completed_at,
        }[key]

    def matches(self, key: str, needle: str, labels: dict):
        """Rows whose `key` column, as the page shows it, contains `needle` (any case)."""
        like = f"%{_escape(needle.lower())}%"

        def text(column):
            return column.ilike(like, escape="\\")

        if key == "type":
            return _by_label(DeploymentRequest.request_type, labels["type"], needle)
        if key == "status":
            return _by_label(DeploymentRequest.status, labels["status"], needle)
        if key == "system":
            # Test / Live, or a test.local row's hostname, which is what its badge shows.
            return or_(
                text(cast(DeploymentRequest.environment, String)),
                (DeploymentRequest.request_type == RequestType.test_local) & text(DeploymentRequest.server),
            )
        if key == "branch-commit":
            # A dump & restore row shows its dump → restore source in this column.
            return or_(
                text(DeploymentRequest.git_branch), text(DeploymentRequest.commit_hash),
                text(DeploymentRequest.dump_source), text(DeploymentRequest.restore_source),
            )
        column = {
            "task-id": DeploymentRequest.task_id,
            "module-name": DeploymentRequest.module_name,
            "client": self.client.name,
            "url": DeploymentRequest.server,
            "version": DeploymentRequest.version,
            "changes": DeploymentRequest.changes_description,
            "requested-by": self.requester.name,
        }[key]
        return text(column)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _by_label(column, labels: dict, needle: str):
    """Enum columns match on the label the page shows ("Deployed"), not the stored
    value ("completed") — that's what the user typed."""
    hits = [value for value, label in labels.items() if needle.lower() in label.lower()]
    return column.in_(hits) if hits else false()


def apply(query: Query, list_query: ListQuery, labels: dict, default_ordering) -> Query:
    """Search, filters and sort on a DeploymentRequest query; the usual ordering when no
    column is sorted, and as the tie-break when one is."""
    joins = _Joins(query)
    query = joins.query
    if list_query.q:
        query = query.filter(or_(*(joins.matches(k, list_query.q, labels) for k in COLUMNS if k not in _NOT_TEXT)))
    for key, value in list_query.filters.items():
        query = query.filter(joins.matches(key, value, labels))
    if list_query.sort:
        expr = joins.sort_expr(list_query.sort)
        # Empty values last either way, so a sort never opens on a screen of dashes.
        query = query.order_by(
            case((expr.is_(None), 1), else_=0),
            expr.desc() if list_query.desc else expr.asc(),
            *default_ordering,
        )
    else:
        query = query.order_by(*default_ordering)
    return query


def csv_text(rows: list[list[str]], keys: list[str]) -> str:
    """CSV with the chosen columns' labels as the header. A BOM so Excel reads UTF-8,
    and any cell a spreadsheet would run as a formula is prefixed with ' (CSV injection:
    a task id typed as =HYPERLINK(...) must stay text when someone opens the export)."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([COLUMNS[k] for k in keys])
    for row in rows:
        writer.writerow(["'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v for v in row])
    return "﻿" + out.getvalue()
