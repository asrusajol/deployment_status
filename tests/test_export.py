from datetime import datetime, timezone
from io import BytesIO

from openpyxl import load_workbook

from app.models.client import Client
from app.models.client_version_status import ClientVersionStatus
from app.services.export import release_tracker_rows_to_xlsx


def test_release_tracker_rows_to_xlsx_writes_expected_columns():
    row = ClientVersionStatus(
        id=1, client_id=1,
        test_current_version="1.0", test_updated_at=datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc),
        live_current_version="2026.34.34", live_updated_at=datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc),
        main_version="2026.34.40", main_pr_number=15009,
        main_updated_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
    row.client = Client(name="CRM")

    content = release_tracker_rows_to_xlsx([row], "Release Tracker")
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
