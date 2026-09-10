from datetime import datetime, timezone
from io import BytesIO

from openpyxl import load_workbook

from app.models.bitbucket_main_branch_status import BitbucketMainBranchStatus
from app.models.client import Client
from app.models.client_version_status import ClientVersionStatus
from app.services.export import order_task_dependency_rows_to_xlsx, release_tracker_rows_to_xlsx
from app.services.order_task_dependency import TaskRow


def test_release_tracker_rows_to_xlsx_writes_expected_columns():
    row = ClientVersionStatus(
        id=1, client_id=1,
        test_current_version="1.0", test_updated_at=datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc),
        live_current_version="2026.34.34", live_updated_at=datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc),
    )
    row.client = Client(name="CRM")
    # Main Version is now the live bitbucket_main_branch_status cache, shared across
    # every row — not a per-row snapshot — so it's passed in separately.
    main_status = BitbucketMainBranchStatus(
        id=1, version="2026.34.40", pr_number=15009,
        version_changed_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )

    content = release_tracker_rows_to_xlsx([row], "Release Tracker", main_status=main_status)
    workbook = load_workbook(BytesIO(content))
    sheet = workbook.active

    header_row = [cell.value for cell in sheet[1]]
    assert header_row == [
        "Client", "Test Current Version", "Test Updated At",
        "Live Current Version", "Live Updated At",
        "Main Version", "Main Updated At",
    ]
    data_row = [cell.value for cell in sheet[2]]
    assert data_row == [
        "CRM", "1.0", "2026-08-27 09:00 UTC",
        "2026.34.34", "2026-08-27 10:00 UTC",
        "2026.34.40 (PR #15009)", "2026-08-20 00:00 UTC",
    ]


def test_release_tracker_rows_to_xlsx_two_clients_share_the_same_main_version():
    row1 = ClientVersionStatus(id=1, client_id=1)
    row1.client = Client(name="Acme")
    row2 = ClientVersionStatus(id=2, client_id=2)
    row2.client = Client(name="Zebra Corp")
    main_status = BitbucketMainBranchStatus(id=1, version="2026.34.40", pr_number=15009)

    content = release_tracker_rows_to_xlsx([row1, row2], "Release Tracker", main_status=main_status)
    workbook = load_workbook(BytesIO(content))
    sheet = workbook.active

    main_version_col = [cell.value for cell in sheet[1]].index("Main Version") + 1
    assert sheet.cell(row=2, column=main_version_col).value == "2026.34.40 (PR #15009)"
    assert sheet.cell(row=3, column=main_version_col).value == "2026.34.40 (PR #15009)"


def test_release_tracker_rows_to_xlsx_without_a_bitbucket_sync_yet():
    row = ClientVersionStatus(id=1, client_id=1)
    row.client = Client(name="CRM")

    content = release_tracker_rows_to_xlsx([row], "Release Tracker", main_status=None)
    workbook = load_workbook(BytesIO(content))
    sheet = workbook.active

    data_row = [cell.value for cell in sheet[2]]
    # openpyxl round-trips an empty-string cell write as None on read — this just
    # confirms it didn't error and didn't write a stray "None" string into the cell.
    assert data_row[-2:] == [None, None]  # Main Version, Main Updated At


def _task_row(**overrides):
    defaults = dict(
        operation_id=1,
        order_id=11,
        order_custom_id="PR-00001",
        prod_order_pos_id=12,
        item_name="Item A",
        pos="0020",
        name="Milling",
        machine_name="M1",
        machine_group_id=13,
        machine_group_name="Team Rajib",
        status="PLANNED",
        start=None,
        end=None,
        due_date=None,
        has_dependency=True,
        is_blocked=True,
        blocked_by="Cutting",
        is_overdue=True,
    )
    defaults.update(overrides)
    return TaskRow(**defaults)


def test_order_task_dependency_xlsx_has_the_expected_header_row():
    content = order_task_dependency_rows_to_xlsx([_task_row()], "Order Task Dependency")

    sheet = load_workbook(BytesIO(content)).active
    assert [cell.value for cell in sheet[1]] == [
        "Order", "Item", "Pos", "Task", "Machine", "Machine Group",
        "Status", "Due Date", "Blocked By", "Overdue",
    ]


def test_order_task_dependency_xlsx_writes_one_flat_row_per_task():
    content = order_task_dependency_rows_to_xlsx([_task_row(), _task_row(operation_id=2, name="Drilling")], "S")

    sheet = load_workbook(BytesIO(content)).active
    assert sheet.max_row == 3  # header + 2 tasks
    # Due Date is None on this fixture: the getter writes "", which openpyxl
    # round-trips as None on read (the release-tracker test above documents the same).
    assert [cell.value for cell in sheet[2]] == [
        "PR-00001", "Item A", "0020", "Milling", "M1", "Team Rajib", "PLANNED", None, "Cutting", "Yes",
    ]


def test_order_task_dependency_xlsx_renders_a_non_overdue_task_blank():
    content = order_task_dependency_rows_to_xlsx([_task_row(is_overdue=False, blocked_by=None)], "S")

    sheet = load_workbook(BytesIO(content)).active
    # Blank, not the string "None" — openpyxl reads an empty-string write back as None.
    assert sheet["I2"].value is None
    assert sheet["J2"].value is None
