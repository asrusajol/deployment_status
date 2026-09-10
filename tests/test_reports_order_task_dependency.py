from datetime import date

import pytest

from app.models.team import Team
from app.models.user import UserRole
from app.routers import reports_order_task_dependency as report_module
from app.services.order_task_dependency import OrderGroup, TaskRow
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user

URL = "/reports/order-task-dependency"


def task(**overrides):
    defaults = dict(
        operation_id=1,
        order_id=11,
        order_custom_id="PR-00001",
        prod_order_pos_id=12,
        item_name="Item A",
        pos="0020",
        name="Milling",
        machine_name="M1",
        machine_group_id=13,
        machine_group_name="Team Rajib",
        status="PLANNED",
        start=None,
        end=None,
        due_date=None,
        has_dependency=True,
        is_blocked=True,
        blocked_by="Cutting",
        is_overdue=True,
        is_scheduled_past_due=False,
    )
    defaults.update(overrides)
    return TaskRow(**defaults)


def group(tasks=None):
    return OrderGroup(order_id=11, order_custom_id="PR-00001", tasks=tasks or [task()])


@pytest.fixture()
def report(monkeypatch):
    """Stub the CRM: record the filters the route resolved, return canned groups."""
    calls = {}

    def fake_load_report(provider, filters, today, machine_group_names=None):
        calls["filters"] = filters
        calls["machine_group_names"] = machine_group_names
        return calls.get("groups", [group()])

    monkeypatch.setattr(report_module, "load_report", fake_load_report)
    monkeypatch.setattr(report_module, "build_provider", lambda settings: object())
    return calls


def signed_in(web, role=UserRole.devops):
    client, session = web
    make_user(session, id=1, name="O", role=role, username="o", password=DEFAULT_TEST_PASSWORD)
    session.add(Team(id=13, source_system_id="MG-00013", name="Team Rajib"))
    session.commit()
    login_as(client, "o")
    return client


def test_the_page_renders_the_tasks(web, report):
    response = signed_in(web).get(URL)

    assert response.status_code == 200
    assert "PR-00001" in response.text
    assert "Milling" in response.text
    assert "Cutting" in response.text


def test_a_developer_is_refused(web, report):
    client = signed_in(web, role=UserRole.developer)

    assert client.get(URL).status_code == 403


def test_the_machine_group_dropdown_comes_from_the_local_teams_table(web, report):
    response = signed_in(web).get(URL)

    assert "Team Rajib" in response.text


def test_group_names_are_handed_to_the_loader_from_the_local_teams_table(web, report):
    # The report rows show a group NAME, but the CRM fetch only yields group ids —
    # the names come from the same local mirror that fills the dropdown.
    signed_in(web).get(URL)

    assert report["machine_group_names"] == {13: "Team Rajib"}


def test_filters_are_passed_through_to_the_loader(web, report):
    signed_in(web).get(URL, params={"start": "2026-09-01", "end": "2026-09-30", "machine_group_id": 13, "overdue_only": "on"})

    filters = report["filters"]
    assert filters.start == date(2026, 9, 1)
    assert filters.end == date(2026, 9, 30)
    assert filters.machine_group_ids == [13]
    assert filters.overdue_only is True


def test_show_closed_reaches_the_loader_when_the_checkbox_is_submitted(web, report):
    signed_in(web).get(URL, params={"show_closed": "on"})

    assert report["filters"].show_closed is True


def test_show_closed_reaches_the_loader_as_false_when_absent(web, report):
    signed_in(web).get(URL)

    assert report["filters"].show_closed is False


def test_an_invalid_date_shows_an_error_banner_not_a_500(web, report):
    response = signed_in(web).get(URL, params={"start": "not-a-date"})

    assert response.status_code == 200
    assert "YYYY-MM-DD" in response.text


def test_a_crm_failure_shows_an_error_banner_not_a_500(web, monkeypatch):
    def boom(provider, filters, today, machine_group_names=None):
        raise OSError("crm.test.local unreachable")

    monkeypatch.setattr(report_module, "load_report", boom)
    monkeypatch.setattr(report_module, "build_provider", lambda settings: object())

    response = signed_in(web).get(URL)

    assert response.status_code == 200
    assert "could not be loaded" in response.text


def test_an_empty_result_renders_an_empty_state(web, report):
    report["groups"] = []

    response = signed_in(web).get(URL)

    assert response.status_code == 200
    assert "No orders" in response.text


def test_the_excel_export_resolves_the_same_filters_as_the_page(web, report):
    client = signed_in(web)
    params = {"start": "2026-09-01", "end": "2026-09-30", "machine_group_id": 13, "overdue_only": "on"}

    page_response = client.get(URL, params=params)
    assert page_response.status_code == 200
    page_filters = report["filters"]

    export_response = client.get(f"{URL}/export.xlsx", params=params)
    assert export_response.status_code == 200
    assert export_response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert report["filters"] == page_filters


def test_the_excel_export_is_refused_for_a_developer(web, report):
    assert signed_in(web, role=UserRole.developer).get(f"{URL}/export.xlsx").status_code == 403


def test_the_pdf_export_returns_a_pdf(web, report):
    response = signed_in(web).get(f"{URL}/export.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")


def test_the_pdf_export_is_refused_for_a_developer(web, report):
    assert signed_in(web, role=UserRole.developer).get(f"{URL}/export.pdf").status_code == 403
