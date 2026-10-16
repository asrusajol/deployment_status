"""The design's toast: after an action saves, the next page shows a short confirmation
once. The server keeps it in the session across the redirect (app/toast.py)."""

import html
import re

from app.models.client import Client
from app.models.seeder_command import SeederCommand
from app.models.user import UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_checklist_item, make_user
from tests.test_dashboard import _seed_in_progress_standard_request, _seed_pending_request


def _toasts(text):
    return [html.unescape(t) for t in re.findall(r'<div class="toast"[^>]*data-toast>([^<]*)</div>', text)]


def test_toast_rides_the_redirect_and_is_gone_after(web):
    client, session = web
    request = _seed_pending_request(session)
    login_as(client, "lead")

    response = client.post(f"/requests/{request.id}/approve", follow_redirects=False)
    first = client.get(response.headers["location"]).text
    again = client.get("/requests").text

    assert _toasts(first) == [f"Request #{request.id} approved"]
    assert _toasts(again) == []


def test_deploy_toast(web):
    client, session = web
    request = _seed_in_progress_standard_request(session)
    login_as(client, "deployer")

    client.post(f"/requests/{request.id}/deploy", data={"current_version": "2026.1"}, follow_redirects=False)

    assert _toasts(client.get("/requests").text) == [
        f"Request #{request.id} marked as deployed — history and release tracker updated"
    ]


def test_return_names_the_requester(web):
    client, session = web
    request = _seed_in_progress_standard_request(session)
    login_as(client, "deployer")

    client.post(f"/requests/{request.id}/return", data={"reason": "Wrong branch"}, follow_redirects=False)

    assert _toasts(client.get("/requests").text) == [f"Request #{request.id} returned to Requester"]


def test_a_failed_action_shows_no_toast(web):
    client, session = web
    request = _seed_in_progress_standard_request(session)
    login_as(client, "deployer")

    response = client.post(f"/requests/{request.id}/return", data={"reason": "   "}, follow_redirects=False)

    assert response.status_code == 400
    assert _toasts(client.get("/requests").text) == []


def test_user_settings_saved(web):
    client, session = web
    make_user(session, id=1, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "root")

    client.post("/admin/users/save", data={"user_ids": ["1"], "role-1": "admin", "active-1": "on"}, follow_redirects=False)

    assert _toasts(client.get("/admin/users").text) == ["User settings saved"]


def test_checklist_add_update_retire_restore(web):
    client, session = web
    make_user(session, id=1, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    item = make_checklist_item(session, label="Backup taken")
    login_as(client, "root")

    def after(path, data):
        client.post(path, data=data, follow_redirects=False)
        return _toasts(client.get("/management/checklists").text)

    assert after("/management/checklists", {"label": "Smoke test", "request_types": "standard"}) == ["Checklist item added"]
    assert after(f"/management/checklists/{item.id}/edit", {"label": "Backup taken!"}) == ["Checklist item updated"]
    assert after(f"/management/checklists/{item.id}/deactivate", {}) == ["Item moved to Retire"]
    assert after(f"/management/checklists/{item.id}/activate", {}) == ['"Backup taken!" restored']


def test_seeder_added_saved_deleted(web):
    client, session = web
    make_user(session, id=1, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    session.add(Client(id=1, name="CRM"))
    session.commit()
    login_as(client, "root")

    client.post("/seeder-collection/new", data={"client_id": "1", "title": "Perms", "command": "php artisan seed"},
                follow_redirects=False)
    assert _toasts(client.get("/seeder-collection").text) == ["Seeder for CRM added"]

    seeder = session.query(SeederCommand).one()
    client.post(f"/seeder-collection/{seeder.id}/edit", data={"title": "Perms 2", "command": "php artisan seed"},
                follow_redirects=False)
    assert _toasts(client.get("/seeder-collection").text) == ["Seeder for CRM saved"]

    client.post(f"/seeder-collection/{seeder.id}/delete", follow_redirects=False)
    assert _toasts(client.get("/seeder-collection").text) == ["Seeder for CRM deleted"]


def test_logout_says_signed_out_on_the_login_page(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "rajib")

    client.post("/logout", follow_redirects=False)

    assert _toasts(client.get("/login").text) == ["Signed out"]


def test_theme_switch_says_which_theme(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "rajib")

    text = client.get("/requests?theme=schertech").text

    assert _toasts(text) == ['Theme "Schertech" applied']
    assert _toasts(client.get("/requests?theme=schertech").text) == []   # already on it


def test_pages_carry_the_toast_hook_for_browser_only_actions(web):
    """Copying happens in the browser, so its toast comes from the page script."""
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "rajib")

    assert "window.dtToast = " in client.get("/requests").text


def test_a_dialog_fetch_leaves_the_toast_for_the_page_it_lands_on(web):
    """A dialog posts with fetch, which follows the redirect itself; that hidden page
    load mustn't use up the toast meant for the page the browser goes to next."""
    client, session = web
    make_user(session, id=1, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    session.add(Client(id=1, name="CRM"))
    session.commit()
    login_as(client, "root")
    dialog = {"X-Requested-With": "dt-dialog"}

    followed = client.post("/seeder-collection/new?dialog=1", headers=dialog,
                           data={"client_id": "1", "title": "Perms", "command": "php artisan seed"})

    assert _toasts(followed.text) == []
    assert _toasts(client.get("/seeder-collection").text) == ["Seeder for CRM added"]
