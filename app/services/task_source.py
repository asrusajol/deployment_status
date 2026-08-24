"""Adapter for the in-house task/ticket system (project_plan.md, Section 6).

Nothing else in the app should call the in-house API directly — always go through
a TaskSourceProvider so the real implementation can be swapped without touching
routers or models. There are two call patterns, used at different times:

  * get_task() — on-demand, per-request, when a developer enters a Task ID.
  * list_users() / list_teams() / list_clients() — bulk, called once a day by a scheduled
    job (see app/services/sync.py) to refresh the local User/Team/Client tables.

InHouseTaskSourceProvider is a stub until the real API's base URL, auth method, and
response shapes are available (see project_plan.md, Section 12 — open inputs needed).
"""

from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import Settings


@dataclass
class TaskInfo:
    task_id: str
    title: str
    client_name: str
    developer_email: str


@dataclass
class UserInfo:
    source_system_id: str  # Machine entity's custom_id
    name: str
    machine_group_id: int | None = None
    email: str | None = None  # not in the Machines feed yet — see app/models/user.py


@dataclass
class ClientInfo:
    source_system_id: str
    name: str


@dataclass
class TeamInfo:
    id: int  # MachineGroups' own numeric id — also what Machines.machine_group_id references
    source_system_id: str  # custom_id, e.g. "MG-00001"
    name: str


@dataclass
class TeamLeadInfo:
    source_system_id: str  # custom_id — matches the same field on UserInfo from Machines
    name: str
    email: str | None = None
    username: str | None = None


@dataclass
class UserContactInfo:
    source_system_id: str  # custom_id — matches the same field on UserInfo from Machines
    name: str
    email: str | None = None
    username: str | None = None


# Deploy operations are matched by name, not by "pos" code — pos codes shift between
# orders (confirmed by the user), but these names are the stable identifier. Matched
# case-insensitively since the CRM's own casing is inconsistent ("Deployment Test
# system" vs "Deployment Live System").
DEPLOY_OPERATION_TARGETS_BY_NAME = {
    "deployment test system": "test",
    "deployment live system": "live",
}

# The CRM's own custom_id for the "Team Leads" userGroup — confirmed by the user. This is
# what list_team_leads() now keys off, not is_supervisor: is_supervisor is a much broader
# flag also set for QA, HR, Ticketing, and Development Management supervisors who are not
# team leads (confirmed by inspecting live /odata/Users data).
TEAM_LEAD_USER_GROUP_CUSTOM_ID = "UG-00002"


@dataclass
class DeployableTaskInfo:
    """One deployable unit: a single "Deployment Test system" or "Deployment Live System"
    operation, matched by name — not by "pos" code.

    Sourced from /get-orders (see InHouseTaskSourceProvider.list_deployable_tasks()), which
    returns a flat, already-filtered (name=deployment) list of just these two operation
    types — unlike the earlier /planvisu/orders/list source, it does not include the
    preceding QA/gate operation at all, so there is deliberately no gate_status/is_ready
    concept here anymore (confirmed with the user after this endpoint switch): this is a
    flat list of currently-PLANNED deploy operations, not a readiness gate.

    task_id (the order's custom_id, e.g. "PR-03045") is what a developer references, but
    it's NOT guaranteed unique across orders — two distinct orders can share the same
    custom_id. order_id (the CRM's own internal order id) is what actually disambiguates
    them; operation_id is what disambiguates individual deploy operations and is what this
    table is keyed on (see DeployableTask), so this duplication never causes a data
    collision — it only matters for a human trying to tell two same-numbered orders apart.
    """

    operation_id: int  # the CRM's own operation id — used as our local PK, like Team.id
    order_id: int  # the CRM's own order id — disambiguates orders sharing the same task_id
    task_id: str  # order's custom_id, e.g. "PR-03045" — what a developer references
    order_name: str | None
    client_name: str | None  # order.customer.name, nullable — some orders have no customer
    item_custom_id: str | None
    item_name: str | None
    pos_id: str  # the CRM's raw pos code at import time — informational only, NOT stable
    target: str  # "test" or "live" — derived from the operation's name, not pos_id
    target_status: str  # the deploy operation's own status_plan — always "PLANNED" (filtered)
    assigned_developer_custom_id: str | None  # operation.machine.custom_id
    assigned_developer_name: str | None
    due_date: str | None  # prodOrderPos.due_date, for context/sorting


class TaskSourceProvider(Protocol):
    def get_task(self, task_id: str) -> TaskInfo | None: ...

    def search_tasks(self, query: str) -> list[TaskInfo]: ...

    def list_users(self) -> list[UserInfo]: ...

    def list_clients(self) -> list[ClientInfo]: ...

    def list_teams(self) -> list[TeamInfo]: ...

    def list_team_leads(self) -> list[TeamLeadInfo]: ...

    def list_user_contacts(self) -> list[UserContactInfo]: ...

    def list_deployable_tasks(self) -> list[DeployableTaskInfo]: ...


def _extract_token(payload: dict) -> str:
    token = payload.get("token")
    if not token:
        raise RuntimeError(
            "CRM API login response had no top-level 'token' field. Got keys: "
            f"{list(payload.keys())} — the response shape may have changed; "
            "update _extract_token() to match."
        )
    return token


class InHouseTaskSourceProvider:
    """Talks to the in-house CRM API at settings.task_api_base_url.

    Auth flow (confirmed): POST {base_url}/login with
    {username, password, ignorePermissions} returns {"token": "...", "user": {...}}.
    The token is sent as `Authorization: Bearer <token>` on every subsequent call.
    A token isn't persisted across process restarts — each run authenticates lazily,
    once, on first use, and again automatically if a call comes back 401.

    The API has two separate path roots under the same host (confirmed by testing
    against the real system): auth/REST endpoints live under {base_url} (e.g.
    {base_url}/login), but OData endpoints live at the domain root, one level up
    (e.g. {base_url minus "/api"}/odata/Machines, NOT {base_url}/odata/Machines).
    _odata_get_all() builds an absolute URL for that reason instead of a relative
    path — httpx uses an absolute URL as-is and ignores the client's base_url.

    A third, unrelated pagination style shows up in /planvisu/orders/list: not OData at
    all, plain `take`/`skip` query params, under the same {base_url} REST root as /login
    (confirmed empirically — `page`/`offset`/other common names are silently ignored by
    this endpoint, only take+skip works). This is no longer used by list_deployable_tasks()
    (see below) but _rest_get_all() is kept in case another endpoint needs it.

    A fourth style is used by /get-orders (used by list_deployable_tasks()): OData-style
    `$top`/`$skip`/`$orderby` params, but under {base_url} (like /login), not the domain
    root (unlike Machines/Users/MachineGroups) — see _rest_odata_get_all().

    get_task / search_tasks / list_clients still need real endpoint paths and
    response shapes from the CRM API before they can be implemented — see
    project_plan.md, Section 12. list_users(), list_teams(), list_team_leads(),
    list_user_contacts(), and list_deployable_tasks() are done.
    """

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        if not settings.task_api_base_url:
            raise RuntimeError(
                "task_api_base_url is not configured. Set it in .env once the in-house "
                "task API's base URL is available (project_plan.md, Section 12)."
            )
        self._username = settings.task_api_username
        self._password = settings.task_api_password
        self._ignore_permissions = settings.task_api_ignore_permissions
        self._deployable_hall_id = settings.task_api_deployable_hall_id
        self._deployable_machine_group_id = settings.task_api_deployable_machine_group_id
        base_url = settings.task_api_base_url.rstrip("/")
        self._client = client or httpx.Client(base_url=base_url, timeout=10.0)
        self._odata_base_url = base_url[: -len("/api")] if base_url.endswith("/api") else base_url
        self._token: str | None = None

    def _login(self) -> str:
        response = self._client.post(
            "/login",
            json={
                "username": self._username,
                "password": self._password,
                "ignorePermissions": self._ignore_permissions,
            },
        )
        response.raise_for_status()
        return _extract_token(response.json())

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if self._token is None:
            self._token = self._login()
        response = self._client.request(method, path, headers=self._auth_header(), **kwargs)
        if response.status_code == 401:
            # Token likely expired — re-authenticate once and retry before giving up.
            self._token = self._login()
            response = self._client.request(method, path, headers=self._auth_header(), **kwargs)
        response.raise_for_status()
        return response

    def _auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _odata_get_all(self, path: str, params: dict, page_size: int = 100) -> list[dict]:
        """Page through an OData $top/$skip listing and return every 'value' row.

        Stops once a page comes back shorter than page_size, rather than trusting an
        @odata.count-style field — that field's exact name isn't confirmed yet, and
        page-length is true regardless of which OData count convention the API uses.

        Uses self._odata_base_url (domain root), not self._client's own base_url
        (which points at /api) — see the class docstring for why they differ.
        """
        items: list[dict] = []
        skip = 0
        url = f"{self._odata_base_url}{path}"
        while True:
            page_params = {**params, "$top": page_size, "$skip": skip}
            response = self._request("GET", url, params=page_params)
            page = response.json().get("value", [])
            items.extend(page)
            if len(page) < page_size:
                return items
            skip += page_size

    def _rest_get_all(self, path: str, params: dict, page_size: int = 40) -> list[dict]:
        """Page through a take/skip listing under {base_url} (not OData) and return every 'value' row.

        Distinct from _odata_get_all(): relative path (uses self._client's own base_url
        directly, since this lives under /api like /login does, not at the domain root),
        and take/skip instead of $top/$skip — confirmed empirically against the real
        endpoint (page/offset/other common names are silently ignored; only this works).
        """
        items: list[dict] = []
        skip = 0
        while True:
            page_params = {**params, "take": page_size, "skip": skip}
            response = self._request("GET", path, params=page_params)
            page = response.json().get("value", [])
            items.extend(page)
            if len(page) < page_size:
                return items
            skip += page_size

    def _rest_odata_get_all(self, path: str, params: dict, page_size: int = 100) -> list[dict]:
        """Page through a $top/$skip listing under {base_url} (not domain root) and return every 'value' row.

        Distinct from _odata_get_all() (domain root) and _rest_get_all() (take/skip):
        /get-orders lives under {base_url} like /login, but takes OData-style $top/$skip
        params like the domain-root endpoints do — confirmed against the real endpoint.
        """
        items: list[dict] = []
        skip = 0
        while True:
            page_params = {**params, "$top": page_size, "$skip": skip}
            response = self._request("GET", path, params=page_params)
            page = response.json().get("value", [])
            items.extend(page)
            if len(page) < page_size:
                return items
            skip += page_size

    def get_task(self, task_id: str) -> TaskInfo | None:
        raise NotImplementedError(
            "Need the real task-lookup endpoint path + response shape from the CRM API "
            "(project_plan.md, Section 12) — auth is wired up and ready to use via self._request()."
        )

    def search_tasks(self, query: str) -> list[TaskInfo]:
        raise NotImplementedError(
            "Need the real task-search endpoint path + response shape from the CRM API."
        )

    def list_users(self) -> list[UserInfo]:
        # Employees are modeled as Machine entities in this system (confirmed by the user).
        # $filter excludes inactive/terminated employees; $select keeps the payload to just
        # the fields we currently use, rather than the full $expand chain in the original example.
        rows = self._odata_get_all(
            "/odata/Machines",
            {
                "$filter": "is_active eq true",
                "$select": "custom_id,name,machine_group_id",
                "$orderby": "custom_id asc",
            },
        )
        return [
            UserInfo(
                source_system_id=str(row["custom_id"]),
                name=row["name"],
                machine_group_id=row.get("machine_group_id"),
            )
            for row in rows
        ]

    def list_clients(self) -> list[ClientInfo]:
        raise NotImplementedError(
            "Need the real client-list endpoint path + response shape from the CRM API."
        )

    def list_teams(self) -> list[TeamInfo]:
        # Deliberately no is_active filter, unlike list_users(): users.machine_group_id
        # is a raw id with no DB-level FK (see app/models/user.py), but we still want every
        # team a user's machine_group_id could possibly point at to resolve — including
        # ones that have since gone inactive — rather than silently excluding them here.
        rows = self._odata_get_all(
            "/odata/MachineGroups",
            {
                "$select": "id,custom_id,name",
                "$orderby": "custom_id asc",
            },
        )
        return [
            TeamInfo(id=row["id"], source_system_id=str(row["custom_id"]), name=row["name"])
            for row in rows
        ]

    def list_team_leads(self) -> list[TeamLeadInfo]:
        # This is a distinct Users entity from Machines — same underlying people (matched
        # by custom_id), but this feed carries email/username, which Machines doesn't.
        #
        # Team Lead status comes from membership in the "Team Leads" userGroup (custom_id
        # UG-00002), not is_supervisor — is_supervisor is set for QA, HR, Ticketing, and
        # Development Management supervisors too, not just team leads (confirmed by the
        # user, and by inspecting live data: e.g. a user in the "HR Dhaka" userGroup has
        # is_supervisor=true but is not a team lead). $expand pulls in each user's
        # userGroup memberships so we can filter on custom_id client-side — there's no
        # evidence this OData server supports server-side any()/lambda filtering on
        # expanded navigation properties, so this is deliberately client-side rather than
        # a fancier $filter expression.
        rows = self._odata_get_all(
            "/odata/Users",
            {
                "$filter": "is_active eq true",
                "$select": "custom_id,name,email,username",
                "$expand": "userGroup",
            },
        )
        return [
            TeamLeadInfo(
                source_system_id=str(row["custom_id"]),
                name=row["name"],
                email=row.get("email"),
                username=row.get("username"),
            )
            for row in rows
            if any(
                group.get("custom_id") == TEAM_LEAD_USER_GROUP_CUSTOM_ID
                for group in (row.get("userGroup") or [])
            )
        ]

    def list_user_contacts(self) -> list[UserContactInfo]:
        # Same /odata/Users entity as list_team_leads(), but deliberately no is_supervisor
        # filter here — this covers every active person (developers, QA, DevOps, team
        # leads alike), since Machines carries neither email nor username for anyone.
        # This is purely a contact-info backfill; it never touches role — see
        # sync_user_contacts() in app/services/sync.py for why that's kept separate
        # from the supervisor-driven promotion in sync_team_leads().
        rows = self._odata_get_all(
            "/odata/Users",
            {
                "$filter": "is_active eq true",
                "$select": "custom_id,name,email,username",
            },
        )
        return [
            UserContactInfo(
                source_system_id=str(row["custom_id"]),
                name=row["name"],
                email=row.get("email"),
                username=row.get("username"),
            )
            for row in rows
        ]

    def list_deployable_tasks(self) -> list[DeployableTaskInfo]:
        # /get-orders returns a flat list of operations, already scoped server-side to
        # this team's hall + machine group (name=deployment is sent too, but is no longer
        # relied on as a hard filter — see below). Unlike the earlier /planvisu/orders/list
        # source, there is no nested operations-per-position list here, so the preceding
        # QA/gate operation isn't available at all — this is a flat "currently planned
        # deploy operations" list, not a readiness gate (confirmed with the user after
        # this endpoint switch).
        #
        # We filter to status_plan == "PLANNED" ourselves rather than trust the params
        # alone, since matching client-side is cheap and doesn't depend on the server
        # filter staying exactly as observed.
        rows = self._rest_odata_get_all(
            "/get-orders",
            {
                "halls": self._deployable_hall_id,
                "machineGroups": self._deployable_machine_group_id,
                "active": "true",
                "request_from": "task_view",
                "elapsedTime": "true",
                "name": "deployment",
                "$orderby": "id desc",
                "$count": "true",
            },
        )

        results: list[DeployableTaskInfo] = []
        for row in rows:
            if row.get("status_plan") != "PLANNED":
                continue
            # No longer filtering rows out for not matching a known deploy-operation name —
            # the hall/machineGroup server-side scope plus the PLANNED check above are the
            # only filters now (confirmed with the user: real /get-orders data isn't reliably
            # limited to just "Deployment Test system"/"Deployment Live System", so the old
            # name-based filter was silently dropping legitimate rows). The name is still used
            # to classify test/live when it matches a known pattern; anything else falls back
            # to "unknown" rather than being excluded.
            target = DEPLOY_OPERATION_TARGETS_BY_NAME.get((row.get("name") or "").strip().lower(), "unknown")
            position = row.get("prodOrderPos") or {}
            order = position.get("prodOrder") or {}
            # order.customer is null on most real orders (confirmed against live /get-orders
            # data) — the client only shows up on order.customer for a minority of rows.
            # For the rest, it's one level deeper: order.source.project.customer, since
            # source is the originating CRM task and project is what actually carries the
            # client relationship.
            source = order.get("source") or {}
            project = source.get("project") or {}
            customer = order.get("customer") or project.get("customer") or {}
            item = position.get("item") or {}
            machine = row.get("machine") or {}
            results.append(
                DeployableTaskInfo(
                    operation_id=row["id"],
                    order_id=order["id"],
                    task_id=str(order["custom_id"]),
                    order_name=order.get("name"),
                    client_name=customer.get("name") if customer else None,
                    item_custom_id=item.get("custom_id"),
                    item_name=item.get("name"),
                    pos_id=row.get("pos", ""),
                    target=target,
                    target_status=row.get("status_plan", ""),
                    assigned_developer_custom_id=machine.get("custom_id") if machine else None,
                    assigned_developer_name=machine.get("name") if machine else None,
                    due_date=position.get("due_date"),
                )
            )
        return results
