import json

import httpx
import pytest

from app.config import Settings
from app.services.task_source import (
    InHouseTaskSourceProvider,
    TeamInfo,
    TeamLeadInfo,
    UserContactInfo,
    UserInfo,
)


def _make_provider(handler):
    settings = Settings(
        task_api_base_url="http://crm.test.local/api",
        task_api_username="ahamad",
        task_api_password="placeholder",
    )
    client = httpx.Client(base_url=settings.task_api_base_url, transport=httpx.MockTransport(handler))
    return InHouseTaskSourceProvider(settings, client=client)


def test_login_extracts_token_from_real_response_shape():
    def handler(request):
        assert request.url.path == "/api/login"
        assert json.loads(request.content) == {
            "username": "ahamad",
            "password": "placeholder",
            "ignorePermissions": True,
        }
        return httpx.Response(
            200,
            json={
                "user": {
                    "id": 59,
                    "custom_id": "101088",
                    "username": "ahamad",
                    "name": "Rajib Ahamad",
                    "permissions": [],
                    "machine_id": None,
                },
                "token": "1|fake-token-for-tests",
            },
        )

    provider = _make_provider(handler)
    assert provider._login() == "1|fake-token-for-tests"


def test_login_missing_token_field_raises_with_helpful_message():
    def handler(request):
        return httpx.Response(200, json={"unexpected": "shape"})

    provider = _make_provider(handler)
    with pytest.raises(RuntimeError, match="no top-level 'token' field"):
        provider._login()


def test_request_authenticates_lazily_and_sends_bearer_header():
    def handler(request):
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"token": "tok-1"})
        assert request.headers["authorization"] == "Bearer tok-1"
        return httpx.Response(200, json={"ok": True})

    provider = _make_provider(handler)
    response = provider._request("GET", "/some-resource")
    assert response.json() == {"ok": True}


def test_request_reauthenticates_once_on_401_then_succeeds():
    calls = {"login": 0, "data": 0}

    def handler(request):
        if request.url.path == "/api/login":
            calls["login"] += 1
            token = "tok-1" if calls["login"] == 1 else "tok-2"
            return httpx.Response(200, json={"token": token})
        calls["data"] += 1
        if request.headers["authorization"] == "Bearer tok-1":
            return httpx.Response(401, json={"error": "token expired"})
        return httpx.Response(200, json={"ok": True})

    provider = _make_provider(handler)
    response = provider._request("GET", "/some-resource")

    assert response.json() == {"ok": True}
    assert calls["login"] == 2
    assert calls["data"] == 2


def test_odata_get_all_pages_until_a_short_page_is_returned():
    # 5 total rows, page_size=2 -> pages of 2, 2, 1 across $skip=0,2,4
    all_rows = [{"id": i} for i in range(5)]

    def handler(request):
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"token": "tok-1"})
        skip = int(request.url.params["$skip"])
        top = int(request.url.params["$top"])
        page = all_rows[skip : skip + top]
        return httpx.Response(200, json={"value": page})

    provider = _make_provider(handler)
    rows = provider._odata_get_all("/odata/Whatever", {}, page_size=2)
    assert rows == all_rows


def test_list_users_filters_active_and_maps_machine_fields():
    def handler(request):
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"token": "tok-1"})
        # OData lives at the domain root, NOT under /api — see task_source.py's docstring.
        assert str(request.url).startswith("http://crm.test.local/odata/Machines")
        assert request.url.path == "/odata/Machines"
        params = request.url.params
        assert params["$filter"] == "is_active eq true"
        assert params["$select"] == "custom_id,name,machine_group_id"
        assert params["$orderby"] == "custom_id asc"
        return httpx.Response(
            200,
            json={
                "value": [
                    {"custom_id": "101088", "name": "Rajib Ahamad", "machine_group_id": 4},
                    {"custom_id": "101099", "name": "Farhan Ahmed", "machine_group_id": None},
                ]
            },
        )

    provider = _make_provider(handler)
    users = provider.list_users()

    assert users == [
        UserInfo(source_system_id="101088", name="Rajib Ahamad", machine_group_id=4),
        UserInfo(source_system_id="101099", name="Farhan Ahmed", machine_group_id=None),
    ]


def test_list_teams_has_no_is_active_filter_and_maps_group_fields():
    def handler(request):
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"token": "tok-1"})
        assert request.url.path == "/odata/MachineGroups"
        params = request.url.params
        # Unlike list_users(), no $filter here — see task_source.py's list_teams() comment.
        assert "$filter" not in params
        assert params["$select"] == "id,custom_id,name"
        return httpx.Response(
            200,
            json={
                "value": [
                    {"id": 1, "custom_id": "MG-00001", "name": "Team QA"},
                    {"id": 3, "custom_id": "MG-00003", "name": "Developer"},
                ]
            },
        )

    provider = _make_provider(handler)
    teams = provider.list_teams()

    assert teams == [
        TeamInfo(id=1, source_system_id="MG-00001", name="Team QA"),
        TeamInfo(id=3, source_system_id="MG-00003", name="Developer"),
    ]


def test_list_team_leads_filters_by_team_leads_usergroup_membership():
    def handler(request):
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"token": "tok-1"})
        # /odata/Users is a distinct entity from /odata/Machines, matched by custom_id.
        assert request.url.path == "/odata/Users"
        params = request.url.params
        # No is_supervisor filter — that flag also covers QA/HR/Ticketing/Dev-management
        # supervisors who aren't team leads. userGroup is expanded so membership in the
        # "Team Leads" group (custom_id UG-00002) can be checked client-side instead.
        assert params["$filter"] == "is_active eq true"
        assert params["$select"] == "custom_id,name,email,username"
        assert params["$expand"] == "userGroup"
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "custom_id": "101088",
                        "name": "Rajib Ahamad",
                        "email": "rahamad@schertech.com",
                        "username": "ahamad",
                        "userGroup": [{"custom_id": "UG-00002", "name": "Team Leads"}],
                    },
                    # Supervisor of a different userGroup — must NOT be treated as a team
                    # lead just because is_supervisor would have been true for them.
                    {
                        "custom_id": "101009",
                        "name": "Hally Harald Gomes",
                        "email": "gharald@schertech.com",
                        "username": "gomes",
                        "userGroup": [{"custom_id": "UG-00004", "name": "HR Dhaka"}],
                    },
                    # No userGroup at all — must not blow up on a missing/empty list.
                    {
                        "custom_id": "101003",
                        "name": "Biprojit Roy",
                        "email": "bproy@schertech.com",
                        "username": "biprojit",
                        "userGroup": [],
                    },
                ]
            },
        )

    provider = _make_provider(handler)
    leads = provider.list_team_leads()

    assert leads == [
        TeamLeadInfo(source_system_id="101088", name="Rajib Ahamad", email="rahamad@schertech.com", username="ahamad")
    ]


def test_list_user_contacts_covers_everyone_active_with_no_supervisor_filter():
    def handler(request):
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"token": "tok-1"})
        assert request.url.path == "/odata/Users"
        params = request.url.params
        # Deliberately broader than list_team_leads() — no is_supervisor filter, since
        # this backfills contact info for every active person, not just supervisors.
        assert params["$filter"] == "is_active eq true"
        assert params["$select"] == "custom_id,name,email,username"
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "custom_id": "101003",
                        "name": "Biprojit Roy",
                        "email": "bproy@schertech.com",
                        "username": "biprojit",
                    },
                ]
            },
        )

    provider = _make_provider(handler)
    contacts = provider.list_user_contacts()

    assert contacts == [
        UserContactInfo(source_system_id="101003", name="Biprojit Roy", email="bproy@schertech.com", username="biprojit")
    ]


def test_rest_get_all_pages_via_take_skip_not_odata_style():
    all_rows = [{"id": i} for i in range(5)]

    def handler(request):
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"token": "tok-1"})
        # take/skip, not $top/$skip — this endpoint ignores OData-style params entirely.
        skip = int(request.url.params["skip"])
        take = int(request.url.params["take"])
        page = all_rows[skip : skip + take]
        return httpx.Response(200, json={"value": page, "@count": len(all_rows), "@total": len(all_rows)})

    provider = _make_provider(handler)
    rows = provider._rest_get_all("/planvisu/orders/list", {"status": "all"}, page_size=2)
    assert rows == all_rows


def _deployable_row(
    op_id,
    name,
    status_plan,
    order_id,
    task_id,
    pos="0040",
    order_name=None,
    customer_name=None,
    project_customer_name=None,
    item_custom_id=None,
    item_name=None,
    machine_custom_id=None,
    machine_name=None,
    due_date=None,
):
    return {
        "id": op_id,
        "name": name,
        "pos": pos,
        "status_plan": status_plan,
        "machine": ({"custom_id": machine_custom_id, "name": machine_name} if machine_custom_id else None),
        "prodOrderPos": {
            "due_date": due_date,
            "item": {"custom_id": item_custom_id, "name": item_name},
            "prodOrder": {
                "id": order_id,
                "custom_id": task_id,
                "name": order_name,
                "customer": {"name": customer_name} if customer_name else None,
                "source": {
                    "project": {"customer": {"name": project_customer_name}} if project_customer_name else None,
                },
            },
        },
    }


def test_list_deployable_tasks_extracts_planned_deploy_operations_by_name():
    def handler(request):
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"token": "tok-1"})
        assert request.url.path == "/api/get-orders"
        params = request.url.params
        assert params["halls"] == "5"
        assert params["machineGroups"] == "13"
        assert params["name"] == "deployment"
        return httpx.Response(
            200,
            json={
                "value": [
                    # pos deliberately not "0040" — matching must go by name, not by pos.
                    _deployable_row(
                        2060,
                        "Deployment Test system",
                        "PLANNED",
                        order_id=552,
                        task_id="PR-03045",
                        pos="0041",
                        order_name="Some order",
                        customer_name="VolaPlast GmbH & Co. KG",
                        item_custom_id="ReportV",
                        item_name="ReportVisu",
                        machine_custom_id="101088",
                        machine_name="Rajib Ahamad",
                        due_date="2026-08-04 00:00:00",
                    ),
                    _deployable_row(
                        2062,
                        "Deployment Live System",
                        "PLANNED",
                        order_id=552,
                        task_id="PR-03045",
                        pos="0061",
                        machine_custom_id="101088",
                        machine_name="Rajib Ahamad",
                    ),
                ],
                "@count": 2,
            },
        )

    provider = _make_provider(handler)
    tasks = provider.list_deployable_tasks()

    assert len(tasks) == 2
    test_task = next(t for t in tasks if t.target == "test")
    live_task = next(t for t in tasks if t.target == "live")

    assert test_task.operation_id == 2060
    assert test_task.order_id == 552
    assert test_task.task_id == "PR-03045"
    assert test_task.client_name == "VolaPlast GmbH & Co. KG"
    assert test_task.item_name == "ReportVisu"
    assert test_task.assigned_developer_custom_id == "101088"
    assert test_task.pos_id == "0041"  # informational only — not "0040"
    assert test_task.target_status == "PLANNED"

    assert live_task.operation_id == 2062
    assert live_task.pos_id == "0061"


def test_list_deployable_tasks_falls_back_to_source_project_customer():
    # Confirmed against the real /get-orders payload: order.customer is null on most
    # real orders — the client only shows up on order.source.project.customer instead.
    def handler(request):
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"token": "tok-1"})
        return httpx.Response(
            200,
            json={
                "value": [
                    _deployable_row(
                        1811,
                        "Deployment Test system",
                        "PLANNED",
                        order_id=514,
                        task_id="PR-03007",
                        customer_name=None,
                        project_customer_name="AGVS Aluminium Werke GmbH Villingen",
                    ),
                ],
                "@count": 1,
            },
        )

    provider = _make_provider(handler)
    tasks = provider.list_deployable_tasks()

    assert len(tasks) == 1
    assert tasks[0].client_name == "AGVS Aluminium Werke GmbH Villingen"


def test_list_deployable_tasks_skips_operations_that_are_not_planned():
    def handler(request):
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"token": "tok-1"})
        return httpx.Response(
            200,
            json={
                "value": [
                    _deployable_row(
                        2064, "Deployment Test system", "IN_PRODUCTION", order_id=553, task_id="PR-03046"
                    ),
                ],
                "@count": 1,
            },
        )

    provider = _make_provider(handler)
    assert provider.list_deployable_tasks() == []


def test_list_deployable_tasks_skips_rows_not_named_a_deploy_operation():
    # Defensive client-side check even though the server's own name filter should
    # already exclude these.
    def handler(request):
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"token": "tok-1"})
        return httpx.Response(
            200,
            json={
                "value": [
                    _deployable_row(2051, "QualiVisu Internal - WP1", "PLANNED", order_id=551, task_id="PR-03044"),
                ],
                "@count": 1,
            },
        )

    provider = _make_provider(handler)
    assert provider.list_deployable_tasks() == []


def test_list_deployable_tasks_handles_missing_customer_and_machine():
    def handler(request):
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"token": "tok-1"})
        return httpx.Response(
            200,
            json={
                "value": [
                    _deployable_row(2070, "Deployment Test system", "PLANNED", order_id=554, task_id="PR-03047"),
                ],
                "@count": 1,
            },
        )

    provider = _make_provider(handler)
    tasks = provider.list_deployable_tasks()

    assert len(tasks) == 1
    assert tasks[0].client_name is None
    assert tasks[0].assigned_developer_custom_id is None
    assert tasks[0].assigned_developer_name is None
