from app.models.user import UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user


def test_login_with_correct_credentials_redirects_to_dashboard(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()

    response = client.post(
        "/login", data={"username": "rajib", "password": DEFAULT_TEST_PASSWORD}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"


def test_login_with_must_change_password_redirects_there_instead(web):
    client, session = web
    make_user(
        session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD,
        must_change_password=True,
    )
    session.commit()

    response = client.post(
        "/login", data={"username": "rajib", "password": DEFAULT_TEST_PASSWORD}, follow_redirects=False
    )

    assert response.headers["location"] == "/change-password"


def test_login_with_wrong_password_is_rejected(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()

    response = client.post("/login", data={"username": "rajib", "password": "wrong-password"})

    assert response.status_code == 401
    assert "Invalid username or password" in response.text


def test_login_with_unknown_username_is_rejected(web):
    client, _session = web
    response = client.post("/login", data={"username": "ghost", "password": "whatever"})
    assert response.status_code == 401


def test_login_for_user_with_no_password_set_is_rejected(web):
    # An employee synced from the CRM but never granted login access by an admin.
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=None)
    session.commit()

    response = client.post("/login", data={"username": "rajib", "password": "anything"})

    assert response.status_code == 401


def test_already_logged_in_visiting_login_redirects_to_dashboard(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "rajib")

    response = client.get("/login", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/dashboard"


def test_logout_clears_session_and_protected_routes_redirect_again(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "rajib")
    assert client.get("/dashboard").status_code == 200

    client.post("/logout", follow_redirects=False)

    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_must_change_password_blocks_other_protected_routes(web):
    client, session = web
    make_user(
        session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD,
        must_change_password=True,
    )
    session.commit()
    login_as(client, "rajib")

    for path in ("/dashboard", "/requests", "/requests/new"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/change-password", path

    # But the change-password page itself must stay reachable, or the user is stuck.
    assert client.get("/change-password").status_code == 200


def test_change_password_success_clears_forced_flag_and_allows_new_password(web):
    client, session = web
    make_user(
        session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD,
        must_change_password=True,
    )
    session.commit()
    login_as(client, "rajib")

    response = client.post(
        "/change-password",
        data={
            "current_password": DEFAULT_TEST_PASSWORD,
            "new_password": "brand-new-pw-1",
            "confirm_password": "brand-new-pw-1",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    assert client.get("/dashboard").status_code == 200  # no longer forced

    # And the old password no longer works after logging out.
    client.post("/logout")
    old_login = client.post("/login", data={"username": "rajib", "password": DEFAULT_TEST_PASSWORD})
    assert old_login.status_code == 401
    new_login = client.post("/login", data={"username": "rajib", "password": "brand-new-pw-1"}, follow_redirects=False)
    assert new_login.status_code == 303


def test_change_password_wrong_current_password_is_rejected(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "rajib")

    response = client.post(
        "/change-password",
        data={"current_password": "not-it", "new_password": "brand-new-pw-1", "confirm_password": "brand-new-pw-1"},
    )

    assert response.status_code == 400
    assert "incorrect" in response.text


def test_change_password_mismatched_confirmation_is_rejected(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "rajib")

    response = client.post(
        "/change-password",
        data={
            "current_password": DEFAULT_TEST_PASSWORD,
            "new_password": "brand-new-pw-1",
            "confirm_password": "something-else",
        },
    )

    assert response.status_code == 400
    assert "match" in response.text


def test_change_password_too_short_is_rejected(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "rajib")

    response = client.post(
        "/change-password",
        data={"current_password": DEFAULT_TEST_PASSWORD, "new_password": "short", "confirm_password": "short"},
    )

    assert response.status_code == 400


import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.auth import can_edit_client_version_status
from app.database import Base
from app.models.client import Client
from app.models.client_version_status import ClientVersionStatus
from app.models.deployment_request import DeploymentEnvironment


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _make_permission_test_user(session, *, id, name, role=UserRole.developer, username=None):
    # Deliberately independent of tests.conftest.make_user
    # — these tests only need a bare User row, no password/login machinery.
    from app.models.user import User

    user = User(id=id, name=name, role=role, username=username)
    session.add(user)
    session.flush()
    return user


def _make_client_version_status(session, *, test_recorded_by=None, live_recorded_by=None):
    session.add(Client(id=1, name="CRM"))
    session.flush()
    row = ClientVersionStatus(
        client_id=1, test_current_version="1.0", test_recorded_by=test_recorded_by,
        live_current_version="2.0", live_recorded_by=live_recorded_by,
    )
    session.add(row)
    session.flush()
    return row


def test_recorder_can_edit_the_environment_they_recorded(db_session):
    user = _make_permission_test_user(db_session, id=5, name="Deployer", username="deployer")
    row = _make_client_version_status(db_session, test_recorded_by=5)
    assert can_edit_client_version_status(user, row, DeploymentEnvironment.test) is True


def test_recorder_cannot_edit_the_other_environment_on_the_same_row(db_session):
    user = _make_permission_test_user(db_session, id=5, name="Deployer", username="deployer")
    row = _make_client_version_status(db_session, test_recorded_by=5, live_recorded_by=6)
    assert can_edit_client_version_status(user, row, DeploymentEnvironment.live) is False


def test_admin_can_edit_either_environment(db_session):
    admin = _make_permission_test_user(db_session, id=7, name="Root Admin", role=UserRole.admin, username="root")
    row = _make_client_version_status(db_session, test_recorded_by=5, live_recorded_by=6)
    assert can_edit_client_version_status(admin, row, DeploymentEnvironment.test) is True
    assert can_edit_client_version_status(admin, row, DeploymentEnvironment.live) is True


def test_devops_can_edit_either_environment(db_session):
    devops = _make_permission_test_user(db_session, id=8, name="Ops", role=UserRole.devops, username="ops")
    row = _make_client_version_status(db_session, test_recorded_by=5, live_recorded_by=6)
    assert can_edit_client_version_status(devops, row, DeploymentEnvironment.test) is True
    assert can_edit_client_version_status(devops, row, DeploymentEnvironment.live) is True
