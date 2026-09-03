"""Renders DeploymentStatusRow lists (app/services/dashboard.py) to an .xlsx workbook, for
the "Export to Excel" button on both dashboards."""

from io import BytesIO
from typing import Callable

from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from app.models.bitbucket_main_branch_status import BitbucketMainBranchStatus
from app.models.client_version_status import ClientVersionStatus
from app.services.dashboard import DeploymentStatusRow

COLUMNS = [
    ("Client", lambda r: r.client_name),
    ("System", lambda r: r.environment.capitalize() if r.environment else ""),
    ("Task ID", lambda r: r.task_id or ""),
    ("Branch", lambda r: r.git_branch or ""),
    ("Commit", lambda r: r.commit_hash or ""),
    ("Version", lambda r: r.version or ""),
    ("Changes", lambda r: r.changes_description or ""),
    ("Requested By", lambda r: r.requested_by or ""),
    ("Approved By", lambda r: r.approved_by or ""),
    ("Deployed By", lambda r: r.deployed_by or ""),
    ("Requested At", lambda r: r.requested_at.strftime("%Y-%m-%d %H:%M UTC") if r.requested_at else ""),
    ("Deployed At", lambda r: r.deployed_at.strftime("%Y-%m-%d %H:%M UTC") if r.deployed_at else ""),
]


def _columns_to_xlsx(rows: list, columns: list[tuple[str, Callable]], sheet_title: str) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_title[:31]  # Excel's hard limit on sheet-name length

    headers = [label for label, _ in columns]
    sheet.append(headers)
    for row in rows:
        sheet.append([getter(row) for _, getter in columns])

    for index, (header, getter) in enumerate(columns, start=1):
        widest = max([len(header)] + [len(str(getter(row))) for row in rows])
        sheet.column_dimensions[get_column_letter(index)].width = min(widest + 2, 40)

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def rows_to_xlsx(rows: list[DeploymentStatusRow], sheet_title: str) -> bytes:
    return _columns_to_xlsx(rows, COLUMNS, sheet_title)


def _release_tracker_columns(main_status: BitbucketMainBranchStatus | None) -> list[tuple[str, Callable]]:
    # Main Version/Updated At are a live read of the single bitbucket_main_branch_status
    # cache row, identical for every client — not a per-row field — so they're closed
    # over here rather than read off each row (see ClientVersionStatus's docstring).
    main_version = main_status.version if main_status else None
    main_pr_number = main_status.pr_number if main_status else None
    main_updated_at = main_status.version_changed_at if main_status else None
    main_version_display = f"{main_version} (PR #{main_pr_number})" if main_version and main_pr_number else (main_version or "")
    main_updated_at_display = main_updated_at.strftime("%Y-%m-%d %H:%M UTC") if main_updated_at else ""

    return [
        ("Client", lambda r: r.client.name if r.client else ""),
        ("Test Current Version", lambda r: r.test_current_version or ""),
        ("Test Updated At", lambda r: r.test_updated_at.strftime("%Y-%m-%d %H:%M UTC") if r.test_updated_at else ""),
        ("Live Current Version", lambda r: r.live_current_version or ""),
        ("Live Updated At", lambda r: r.live_updated_at.strftime("%Y-%m-%d %H:%M UTC") if r.live_updated_at else ""),
        ("Main Version", lambda r: main_version_display),
        ("Main Updated At", lambda r: main_updated_at_display),
    ]


def release_tracker_rows_to_xlsx(
    rows: list[ClientVersionStatus], sheet_title: str, main_status: BitbucketMainBranchStatus | None = None
) -> bytes:
    return _columns_to_xlsx(rows, _release_tracker_columns(main_status), sheet_title)
