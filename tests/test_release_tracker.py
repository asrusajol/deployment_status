from datetime import datetime, timezone

from app.models.client import Client
from app.models.client_version_status import ClientVersionStatus
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user


def _seed_release_tracker_row(session, *, client_id=1, client_name="CRM", recorded_by=1, **overrides):
    if session.get(Client, client_id) is None:
        session.add(Client(id=client_id, name=client_name))
    make_user(session, id=recorded_by, name="Deployer", username=f"deployer{recorded_by}", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    defaults = dict(
        client_id=client_id,
        test_current_version="1.0", test_recorded_by=recorded_by, test_updated_at=datetime.now(timezone.utc),
        live_current_version="2.0", live_recorded_by=recorded_by, live_updated_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    row = ClientVersionStatus(**defaults)
    session.add(row)
    session.commit()
    return row


def test_release_tracker_page_renders_one_row_per_client(web):
    client, session = web
    _seed_release_tracker_row(session)
    login_as(client, "deployer1")

    response = client.get("/release-tracker")

    assert response.status_code == 200
    assert "1.0" in response.text
    assert "2.0" in response.text
    assert "CRM" in response.text
    assert 'name="environment"' not in response.text  # System filter is gone


def test_release_tracker_requires_login(web):
    client, session = web
    _seed_release_tracker_row(session)

    response = client.get("/release-tracker", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_release_tracker_filters_by_client(web):
    client, session = web
    _seed_release_tracker_row(session, client_id=1, client_name="CRM", test_current_version="1.0")
    _seed_release_tracker_row(session, client_id=2, client_name="Acme", recorded_by=2, test_current_version="9.0")
    login_as(client, "deployer1")

    response = client.get("/release-tracker", params={"client_id": "1"})

    assert "1.0" in response.text
    assert "9.0" not in response.text


def test_release_tracker_export_xlsx(web):
    client, session = web
    _seed_release_tracker_row(session)
    login_as(client, "deployer1")

    response = client.get("/release-tracker/export.xlsx")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_recorder_can_edit_the_environment_they_recorded(web):
    client, session = web
    row = _seed_release_tracker_row(session, test_current_version="1.0")
    login_as(client, "deployer1")

    response = client.post(
        f"/release-tracker/{row.id}/edit", data={"test_current_version": "1.1"}, follow_redirects=False
    )

    assert response.status_code == 303
    session.refresh(row)
    assert row.test_current_version == "1.1"
    assert row.live_current_version == "2.0"  # untouched — no live_current_version in the POST


def test_recorder_cannot_edit_the_other_environment(web):
    client, session = web
    row = _seed_release_tracker_row(session, test_recorded_by=1, live_recorded_by=1)
    make_user(session, id=2, name="Other Dev", username="otherdev", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    # Give live to a different recorder so deployer2 has no permission over it
    row.live_recorded_by = 2
    session.commit()
    login_as(client, "deployer1")

    response = client.post(f"/release-tracker/{row.id}/edit", data={"live_current_version": "9.9"})

    assert response.status_code == 403
    session.refresh(row)
    assert row.live_current_version == "2.0"


def test_edit_rejects_blank_current_version(web):
    client, session = web
    row = _seed_release_tracker_row(session, test_current_version="1.0")
    login_as(client, "deployer1")

    response = client.post(f"/release-tracker/{row.id}/edit", data={"test_current_version": "   "})

    assert response.status_code == 400
    session.refresh(row)
    assert row.test_current_version == "1.0"


def test_edit_no_op_does_not_bump_updated_at(web):
    client, session = web
    row = _seed_release_tracker_row(session, test_current_version="1.0")
    original_updated_at = row.test_updated_at
    session.commit()
    login_as(client, "deployer1")

    client.post(f"/release-tracker/{row.id}/edit", data={"test_current_version": "1.0"})  # same value

    session.refresh(row)
    assert row.test_updated_at == original_updated_at  # unchanged, since nothing actually differed
