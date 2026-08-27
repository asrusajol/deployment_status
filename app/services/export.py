"""Renders DeploymentStatusRow lists (app/services/dashboard.py) to an .xlsx workbook, for
the "Export to Excel" button on both dashboards."""

from io import BytesIO

from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from app.models.client_version_record import ClientVersionRecord
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


def rows_to_xlsx(rows: list[DeploymentStatusRow], sheet_title: str) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_title[:31]  # Excel's hard limit on sheet-name length

    headers = [label for label, _ in COLUMNS]
    sheet.append(headers)
    for row in rows:
        sheet.append([getter(row) for _, getter in COLUMNS])

    for index, (header, getter) in enumerate(COLUMNS, start=1):
        widest = max([len(header)] + [len(str(getter(row))) for row in rows])
        sheet.column_dimensions[get_column_letter(index)].width = min(widest + 2, 40)

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


RELEASE_TRACKER_COLUMNS = [
    ("Client", lambda r: r.client.name if r.client else ""),
    ("System", lambda r: r.environment.value.capitalize() if r.environment else ""),
    ("Current Version", lambda r: r.current_version or ""),
    ("Previous Version", lambda r: r.previous_version or ""),
    (
        "Current Version at Main",
        lambda r: f"{r.main_version} (PR #{r.main_pr_number})" if r.main_version and r.main_pr_number
        else (r.main_version or ""),
    ),
    ("Recorded By", lambda r: r.recorder.name if r.recorder else ""),
    ("Updated At", lambda r: r.updated_at.strftime("%Y-%m-%d %H:%M UTC") if r.updated_at else ""),
]


def release_tracker_rows_to_xlsx(rows: list[ClientVersionRecord], sheet_title: str) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_title[:31]

    headers = [label for label, _ in RELEASE_TRACKER_COLUMNS]
    sheet.append(headers)
    for row in rows:
        sheet.append([getter(row) for _, getter in RELEASE_TRACKER_COLUMNS])

    for index, (header, getter) in enumerate(RELEASE_TRACKER_COLUMNS, start=1):
        widest = max([len(header)] + [len(str(getter(row))) for row in rows]) if rows else len(header)
        sheet.column_dimensions[get_column_letter(index)].width = min(widest + 2, 40)

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
