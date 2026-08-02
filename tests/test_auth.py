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
