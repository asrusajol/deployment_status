from datetime import datetime, timezone
from io import BytesIO

from openpyxl import load_workbook

from app.models.client import Client
from app.models.client_version_record import ClientVersionRecord
from app.models.deployment_request import DeploymentEnvironment
from app.models.user import User
from app.services.export import release_tracker_rows_to_xlsx


def test_release_tracker_rows_to_xlsx_writes_expected_columns():
    record = ClientVersionRecord(
        id=1,
        client_id=1,
        environment=DeploymentEnvironment.live,
        current_version="2026.34.34",
        previous_version="2026.34.30",
        main_version="2026.34.40",
        main_pr_number=1234,
        created_at=datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc),
    )
    record.client = Client(name="CRM")
    record.recorder = User(name="Deployer")

    content = release_tracker_rows_to_xlsx([record], "Release Tracker")
    workbook = load_workbook(BytesIO(content))
    sheet = workbook.active

    header_row = [cell.value for cell in sheet[1]]
    assert header_row == [
        "Client", "System", "Current Version", "Previous Version",
        "Current Version at Main", "Recorded By", "Updated At",
    ]
    data_row = [cell.value for cell in sheet[2]]
    assert data_row == [
        "CRM", "Live", "2026.34.34", "2026.34.30",
        "2026.34.40 (PR #1234)", "Deployer", "2026-08-27 10:00 UTC",
    ]
