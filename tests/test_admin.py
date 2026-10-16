"""Users page access and listing. Saving changes is tests/test_admin_users_grid.py."""

from app.models.user import UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user


def test_admin_users_page_requires_admin_role(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "rajib")

    response = client.get("/admin/users")

    assert response.status_code == 403


def test_admin_users_page_redirects_anonymous_to_login(web):
    client, _session = web
    response = client.get("/admin/users", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_admin_users_page_lists_users(web):
    client, session = web
    make_user(session, id=1, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    make_user(session, id=2, name="Some Developer", role=UserRole.developer)  # no login access yet
    session.commit()
    login_as(client, "root")

    response = client.get("/admin/users")

    assert response.status_code == 200
    assert "Root Admin" in response.text
    assert "Some Developer" in response.text
    assert "No login access" in response.text


def test_return_override_column_shown_only_for_admin_rows(web):
    client, session = web
    make_user(session, id=1, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    make_user(session, id=2, name="Some Developer", role=UserRole.developer)
    session.commit()
    login_as(client, "root")

    response = client.get("/admin/users")

    assert response.status_code == 200
    assert "Show others" in response.text
