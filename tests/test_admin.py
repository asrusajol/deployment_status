from app.models.user import User, UserRole
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


def test_admin_grants_login_access_to_user_with_no_username_yet(web):
    client, session = web
    make_user(session, id=1, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    make_user(session, id=2, name="Some Developer", role=UserRole.developer)
    session.commit()
    login_as(client, "root")

    response = client.post(
        "/admin/users/2/set-password",
        data={"username": "somedev", "new_password": "temp-pass-1"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    target = session.get(User, 2)
    session.refresh(target)
    assert target.username == "somedev"
    assert target.password_hash is not None
    assert target.must_change_password is True

    # And the granted account can now actually log in.
    login_response = client.post(
        "/login", data={"username": "somedev", "password": "temp-pass-1"}, follow_redirects=False
    )
    assert login_response.status_code == 303
    assert login_response.headers["location"] == "/change-password"


def test_admin_resets_password_for_user_who_already_has_access(web):
    client, session = web
    make_user(session, id=1, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    make_user(
        session, id=2, name="Some Developer", username="somedev", password="old-password-1",
        must_change_password=False,
    )
    session.commit()
    login_as(client, "root")

    response = client.post(
        "/admin/users/2/set-password", data={"new_password": "new-temp-pass-1"}, follow_redirects=False
    )

    assert response.status_code == 303
    target = session.get(User, 2)
    session.refresh(target)
    assert target.must_change_password is True  # forced again, since the admin now knows it

    old_login = client.post("/login", data={"username": "somedev", "password": "old-password-1"})
    assert old_login.status_code == 401
    new_login = client.post(
        "/login", data={"username": "somedev", "password": "new-temp-pass-1"}, follow_redirects=False
    )
    assert new_login.status_code == 303


def test_admin_set_password_rejects_short_password(web):
    client, session = web
    make_user(session, id=1, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    make_user(session, id=2, name="Some Developer", username="somedev")
    session.commit()
    login_as(client, "root")

    response = client.post("/admin/users/2/set-password", data={"new_password": "short"})

    assert response.status_code == 400
    target = session.get(User, 2)
    session.refresh(target)
    assert target.password_hash is None


def test_admin_set_password_rejects_duplicate_username(web):
    client, session = web
    make_user(session, id=1, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    make_user(session, id=2, name="Someone Else", username="taken")
    make_user(session, id=3, name="Some Developer")  # no username yet
    session.commit()
    login_as(client, "root")

    response = client.post(
        "/admin/users/3/set-password", data={"username": "taken", "new_password": "temp-pass-1"}
    )

    assert response.status_code == 400
    assert "already taken" in response.text


def test_admin_changes_a_users_role(web):
    client, session = web
    make_user(session, id=1, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    make_user(session, id=2, name="Some Developer", role=UserRole.developer)
    session.commit()
    login_as(client, "root")

    response = client.post("/admin/users/2/set-role", data={"role": "devops"}, follow_redirects=False)

    assert response.status_code == 303
    target = session.get(User, 2)
    session.refresh(target)
    assert target.role == UserRole.devops


def test_non_admin_cannot_change_roles(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    make_user(session, id=2, name="Some Developer", role=UserRole.developer)
    session.commit()
    login_as(client, "rajib")

    response = client.post("/admin/users/2/set-role", data={"role": "admin"})

    assert response.status_code == 403
