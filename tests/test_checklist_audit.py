"""The checklist audit on a request row is its own grant: admins always, anyone else only
with User.can_view_checklist_audit. Without it, the audit must not be in the page at all."""

from datetime import datetime, timezone

from app.models.checklist import ChecklistConfirmation
from app.models.deployment_request import DeploymentRequest, RequestStatus, RequestType
from app.models.user import User, UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_checklist_item, make_user

# Differs from the term's current label on purpose: proves the dialog shows the snapshot,
# and gives the leak test a string that can only come from the audit.
SNAPSHOT = "SNAPSHOT: close cronjobs as worded at start"


def _seed(session, *, dev_audit=False):
    make_user(session, id=1, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    dev = make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    dev.can_view_checklist_audit = dev_audit
    session.flush()
    item = make_checklist_item(session, label="Reworded later")
    request = DeploymentRequest(task_id="PR-A", requested_by=2, status=RequestStatus.in_progress,
                                request_type=RequestType.db_dump_restore, dump_source="crm-live",
                                restore_source="crm-staging", created_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
    session.add(request)
    session.flush()
    session.add(ChecklistConfirmation(request_id=request.id, checklist_item_id=item.id, item_label=SNAPSHOT,
                                      confirmed_by=1, confirmed_at=datetime(2026, 9, 2, 9, 30)))
    session.commit()
    return request


def test_audit_contents_absent_for_user_without_access(web):
    client, session = web
    _seed(session)
    login_as(client, "devone")

    page = client.get("/requests").text

    assert SNAPSHOT not in page
    assert "data-checklist-log=" not in page


def test_user_with_access_sees_audit_with_who(web):
    client, session = web
    request = _seed(session, dev_audit=True)
    login_as(client, "devone")

    page = client.get("/requests").text

    assert f'data-checklist-log="{request.id}"' in page
    log_start = page.index(f'id="checklist-log-{request.id}"')
    log = page[log_start:page.index("</ul>", log_start)]
    assert SNAPSHOT in log
    assert "Root Admin" in log


def test_admin_always_sees_audit(web):
    client, session = web
    request = _seed(session)
    login_as(client, "root")

    assert f'data-checklist-log="{request.id}"' in client.get("/requests").text


def test_admin_toggles_audit_access(web):
    client, session = web
    _seed(session)
    login_as(client, "root")

    client.post("/admin/users/2/set-checklist-audit-access", data={"can_view_checklist_audit": "on"})
    session.expire_all()
    assert session.get(User, 2).can_view_checklist_audit is True

    client.post("/admin/users/2/set-checklist-audit-access", data={})
    session.expire_all()
    assert session.get(User, 2).can_view_checklist_audit is False


def test_non_admin_cannot_toggle_audit_access(web):
    client, session = web
    _seed(session, dev_audit=True)
    login_as(client, "devone")

    assert client.post("/admin/users/2/set-checklist-audit-access", data={}).status_code == 403
    session.expire_all()
    assert session.get(User, 2).can_view_checklist_audit is True
