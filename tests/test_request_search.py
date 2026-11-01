"""Requests page: the design's Search, column sort / filter and CSV export.

The list is paginated on the server, so all of it runs there — searching or sorting
only the 15 rows on screen would quietly miss everything on the other pages.
"""

import csv
import io
from datetime import datetime, timedelta, timezone

from app.models.client import Client
from app.models.deployment_execution import DeploymentExecution, ExecutionStatus
from app.models.deployment_request import DeploymentEnvironment, DeploymentRequest, RequestStatus
from app.models.user import UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


def _seed(session):
    session.add_all([Client(id=1, name="CRM"), Client(id=2, name="PlanVisu")])
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    make_user(session, id=2, name="Zunayed Rabbi", role=UserRole.devops, username="zunayed", password=DEFAULT_TEST_PASSWORD)
    rows = [
        # task, client, env, requester, status, hours after T0
        ("PR-100", 1, DeploymentEnvironment.live, 1, RequestStatus.approved, 0),
        ("PR-200", 2, DeploymentEnvironment.test, 2, RequestStatus.completed, 1),
        ("PR-300", 2, DeploymentEnvironment.live, 1, RequestStatus.in_progress, 2),
        ("=cmd()", 1, DeploymentEnvironment.test, 2, RequestStatus.rejected, 3),
    ]
    made = {}
    for task, client_id, env, requester, status, hours in rows:
        r = DeploymentRequest(
            task_id=task, module_name="Interface", client_id=client_id, environment=env, version="V12",
            git_branch="release/v12", requested_by=requester, status=status, created_at=T0 + timedelta(hours=hours),
        )
        session.add(r)
        session.flush()
        made[task] = r
    session.add(DeploymentExecution(
        request_id=made["PR-200"].id, executed_by=2, claimed_at=T0, started_at=T0,
        completed_at=T0 + timedelta(days=2), status=ExecutionStatus.completed,
    ))
    session.commit()
    return made


def _tasks(text):
    """Task IDs in table-body order (both tables), from the Task ID cells."""
    body = text[text.index("<tbody>"):text.rindex("</tbody>")]
    order = []
    for task in ("PR-100", "PR-200", "PR-300", "=cmd()"):
        at = body.find(f"<td>{task}</td>")
        if at != -1:
            order.append((at, task))
    return [t for _, t in sorted(order)]


def _page(client, query=""):
    return client.get("/requests" + query).text


def test_search_box_is_in_the_toolbar_and_keeps_its_text(web):
    client, session = web
    _seed(session)
    login_as(client, "rajib")

    text = _page(client, "?q=crm")

    assert 'id="request-search"' in text
    assert 'name="q" value="crm"' in text


def test_search_matches_any_column_across_every_page(web):
    client, session = web
    _seed(session)
    login_as(client, "rajib")

    assert set(_tasks(_page(client, "?q=planvisu"))) == {"PR-200", "PR-300"}       # client
    assert _tasks(_page(client, "?q=pr-1")) == ["PR-100"]                          # task id, any case
    assert set(_tasks(_page(client, "?q=zunayed"))) == {"PR-200", "=cmd()"}        # requested by
    assert _tasks(_page(client, "?q=deployed")) == ["PR-200"]                      # status, by its label
    assert set(_tasks(_page(client, "?q=live"))) == {"PR-100", "PR-300"}           # system


def test_search_count_is_in_the_title_and_pages_keep_the_search(web):
    client, session = web
    _seed(session)
    login_as(client, "rajib")

    text = _page(client, "?q=planvisu")

    assert "Request (2)" in text
    assert 'name="q" value="planvisu"' in text[text.index('class="page-size-form"'):]


def test_search_with_no_match_says_so(web):
    client, session = web
    _seed(session)
    login_as(client, "rajib")

    text = _page(client, "?q=nothing-like-this")

    assert "Request (0)" in text
    assert "No requests match" in text


def test_sort_by_a_column_both_ways(web):
    client, session = web
    _seed(session)
    login_as(client, "rajib")

    assert _tasks(_page(client, "?sort=requested-at&dir=asc")) == ["PR-100", "PR-200", "PR-300", "=cmd()"]
    assert _tasks(_page(client, "?sort=requested-at&dir=desc")) == ["=cmd()", "PR-300", "PR-200", "PR-100"]
    assert set(_tasks(_page(client, "?sort=client&dir=asc"))[:2]) == {"PR-100", "=cmd()"}   # CRM first
    assert set(_tasks(_page(client, "?sort=client&dir=desc"))[:2]) == {"PR-200", "PR-300"}  # PlanVisu first


def test_sort_by_deployed_at_puts_undeployed_last(web):
    client, session = web
    _seed(session)
    login_as(client, "rajib")

    assert _tasks(_page(client, "?sort=deployed-at&dir=desc"))[0] == "PR-200"
    assert _tasks(_page(client, "?sort=deployed-at&dir=asc"))[0] == "PR-200"


def test_unknown_sort_falls_back_to_the_usual_order(web):
    client, session = web
    _seed(session)
    login_as(client, "rajib")

    assert _tasks(_page(client, "?sort=nope")) == _tasks(_page(client))


def test_sorted_header_is_marked(web):
    client, session = web
    _seed(session)
    login_as(client, "rajib")

    text = _page(client, "?sort=status&dir=desc")

    assert 'data-sort="status" data-dir="desc"' in text


def test_column_filters_combine_with_each_other_and_search(web):
    client, session = web
    _seed(session)
    login_as(client, "rajib")

    assert set(_tasks(_page(client, "?f-client=plan"))) == {"PR-200", "PR-300"}
    assert _tasks(_page(client, "?f-client=plan&f-status=progress")) == ["PR-300"]
    assert _tasks(_page(client, "?f-client=plan&q=pr-2")) == ["PR-200"]
    # Unknown columns are ignored rather than erroring.
    assert len(_tasks(_page(client, "?f-nope=x"))) == 4


def test_filters_are_on_the_page_for_the_header_menus(web):
    client, session = web
    _seed(session)
    login_as(client, "rajib")

    text = _page(client, "?f-client=plan")

    assert 'name="f-client" value="plan"' in text


def _csv(client, query=""):
    response = client.get("/requests/export.csv" + query)
    assert response.status_code == 200
    return response, list(csv.reader(io.StringIO(response.text.lstrip("﻿"))))


def test_csv_export_has_every_matching_row_and_the_chosen_columns(web):
    client, session = web
    _seed(session)
    login_as(client, "rajib")

    response, rows = _csv(client, "?q=planvisu&cols=task-id,client,status")

    assert response.headers["content-type"].startswith("text/csv")
    assert 'filename="deployment-requests.csv"' in response.headers["content-disposition"]
    assert rows[0] == ["Task ID", "Client", "Status"]
    assert sorted(rows[1:]) == [["PR-200", "PlanVisu", "Deployed"], ["PR-300", "PlanVisu", "In Progress"]]


def test_csv_export_defaults_to_every_column_and_follows_the_sort(web):
    client, session = web
    _seed(session)
    login_as(client, "rajib")

    _response, rows = _csv(client, "?sort=requested-at&dir=asc")

    assert rows[0][:3] == ["Type", "Task ID", "Module Name"]
    assert "Action" not in rows[0]
    assert len(rows[0]) == 13
    assert [r[1] for r in rows[1:]][:3] == ["PR-100", "PR-200", "PR-300"]


def test_csv_export_defuses_spreadsheet_formulas(web):
    client, session = web
    _seed(session)
    login_as(client, "rajib")

    _response, rows = _csv(client, "?q=cmd&cols=task-id")

    assert rows[1] == ["'=cmd()"]


def test_csv_export_needs_login(web):
    client, session = web
    _seed(session)

    response = client.get("/requests/export.csv", follow_redirects=False)

    assert response.status_code == 303


def test_column_keys_are_the_table_scripts_slugged_headers():
    """The page script keys columns by slugging the header text; the server's keys
    must be the same, or sort / filter / cols would name a different column."""
    import re

    from app.services.request_query import COLUMNS

    for key, label in COLUMNS.items():
        assert re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") == key
