"""The design's table features on every list table (app/static/grid.js): drag to
reorder, resize, the header menu (sort / filter / move / hide), the gear where the
design has one, and filler rows.

The behaviour is in the browser; these check what the server gives it — each table's
grid key, the gear, and sort values for cells whose text isn't what they mean
(switches, pickers, Yes/No icons).
"""

import re

from app.models.deployment_request import RequestType
from app.models.user import UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_checklist_item, make_user
from tests.test_dashboard import _seed_two_completed_deployments
from tests.test_release_tracker import _seed_release_tracker_row


def _admin(client, session):
    make_user(session, id=50, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "root")


def _gear(text, key):
    return re.search(rf'<button[^>]*data-grid-gear="{key}"[^>]*title="Table settings \(show / hide columns\)"', text)


def test_every_page_loads_the_grid_script_and_its_icons(web):
    client, session = web
    _admin(client, session)

    text = client.get("/dashboard/history").text

    assert re.search(r'<script src="/static/grid\.js\?v=\w+"></script>', text)
    assert 'id="grid-icons"' in text
    assert client.get("/static/grid.js").status_code == 200


def test_history_and_dashboard_tables_are_grids_with_a_gear(web):
    client, session = web
    _admin(client, session)
    _seed_two_completed_deployments(session)

    history = client.get("/dashboard/history").text
    dashboard = client.get("/dashboard").text

    assert 'class="status-table deployment-table" data-column-order="dt-history-columns"' in history
    assert _gear(history, "dt-history-columns")
    assert 'class="status-table deployment-table" data-column-order="dt-status-columns"' in dashboard
    assert _gear(dashboard, "dt-status-columns")


def test_release_tracker_is_a_grid_with_a_gear(web):
    client, session = web
    _admin(client, session)
    _seed_release_tracker_row(session, recorded_by=51)

    text = client.get("/release-tracker").text

    assert 'class="status-table release-tracker-table" data-column-order="dt-release-columns"' in text
    assert _gear(text, "dt-release-columns")


def test_users_grid_has_no_gear_and_sort_values_for_its_controls(web):
    """The design's Users table has no gear (gear: false) but sorts and filters."""
    client, session = web
    _admin(client, session)
    make_user(session, id=51, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()

    text = client.get("/admin/users").text

    assert 'class="grid-table users-table" data-column-order="dt-users-columns"' in text
    assert "data-grid-gear" not in text
    row = re.search(r'<tr data-user-row[^>]*>.*?name="role-51".*?</tr>', text, re.S).group(0)
    assert 'data-value="Developer"' in row          # the Role picker
    assert 'data-value="Off"' in row and 'data-value="On"' in row   # switches


def test_checklist_tables_are_one_grid_with_a_gear_and_yes_no_values(web):
    client, session = web
    _admin(client, session)
    make_checklist_item(session, label="Backup taken", request_types=(RequestType.standard,))

    text = client.get("/management/checklists").text

    tables = re.findall(r'<table class="grid-table checklist-table" data-group="\w+" data-column-order="dt-checklist-columns"', text)
    assert len(tables) == 4          # All + one per request type share one layout
    assert _gear(text, "dt-checklist-columns")
    row = re.search(r'<tr class="checklist-term"[^>]*>.*?</tr>', text, re.S).group(0)
    assert 'data-applies="standard" data-yes="1" data-value="Yes"' in row
    assert 'data-applies="test_local" data-yes="0" data-value="No"' in row
