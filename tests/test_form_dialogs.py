"""New / Edit Request, Add / Edit Seeder and Edit Version open in the design's dialog.

The forms stay server-rendered: with ?dialog=1 a form page renders without the app
shell, and the page script loads it into a <dialog> and posts it back the same way
(validation errors come back into the dialog, a save follows the redirect). Without
scripts the same links still open the full pages.
"""

import re

from app.models.client import Client
from app.models.user import UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user
from tests.test_dashboard import _seed_editable_request
from tests.test_release_tracker import _seed_release_tracker_row
from tests.test_seeder_collection import _seed_seeder_command


def _bare(text):
    return 'id="sidenav"' not in text and "<html" not in text


def test_form_pages_render_without_the_shell_for_the_dialog(web):
    client, session = web
    _seed_editable_request(session)
    make_user(session, id=9, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    _seed_seeder_command(session, client_id=1, client_name="CRM", created_by=9)
    status = _seed_release_tracker_row(session, client_id=1, client_name="CRM", recorded_by=21)
    login_as(client, "root")

    for url, form_action in [
        ("/requests/new", 'action="/requests"'),
        ("/requests/1/edit", 'action="/requests/1/edit"'),
        ("/seeder-collection/new", 'action="/seeder-collection/new"'),
        ("/seeder-collection/1/edit", 'action="/seeder-collection/1/edit"'),
        (f"/release-tracker/{status.id}/edit", f'action="/release-tracker/{status.id}/edit"'),
    ]:
        dialog = client.get(url, params={"dialog": "1"})
        assert dialog.status_code == 200, url
        assert _bare(dialog.text), url
        assert form_action in dialog.text, url
        # The full page is unchanged.
        assert 'id="sidenav"' in client.get(url).text, url


def test_a_rejected_save_comes_back_bare_for_the_dialog(web):
    client, session = web
    make_user(session, id=1, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    session.add(Client(id=1, name="CRM"))
    session.commit()
    login_as(client, "root")

    response = client.post("/seeder-collection/new?dialog=1", data={"client_id": "", "title": "Perms", "command": "php artisan seed"})

    assert response.status_code == 400
    assert _bare(response.text)
    assert "Choose a client" in response.text


def test_the_links_open_the_dialog(web):
    client, session = web
    _seed_editable_request(session)
    make_user(session, id=9, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    _seed_seeder_command(session, client_id=1, client_name="CRM", created_by=9)
    status = _seed_release_tracker_row(session, client_id=1, client_name="CRM", recorded_by=21)
    login_as(client, "requester")

    requests_page = client.get("/requests").text
    assert re.search(r'<a href="/requests/new" class="btn btn-emph" data-dialog', requests_page)
    assert re.search(r'<a href="/requests/1/edit" class="action-link edit"[^>]*data-dialog', requests_page)

    login_as(client, "root")
    seeders = client.get("/seeder-collection").text
    assert re.search(r'<a href="/seeder-collection/new" class="btn btn-emph" data-dialog', seeders)
    assert re.search(r'<a href="/seeder-collection/1/edit"[^>]*data-dialog', seeders)
    tracker = client.get("/release-tracker").text
    assert re.search(rf'<a href="/release-tracker/{status.id}/edit"[^>]*data-dialog', tracker)
    assert 'id="form-dialog"' in tracker
