# Order Task Dependency Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Reports tab to the Deployment Tracker whose first report — Order Task Dependency — shows, per production order, which operations are blocked by an unfinished predecessor and which are overdue, with HTML, Excel and PDF output.

**Architecture:** The CRM's pre-computed version of this report exists only on a shopfloor-suite feature branch that will never reach live, so this app reads the raw OData entity sets (available on both test and live) and computes the dependency/overdue logic itself in Python. A thin adapter layer fetches rows, a pure module computes the report, and three routes render it.

**Tech Stack:** FastAPI, SQLAlchemy, Jinja2, httpx, openpyxl, WeasyPrint (new), pytest.

**Spec:** `docs/superpowers/specs/2026-09-09-order-task-dependency-report-design.md`

## Global Constraints

- **Never filter through an OData navigation path.** `machine/machine_group_id eq X` returns HTTP 200 and *wrong rows* (it silently resolves to the operation's own `machine_group_id` column, which differs from the machine's real group in production data). `prodOrderPos/due_date ge X` is an outright parser error. Filter only on an entity's own fields; resolve everything else in Python.
- **An OData error arrives as HTTP 200 with a corrupt body** containing an `OData-error` marker, so `raise_for_status()` cannot see it and `.json()` raises `JSONDecodeError`. Every OData read must go through the error-detecting helper built in Task 1.
- **`$count` is unsupported.** Page with `$top`/`$skip` + `$orderby=id` until a short page.
- **No magic strings for domain values.** Operation statuses go through the `OperationStatus` enum (Task 3). Never write `"CLOSED"` or `"DELETED"` as a bare literal, tests included.
- **Roles allowed on every `/reports*` route:** `admin`, `devops`, `team_lead`. `developer` gets 403.
- **Store/compute UTC; format at the output boundary only.** Match the existing `"%Y-%m-%d %H:%M UTC"` style used in `app/services/export.py`.
- **Existing style:** NgModule-free, plain FastAPI routers registered in `app/main.py`; Jinja templates in `app/templates/`; tests use the `client` / `web` fixtures and `login_as` from `tests/conftest.py`.
- **Never run a whole-file formatter** over files you edit. Touch only the lines you add or change.
- **Do not `git push`.** Commit locally only.

---

### Task 1: Detect OData errors that masquerade as HTTP 200

**Files:**
- Modify: `app/services/task_source.py` (add exception + helper; route `_odata_get_all` through it)
- Test: `tests/test_task_source.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `class OdataError(RuntimeError)` and `_odata_json(response: httpx.Response) -> dict`, used by every OData read in Task 2.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_task_source.py`:

```python
from app.services.task_source import OdataError, _odata_json


def test_odata_json_raises_on_error_marker_inside_a_200():
    # The real CRM returns 200 + application/json with an OData-error blob spliced
    # into the middle of the body, so raise_for_status() sees nothing wrong.
    body = (
        '{"@context":"http://crm.test.local/odata/$metadata#ProdOrderPosOperations",'
        '"value":[OData-error: {"code":"expression_parser_error",'
        '"message":"Encountered an invalid symbol at: >b<ogus eq 1"}'
    )
    response = httpx.Response(200, text=body, headers={"content-type": "application/json"})

    with pytest.raises(OdataError, match="expression_parser_error"):
        _odata_json(response)


def test_odata_json_raises_on_unparseable_body_without_a_marker():
    response = httpx.Response(200, text="<html>gateway timeout</html>")

    with pytest.raises(OdataError, match="could not be parsed"):
        _odata_json(response)


def test_odata_json_returns_the_payload_when_healthy():
    response = httpx.Response(200, json={"value": [{"id": 1}]})

    assert _odata_json(response) == {"value": [{"id": 1}]}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/abubakkar/Desktop/Versions/codebase/deployment_status && .venv/bin/python -m pytest tests/test_task_source.py -k odata_json -v`
Expected: FAIL with `ImportError: cannot import name 'OdataError'`

- [ ] **Step 3: Implement the helper**

In `app/services/task_source.py`, after `_extract_token()`:

```python
class OdataError(RuntimeError):
    """An OData request failed.

    The CRM reports OData failures as HTTP 200 with `application/json` and an
    "OData-error" blob spliced into the middle of the body, which leaves the JSON
    unparseable — raise_for_status() sees a perfectly good response. Every OData read
    goes through _odata_json() so a bad query surfaces as this exception instead of an
    empty result set (a silently empty report is indistinguishable from "no data").
    """


def _odata_json(response: httpx.Response) -> dict:
    text = response.text
    if "OData-error" in text:
        detail = text[text.index("OData-error") :][:300]
        raise OdataError(f"CRM returned an OData error: {detail}")
    try:
        return response.json()
    except ValueError as exc:
        raise OdataError(f"CRM response could not be parsed as JSON: {text[:200]!r}") from exc
```

- [ ] **Step 4: Route the existing pager through it**

In `_odata_get_all()`, replace the single line

```python
            page = response.json().get("value", [])
```

with

```python
            page = _odata_json(response).get("value", [])
```

(This is the same latent silent-failure risk in the code the new report builds on — one line, same behaviour on success.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_task_source.py -v`
Expected: PASS, including the pre-existing task_source tests.

- [ ] **Step 6: Commit**

```bash
git add app/services/task_source.py tests/test_task_source.py
git commit -m "Detect OData errors returned as HTTP 200 with a corrupt body"
```

---

### Task 2: Adapter methods that fetch the report's raw rows

**Files:**
- Modify: `app/services/task_source.py`
- Test: `tests/test_task_source.py`

**Interfaces:**
- Consumes: `_odata_json`, `_odata_get_all`, `_request` (Task 1 / existing).
- Produces, all on `InHouseTaskSourceProvider`:
  - `list_machines() -> list[MachineInfo]`
  - `list_operations_started_before(end: datetime, machine_ids: list[int] | None) -> list[OperationInfo]`
  - `list_positions_by_id(position_ids: list[int]) -> list[PositionInfo]`
  - `list_positions_by_order(order_ids: list[int]) -> list[PositionInfo]`
  - `list_operations_by_position(position_ids: list[int]) -> list[OperationInfo]`
  - `list_orders_by_id(order_ids: list[int]) -> list[OrderInfo]`
  - and the dataclasses `MachineInfo`, `OperationInfo`, `PositionInfo`, `OrderInfo`.

**Note on the `TaskSourceProvider` Protocol:** do *not* add these six methods to it. The Protocol describes the task-sourcing contract that `sync.py` depends on; these are report reads with a different lifecycle, and widening the Protocol would force every future test double to stub them. The concrete provider is passed directly.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_task_source.py`:

```python
from app.services.task_source import MachineInfo, OperationInfo, OrderInfo, PositionInfo


def _odata_handler(routes):
    """Build a MockTransport handler serving /login plus a {path: [rows]} map."""

    def handler(request):
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"token": "1|fake"})
        rows = routes.get(request.url.path)
        if rows is None:
            raise AssertionError(f"unexpected path {request.url.path} ({request.url.query!r})")
        skip = int(request.url.params.get("$skip", 0))
        top = int(request.url.params.get("$top", 100))
        return httpx.Response(200, json={"value": rows[skip : skip + top]})

    return handler


def test_list_machines_returns_the_group_mapping():
    handler = _odata_handler(
        {"/odata/Machines": [{"id": 44, "name": "Rajib", "custom_id": "101088", "machine_group_id": 13}]}
    )

    machines = _make_provider(handler).list_machines()

    assert machines == [MachineInfo(id=44, name="Rajib", custom_id="101088", machine_group_id=13)]


def test_list_operations_filters_on_own_fields_only_and_never_a_nav_path():
    captured = {}

    def handler(request):
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"token": "1|fake"})
        captured["filter"] = request.url.params.get("$filter")
        return httpx.Response(200, json={"value": []})

    provider = _make_provider(handler)
    provider.list_operations_started_before(datetime(2026, 9, 30, tzinfo=timezone.utc), machine_ids=[44, 45])

    flt = captured["filter"]
    assert "start lt 2026-09-30T00:00:00Z" in flt
    assert "status ne 'DELETED'" in flt
    assert "machine_id in (44,45)" in flt
    # The trap: filtering through a nav path silently returns the wrong rows.
    assert "machine/" not in flt
    assert "prodOrderPos/" not in flt


def test_list_operations_omits_the_machine_clause_when_no_group_filter():
    captured = {}

    def handler(request):
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"token": "1|fake"})
        captured["filter"] = request.url.params.get("$filter")
        return httpx.Response(200, json={"value": []})

    provider = _make_provider(handler)
    provider.list_operations_started_before(datetime(2026, 9, 30, tzinfo=timezone.utc), machine_ids=None)

    assert "machine_id in" not in captured["filter"]


def test_list_operations_maps_rows_to_operation_info():
    handler = _odata_handler(
        {
            "/odata/ProdOrderPosOperations": [
                {
                    "id": 3363,
                    "prod_order_pos_id": 12,
                    "pos": "0070",
                    "name": "Deployment Test system",
                    "status": "PLANNED",
                    "start": "2026-09-03T04:00:00+00:00",
                    "end": None,
                    "machine_id": 44,
                }
            ]
        }
    )

    operations = _make_provider(handler).list_operations_started_before(
        datetime(2026, 9, 30, tzinfo=timezone.utc), machine_ids=None
    )

    assert operations == [
        OperationInfo(
            id=3363,
            prod_order_pos_id=12,
            pos="0070",
            name="Deployment Test system",
            status="PLANNED",
            start="2026-09-03T04:00:00+00:00",
            end=None,
            machine_id=44,
        )
    ]


def test_list_positions_by_id_batches_the_in_clause():
    seen = []

    def handler(request):
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"token": "1|fake"})
        seen.append(request.url.params.get("$filter"))
        return httpx.Response(200, json={"value": []})

    provider = _make_provider(handler)
    provider.list_positions_by_id(list(range(1, 251)), batch_size=100)

    assert len(seen) == 3
    assert seen[0].startswith("id in (1,2,")
    assert "250" in seen[-1]


def test_list_positions_by_id_returns_nothing_without_querying_for_an_empty_input():
    def handler(request):
        raise AssertionError("should not have issued a request")

    provider = _make_provider(handler)
    assert provider.list_positions_by_id([]) == []


def test_list_positions_by_order_maps_rows():
    handler = _odata_handler(
        {
            "/odata/ProdOrderPos": [
                {"id": 12, "prod_order_id": 11, "name": "Deployment Volaplast", "due_date": "2026-04-06T00:00:00+00:00"}
            ]
        }
    )

    positions = _make_provider(handler).list_positions_by_order([11])

    assert positions == [
        PositionInfo(id=12, prod_order_id=11, name="Deployment Volaplast", due_date="2026-04-06T00:00:00+00:00")
    ]


def test_list_orders_by_id_maps_rows():
    handler = _odata_handler({"/odata/ProdOrders": [{"id": 11, "custom_id": "PR-00001"}]})

    assert _make_provider(handler).list_orders_by_id([11]) == [OrderInfo(id=11, custom_id="PR-00001")]
```

Add these imports at the top of the test file if absent:

```python
from datetime import datetime, timezone
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_task_source.py -k "machines or operations or positions or orders" -v`
Expected: FAIL with `ImportError: cannot import name 'MachineInfo'`

- [ ] **Step 3: Add the dataclasses**

In `app/services/task_source.py`, next to the other dataclasses:

```python
@dataclass(frozen=True)
class MachineInfo:
    """A CRM Machine, used only for its machine_group_id.

    The machine's OWN group is the one that matters: an operation also carries a
    machine_group_id column, and the two genuinely differ in real data — filtering
    through the nav path `machine/machine_group_id` silently returns the operation's
    column instead, which is why group filtering resolves machine ids here first.
    """

    id: int
    name: str | None
    custom_id: str | None
    machine_group_id: int | None


@dataclass(frozen=True)
class OperationInfo:
    id: int
    prod_order_pos_id: int | None
    pos: str | None
    name: str | None
    status: str | None
    start: str | None  # UTC ISO-8601 as returned by the CRM
    end: str | None
    machine_id: int | None


@dataclass(frozen=True)
class PositionInfo:
    id: int
    prod_order_id: int | None
    name: str | None
    due_date: str | None


@dataclass(frozen=True)
class OrderInfo:
    id: int
    custom_id: str | None
```

- [ ] **Step 4: Add the fetch methods**

Append to `InHouseTaskSourceProvider`:

```python
    # --- Order Task Dependency report ---------------------------------------
    #
    # Only ever filters on an entity's OWN fields. Nav-path filters are unusable here:
    # `prodOrderPos/due_date ge ...` is a parser error, and `machine/machine_group_id`
    # silently resolves to the operation's own column and returns the wrong rows.
    # See docs/superpowers/specs/2026-09-09-order-task-dependency-report-design.md.

    _OPERATION_SELECT = "id,prod_order_pos_id,pos,name,status,start,end,machine_id"
    _POSITION_SELECT = "id,prod_order_id,name,due_date"

    def list_machines(self) -> list[MachineInfo]:
        rows = self._odata_get_all(
            "/odata/Machines",
            {"$select": "id,name,custom_id,machine_group_id", "$orderby": "id"},
            page_size=200,
        )
        return [
            MachineInfo(
                id=row["id"],
                name=row.get("name"),
                custom_id=row.get("custom_id"),
                machine_group_id=row.get("machine_group_id"),
            )
            for row in rows
        ]

    def list_operations_started_before(
        self, end: datetime, machine_ids: list[int] | None
    ) -> list[OperationInfo]:
        clauses = [
            f"start lt {end.strftime('%Y-%m-%dT%H:%M:%SZ')}",
            f"status ne '{ProdOrderPosOperationStatus.DELETED.value}'",
        ]
        if machine_ids is not None:
            if not machine_ids:
                return []
            clauses.append(f"machine_id in ({','.join(str(i) for i in machine_ids)})")
        rows = self._odata_get_all(
            "/odata/ProdOrderPosOperations",
            {"$select": self._OPERATION_SELECT, "$filter": " and ".join(clauses), "$orderby": "id"},
            page_size=200,
        )
        return [self._operation(row) for row in rows]

    def list_operations_by_position(self, position_ids: list[int]) -> list[OperationInfo]:
        rows = self._in_batches(
            "/odata/ProdOrderPosOperations",
            "prod_order_pos_id",
            position_ids,
            self._OPERATION_SELECT,
            extra_clause=f"status ne '{ProdOrderPosOperationStatus.DELETED.value}'",
        )
        return [self._operation(row) for row in rows]

    def list_positions_by_id(self, position_ids: list[int], batch_size: int = 100) -> list[PositionInfo]:
        rows = self._in_batches(
            "/odata/ProdOrderPos", "id", position_ids, self._POSITION_SELECT, batch_size=batch_size
        )
        return [self._position(row) for row in rows]

    def list_positions_by_order(self, order_ids: list[int]) -> list[PositionInfo]:
        rows = self._in_batches("/odata/ProdOrderPos", "prod_order_id", order_ids, self._POSITION_SELECT)
        return [self._position(row) for row in rows]

    def list_orders_by_id(self, order_ids: list[int]) -> list[OrderInfo]:
        rows = self._in_batches("/odata/ProdOrders", "id", order_ids, "id,custom_id")
        return [OrderInfo(id=row["id"], custom_id=row.get("custom_id")) for row in rows]

    def _in_batches(
        self,
        path: str,
        field: str,
        ids: list[int],
        select: str,
        extra_clause: str | None = None,
        batch_size: int = 100,
    ) -> list[dict]:
        """Fetch `path` where `field` is in `ids`, in url-length-safe batches.

        The `in (...)` operator is supported here (unlike nav-path filters); batching is
        only about keeping the query string sane, not a server limitation.
        """
        rows: list[dict] = []
        unique = sorted(set(ids))
        for offset in range(0, len(unique), batch_size):
            chunk = unique[offset : offset + batch_size]
            clause = f"{field} in ({','.join(str(i) for i in chunk)})"
            if extra_clause:
                clause = f"{clause} and {extra_clause}"
            rows.extend(
                self._odata_get_all(path, {"$select": select, "$filter": clause, "$orderby": "id"}, page_size=200)
            )
        return rows

    @staticmethod
    def _operation(row: dict) -> OperationInfo:
        return OperationInfo(
            id=row["id"],
            prod_order_pos_id=row.get("prod_order_pos_id"),
            pos=row.get("pos"),
            name=row.get("name"),
            status=row.get("status"),
            start=row.get("start"),
            end=row.get("end"),
            machine_id=row.get("machine_id"),
        )

    @staticmethod
    def _position(row: dict) -> PositionInfo:
        return PositionInfo(
            id=row["id"],
            prod_order_id=row.get("prod_order_id"),
            name=row.get("name"),
            due_date=row.get("due_date"),
        )
```

Add to the imports at the top of `app/services/task_source.py`:

```python
from datetime import datetime

from app.enums import ProdOrderPosOperationStatus
```

- [ ] **Step 5: Create the status enum the adapter references**

Create `app/enums.py`:

```python
"""Domain enums mirrored from the CRM.

Hand-maintained to match the CRM's own values — there is no generator. Only the values
this app actually compares against are listed; add a member rather than writing a bare
string literal anywhere.
"""

import enum


class ProdOrderPosOperationStatus(str, enum.Enum):
    """Subset of the CRM's ProdOrderPosOperationStatus used by the dependency report.

    CLOSED is what makes a predecessor non-blocking and an operation non-overdue;
    DELETED rows are excluded everywhere.
    """

    CLOSED = "CLOSED"
    DELETED = "DELETED"
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_task_source.py -v`
Expected: PASS (all, old and new)

- [ ] **Step 7: Commit**

```bash
git add app/services/task_source.py app/enums.py tests/test_task_source.py
git commit -m "Fetch order/position/operation/machine rows for the dependency report"
```

---

### Task 3: The dependency + overdue computation (pure)

**Files:**
- Create: `app/services/order_task_dependency.py`
- Test: `tests/test_order_task_dependency.py`

**Interfaces:**
- Consumes: `MachineInfo`, `OperationInfo`, `PositionInfo`, `OrderInfo` (Task 2); `ProdOrderPosOperationStatus` (Task 2).
- Produces:
  - `@dataclass TaskRow` with fields `operation_id, order_id, order_custom_id, prod_order_pos_id, item_name, pos, name, machine_name, machine_group_id, machine_group_name, status, start, end, due_date, has_dependency, is_blocked, blocked_by, is_overdue`
  - `@dataclass OrderGroup(order_id, order_custom_id, tasks: list[TaskRow])`
  - `compute_order_groups(operations, positions, orders, machines, machine_group_names, today) -> list[OrderGroup]`
  - `filter_overdue_eligible(groups, machine_group_ids, overdue_only) -> list[OrderGroup]`
  - `flatten(groups) -> list[TaskRow]`

This is a port of shopfloor-suite's `OrderTaskDependencyReportService::mapOrder()`; the tests below are the contract.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_order_task_dependency.py`:

```python
from datetime import date

from app.enums import ProdOrderPosOperationStatus as Status
from app.services.order_task_dependency import (
    compute_order_groups,
    filter_overdue_eligible,
    flatten,
)
from app.services.task_source import MachineInfo, OperationInfo, OrderInfo, PositionInfo

TODAY = date(2026, 9, 10)
FUTURE = "2026-12-01T00:00:00+00:00"
PAST = "2026-01-01T00:00:00+00:00"


def op(id, pos, status, *, position=12, name=None, machine_id=44):
    return OperationInfo(
        id=id,
        prod_order_pos_id=position,
        pos=pos,
        name=name or f"op-{id}",
        status=status,
        start="2026-09-01T00:00:00+00:00",
        end=None,
        machine_id=machine_id,
    )


def positions(due_date=FUTURE, id=12, order=11, name="Item A"):
    return [PositionInfo(id=id, prod_order_id=order, name=name, due_date=due_date)]


ORDERS = [OrderInfo(id=11, custom_id="PR-00001")]
MACHINES = [MachineInfo(id=44, name="M1", custom_id="101088", machine_group_id=13)]
GROUP_NAMES = {13: "Team Rajib"}


def compute(operations, position_rows=None, orders=None):
    return compute_order_groups(
        operations=operations,
        positions=position_rows if position_rows is not None else positions(),
        orders=orders if orders is not None else ORDERS,
        machines=MACHINES,
        machine_group_names=GROUP_NAMES,
        today=TODAY,
    )


def test_first_operation_in_a_position_has_no_dependency():
    tasks = flatten(compute([op(1, "0010", "PLANNED")]))

    assert tasks[0].has_dependency is False
    assert tasks[0].is_blocked is False
    assert tasks[0].blocked_by is None


def test_successor_of_an_unfinished_predecessor_is_blocked_by_it():
    tasks = flatten(compute([op(1, "0010", "PLANNED", name="Cutting"), op(2, "0020", "PLANNED")]))

    assert tasks[1].has_dependency is True
    assert tasks[1].is_blocked is True
    assert tasks[1].blocked_by == "Cutting"


def test_successor_of_a_closed_predecessor_is_not_blocked():
    tasks = flatten(compute([op(1, "0010", Status.CLOSED.value), op(2, "0020", "PLANNED")]))

    assert tasks[1].has_dependency is True
    assert tasks[1].is_blocked is False
    assert tasks[1].blocked_by is None


def test_operations_are_sequenced_numerically_not_lexically():
    # "0100" must come after "0090"; string ordering would put it first.
    tasks = flatten(compute([op(2, "0100", "PLANNED"), op(1, "0090", "PLANNED", name="Ninety")]))

    assert [t.pos for t in tasks] == ["0090", "0100"]
    assert tasks[1].blocked_by == "Ninety"


def test_each_position_sequences_independently():
    operations = [
        op(1, "0010", "PLANNED", position=12),
        op(2, "0010", "PLANNED", position=13),
    ]
    rows = [
        PositionInfo(id=12, prod_order_id=11, name="Item A", due_date=FUTURE),
        PositionInfo(id=13, prod_order_id=11, name="Item B", due_date=FUTURE),
    ]

    tasks = flatten(compute(operations, position_rows=rows))

    assert [t.has_dependency for t in tasks] == [False, False]


def test_deleted_operations_are_excluded():
    tasks = flatten(compute([op(1, "0010", Status.DELETED.value), op(2, "0020", "PLANNED")]))

    assert [t.operation_id for t in tasks] == [2]
    # With the DELETED row gone, op 2 is now first in sequence and depends on nothing.
    assert tasks[0].has_dependency is False


def test_overdue_requires_a_past_due_date_and_an_unfinished_operation():
    overdue = flatten(compute([op(1, "0010", "PLANNED")], position_rows=positions(due_date=PAST)))
    # The CLOSED operation needs an open sibling: an order whose tasks are ALL closed is
    # dropped wholesale, so a lone closed task would leave nothing to assert against.
    mixed = flatten(
        compute(
            [op(1, "0010", Status.CLOSED.value), op(2, "0020", "PLANNED")],
            position_rows=positions(due_date=PAST),
        )
    )
    future = flatten(compute([op(1, "0010", "PLANNED")], position_rows=positions(due_date=FUTURE)))

    assert overdue[0].is_overdue is True
    assert mixed[0].is_overdue is False  # closed is never overdue, even past its due date
    assert mixed[1].is_overdue is True  # open and past due
    assert future[0].is_overdue is False


def test_an_order_with_a_single_closed_task_is_dropped_like_any_other_all_closed_order():
    # Guards against special-casing the one-task case out of the all-closed rule.
    assert compute([op(1, "0010", Status.CLOSED.value)]) == []


def test_a_null_due_date_is_never_overdue():
    tasks = flatten(compute([op(1, "0010", "PLANNED")], position_rows=positions(due_date=None)))

    assert tasks[0].is_overdue is False


def test_an_order_whose_tasks_are_all_closed_is_dropped():
    groups = compute([op(1, "0010", Status.CLOSED.value), op(2, "0020", Status.CLOSED.value)])

    assert groups == []


def test_machine_and_group_are_resolved_from_the_machine_not_the_operation():
    tasks = flatten(compute([op(1, "0010", "PLANNED", machine_id=44)]))

    assert tasks[0].machine_name == "M1"
    assert tasks[0].machine_group_id == 13
    assert tasks[0].machine_group_name == "Team Rajib"


def test_an_operation_without_a_machine_still_renders():
    tasks = flatten(compute([op(1, "0010", "PLANNED", machine_id=None)]))

    assert tasks[0].machine_name is None
    assert tasks[0].machine_group_id is None


def test_orders_are_ordered_by_custom_id():
    operations = [op(1, "0010", "PLANNED", position=12), op(2, "0010", "PLANNED", position=99)]
    rows = [
        PositionInfo(id=12, prod_order_id=11, name="A", due_date=FUTURE),
        PositionInfo(id=99, prod_order_id=10, name="B", due_date=FUTURE),
    ]
    orders = [OrderInfo(id=11, custom_id="PR-00002"), OrderInfo(id=10, custom_id="PR-00001")]

    groups = compute(operations, position_rows=rows, orders=orders)

    assert [g.order_custom_id for g in groups] == ["PR-00001", "PR-00002"]


def test_overdue_only_keeps_orders_with_an_overdue_task():
    overdue_groups = compute([op(1, "0010", "PLANNED")], position_rows=positions(due_date=PAST))
    on_time_groups = compute([op(1, "0010", "PLANNED")], position_rows=positions(due_date=FUTURE))

    assert filter_overdue_eligible(overdue_groups, machine_group_ids=[], overdue_only=True) == overdue_groups
    assert filter_overdue_eligible(on_time_groups, machine_group_ids=[], overdue_only=True) == []
    # Off, it is a passthrough.
    assert filter_overdue_eligible(on_time_groups, machine_group_ids=[], overdue_only=False) == on_time_groups


def test_overdue_only_respects_the_machine_group_filter():
    groups = compute([op(1, "0010", "PLANNED")], position_rows=positions(due_date=PAST))

    assert filter_overdue_eligible(groups, machine_group_ids=[13], overdue_only=True) == groups
    assert filter_overdue_eligible(groups, machine_group_ids=[99], overdue_only=True) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_order_task_dependency.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.order_task_dependency'`

- [ ] **Step 3: Implement the module**

Create `app/services/order_task_dependency.py`:

```python
"""The Order Task Dependency report's computation.

A port of shopfloor-suite's OrderTaskDependencyReportService::mapOrder(). That service
is only on a feature branch that will never reach the live CRM, so the logic lives here
instead and runs against raw OData rows — see
docs/superpowers/specs/2026-09-09-order-task-dependency-report-design.md.

Everything in this module is pure: rows in, rows out, no HTTP and no clock of its own
(`today` is always passed in), which is what makes the sequencing rules cheap to test.
"""

from dataclasses import dataclass
from datetime import date, datetime

from app.enums import ProdOrderPosOperationStatus
from app.services.task_source import MachineInfo, OperationInfo, OrderInfo, PositionInfo


@dataclass(frozen=True)
class TaskRow:
    operation_id: int
    order_id: int
    order_custom_id: str | None
    prod_order_pos_id: int
    item_name: str | None
    pos: str | None
    name: str | None
    machine_name: str | None
    machine_group_id: int | None
    machine_group_name: str | None
    status: str | None
    start: datetime | None
    end: datetime | None
    due_date: datetime | None
    has_dependency: bool
    is_blocked: bool
    blocked_by: str | None
    is_overdue: bool


@dataclass(frozen=True)
class OrderGroup:
    order_id: int
    order_custom_id: str | None
    tasks: list[TaskRow]


def _parse(timestamp: str | None) -> datetime | None:
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp)
    except ValueError:
        return None


def _pos_key(pos: str | None) -> int:
    """Sequence key. The CRM stores pos as a zero-padded string ("0070"), so it must sort
    numerically — lexically, "0100" would precede "0090". Mirrors the source service's
    `(int) $operation->pos`, which yields 0 for anything unparseable."""
    try:
        return int(pos)
    except (TypeError, ValueError):
        return 0


def _is_closed(status: str | None) -> bool:
    return status == ProdOrderPosOperationStatus.CLOSED.value


def compute_order_groups(
    operations: list[OperationInfo],
    positions: list[PositionInfo],
    orders: list[OrderInfo],
    machines: list[MachineInfo],
    machine_group_names: dict[int, str],
    today: date,
) -> list[OrderGroup]:
    positions_by_id = {p.id: p for p in positions}
    orders_by_id = {o.id: o for o in orders}
    machines_by_id = {m.id: m for m in machines}

    live = [
        o
        for o in operations
        if o.status != ProdOrderPosOperationStatus.DELETED.value and o.prod_order_pos_id in positions_by_id
    ]

    by_position: dict[int, list[OperationInfo]] = {}
    for operation in live:
        by_position.setdefault(operation.prod_order_pos_id, []).append(operation)

    tasks_by_order: dict[int, list[TaskRow]] = {}
    for position_id, position_operations in by_position.items():
        position = positions_by_id[position_id]
        order = orders_by_id.get(position.prod_order_id)
        if order is None:
            continue
        due_date = _parse(position.due_date)
        predecessor: OperationInfo | None = None

        for operation in sorted(position_operations, key=lambda o: _pos_key(o.pos)):
            machine = machines_by_id.get(operation.machine_id) if operation.machine_id else None
            group_id = machine.machine_group_id if machine else None
            has_dependency = predecessor is not None
            is_blocked = has_dependency and not _is_closed(predecessor.status)

            tasks_by_order.setdefault(order.id, []).append(
                TaskRow(
                    operation_id=operation.id,
                    order_id=order.id,
                    order_custom_id=order.custom_id,
                    prod_order_pos_id=position_id,
                    item_name=position.name,
                    pos=operation.pos,
                    name=operation.name,
                    machine_name=machine.name if machine else None,
                    machine_group_id=group_id,
                    machine_group_name=machine_group_names.get(group_id) if group_id else None,
                    status=operation.status,
                    start=_parse(operation.start),
                    end=_parse(operation.end),
                    due_date=due_date,
                    has_dependency=has_dependency,
                    is_blocked=is_blocked,
                    blocked_by=predecessor.name if is_blocked else None,
                    is_overdue=_is_overdue(operation, due_date, today),
                )
            )
            predecessor = operation

    groups = [
        OrderGroup(order_id=order_id, order_custom_id=orders_by_id[order_id].custom_id, tasks=tasks)
        for order_id, tasks in tasks_by_order.items()
        if tasks and any(not _is_closed(task.status) for task in tasks)
    ]
    return sorted(groups, key=lambda g: g.order_custom_id or "")


def _is_overdue(operation: OperationInfo, due_date: datetime | None, today: date) -> bool:
    if due_date is None or _is_closed(operation.status):
        return False
    return due_date.date() < today


def filter_overdue_eligible(
    groups: list[OrderGroup], machine_group_ids: list[int], overdue_only: bool
) -> list[OrderGroup]:
    """Keep only orders having an overdue task that also matches the machine-group filter.

    Mirrors the source service's filterOverdueEligible(), which shopfloor-suite applies
    to its PDF only; here it applies to every output so the page and both exports can
    never disagree about what is on screen.
    """
    if not overdue_only:
        return groups
    return [
        group
        for group in groups
        if any(
            task.is_overdue and (not machine_group_ids or task.machine_group_id in machine_group_ids)
            for task in group.tasks
        )
    ]


def flatten(groups: list[OrderGroup]) -> list[TaskRow]:
    return [task for group in groups for task in group.tasks]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_order_task_dependency.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git add app/services/order_task_dependency.py tests/test_order_task_dependency.py
git commit -m "Compute order task dependency: sequencing, blocked-by and overdue"
```

---

### Task 4: Fetch orchestration (filters + the two-stage query)

**Files:**
- Modify: `app/services/order_task_dependency.py`
- Test: `tests/test_order_task_dependency_load.py`

**Interfaces:**
- Consumes: everything from Tasks 2–3.
- Produces:
  - `@dataclass ReportFilters(start: date, end: date, machine_group_ids: list[int], overdue_only: bool)` with `ReportFilters.parse(start, end, machine_group_ids, overdue_only, today) -> ReportFilters` and `describe(machine_group_names: dict[int, str]) -> str`
  - `class InvalidFilters(ValueError)`
  - `load_report(provider, filters, today, machine_group_names: dict[int, str] | None = None) -> list[OrderGroup]`

**Where machine-group *names* come from:** the local `teams` table, passed in by the router (Task 6) — not a sixth CRM call. `load_report` therefore takes them as an argument rather than fetching them.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_order_task_dependency_load.py`:

```python
from datetime import date

import pytest

from app.services.order_task_dependency import (
    InvalidFilters,
    ReportFilters,
    flatten,
    load_report,
)
from app.services.task_source import MachineInfo, OperationInfo, OrderInfo, PositionInfo

TODAY = date(2026, 9, 10)


class FakeProvider:
    """Records what load_report asked for, so the two-stage query shape is assertable."""

    def __init__(self, operations, positions, orders, machines):
        self._operations = operations
        self._positions = positions
        self._orders = orders
        self._machines = machines
        self.machine_ids_asked = "unset"
        self.order_ids_asked = None

    def list_machines(self):
        return self._machines

    def list_operations_started_before(self, end, machine_ids):
        self.machine_ids_asked = machine_ids
        return self._operations

    def list_positions_by_id(self, position_ids):
        return [p for p in self._positions if p.id in set(position_ids)]

    def list_positions_by_order(self, order_ids):
        self.order_ids_asked = sorted(order_ids)
        return [p for p in self._positions if p.prod_order_id in set(order_ids)]

    def list_operations_by_position(self, position_ids):
        return [o for o in self._operations if o.prod_order_pos_id in set(position_ids)]

    def list_orders_by_id(self, order_ids):
        return [o for o in self._orders if o.id in set(order_ids)]


def operation(id, position, pos="0010", status="PLANNED", machine_id=44):
    return OperationInfo(
        id=id,
        prod_order_pos_id=position,
        pos=pos,
        name=f"op-{id}",
        status=status,
        start="2026-09-01T00:00:00+00:00",
        end=None,
        machine_id=machine_id,
    )


MACHINES = [
    MachineInfo(id=44, name="M1", custom_id="1", machine_group_id=13),
    MachineInfo(id=45, name="M2", custom_id="2", machine_group_id=7),
]


def test_parse_defaults_end_to_today_and_start_to_one_month_earlier():
    filters = ReportFilters.parse(start=None, end=None, machine_group_ids=[], overdue_only=False, today=TODAY)

    assert filters.end == date(2026, 9, 10)
    assert filters.start == date(2026, 8, 10)


def test_parse_handles_a_month_boundary_without_overflowing():
    filters = ReportFilters.parse(start=None, end="2026-03-31", machine_group_ids=[], overdue_only=False, today=TODAY)

    assert filters.start == date(2026, 2, 28)


def test_parse_rejects_a_reversed_range():
    with pytest.raises(InvalidFilters, match="before"):
        ReportFilters.parse(
            start="2026-09-10", end="2026-09-01", machine_group_ids=[], overdue_only=False, today=TODAY
        )


def test_parse_rejects_an_unparseable_date():
    with pytest.raises(InvalidFilters, match="date"):
        ReportFilters.parse(start="10-09-2026", end=None, machine_group_ids=[], overdue_only=False, today=TODAY)


def test_load_report_excludes_positions_whose_due_date_precedes_the_window():
    operations = [operation(1, position=12), operation(2, position=13)]
    positions = [
        PositionInfo(id=12, prod_order_id=11, name="In window", due_date="2026-09-09T00:00:00+00:00"),
        PositionInfo(id=13, prod_order_id=10, name="Too old", due_date="2026-01-01T00:00:00+00:00"),
    ]
    orders = [OrderInfo(id=11, custom_id="PR-2"), OrderInfo(id=10, custom_id="PR-1")]
    provider = FakeProvider(operations, positions, orders, MACHINES)
    filters = ReportFilters.parse(start="2026-09-01", end="2026-09-30", machine_group_ids=[], overdue_only=False, today=TODAY)

    groups = load_report(provider, filters, today=TODAY)

    assert [g.order_custom_id for g in groups] == ["PR-2"]
    assert provider.order_ids_asked == [11]


def test_load_report_resolves_the_machine_group_filter_to_machine_ids():
    provider = FakeProvider([operation(1, position=12)], [], [], MACHINES)
    filters = ReportFilters.parse(start=None, end=None, machine_group_ids=[13], overdue_only=False, today=TODAY)

    load_report(provider, filters, today=TODAY)

    assert provider.machine_ids_asked == [44]


def test_load_report_asks_for_every_machine_when_no_group_filter():
    provider = FakeProvider([], [], [], MACHINES)
    filters = ReportFilters.parse(start=None, end=None, machine_group_ids=[], overdue_only=False, today=TODAY)

    load_report(provider, filters, today=TODAY)

    assert provider.machine_ids_asked is None


def test_load_report_includes_the_orders_whole_chain_not_just_qualifying_operations():
    # Only op 1 qualifies on date, but the order's other operation must still be shown.
    qualifying = operation(1, position=12, pos="0010")
    sibling = operation(2, position=12, pos="0020")
    positions = [PositionInfo(id=12, prod_order_id=11, name="Item", due_date="2026-09-09T00:00:00+00:00")]
    provider = FakeProvider([qualifying, sibling], positions, [OrderInfo(id=11, custom_id="PR-1")], MACHINES)
    filters = ReportFilters.parse(start="2026-09-01", end="2026-09-30", machine_group_ids=[], overdue_only=False, today=TODAY)

    tasks = flatten(load_report(provider, filters, today=TODAY))

    assert [t.operation_id for t in tasks] == [1, 2]
    assert tasks[1].is_blocked is True


def test_describe_summarises_the_active_filters():
    filters = ReportFilters.parse(
        start="2026-09-01", end="2026-09-30", machine_group_ids=[13], overdue_only=True, today=TODAY
    )

    summary = filters.describe({13: "Team Rajib"})

    assert "2026-09-01" in summary and "2026-09-30" in summary
    assert "Team Rajib" in summary
    assert "Overdue" in summary
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_order_task_dependency_load.py -v`
Expected: FAIL with `ImportError: cannot import name 'InvalidFilters'`

- [ ] **Step 3: Implement filters and orchestration**

Append to `app/services/order_task_dependency.py`. First widen its imports — replace the existing `from datetime import date, datetime` line with:

```python
import calendar
from datetime import date, datetime, time, timedelta, timezone
```

Then append:

```python
class InvalidFilters(ValueError):
    """The submitted filter values cannot produce a report (bad or reversed dates)."""


def _minus_one_month(value: date) -> date:
    """One calendar month earlier, clamped to the shorter month (31 Mar -> 28 Feb).

    Mirrors Carbon's subMonth() without pulling in dateutil for one call.
    """
    year = value.year - 1 if value.month == 1 else value.year
    month = 12 if value.month == 1 else value.month - 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


@dataclass(frozen=True)
class ReportFilters:
    start: date
    end: date
    machine_group_ids: list[int]
    overdue_only: bool

    @classmethod
    def parse(
        cls,
        start: str | None,
        end: str | None,
        machine_group_ids: list[int],
        overdue_only: bool,
        today: date,
    ) -> "ReportFilters":
        """Resolve the query string. Defaults are deliberately asymmetric, matching the
        source report: a missing end means today, a missing start means one month before
        whatever end resolved to."""
        try:
            resolved_end = date.fromisoformat(end) if end else today
            resolved_start = date.fromisoformat(start) if start else _minus_one_month(resolved_end)
        except ValueError as exc:
            raise InvalidFilters("Enter each date as YYYY-MM-DD.") from exc
        if resolved_start > resolved_end:
            raise InvalidFilters("The start date must fall before the end date.")
        return cls(
            start=resolved_start,
            end=resolved_end,
            machine_group_ids=machine_group_ids,
            overdue_only=overdue_only,
        )

    def describe(self, machine_group_names: dict[int, str]) -> str:
        parts = [f"{self.start.isoformat()} to {self.end.isoformat()}"]
        if self.machine_group_ids:
            named = [machine_group_names.get(i, str(i)) for i in self.machine_group_ids]
            parts.append("Machine groups: " + ", ".join(named))
        else:
            parts.append("All machine groups")
        if self.overdue_only:
            parts.append("Overdue only")
        return " · ".join(parts)


def load_report(
    provider,
    filters: ReportFilters,
    today: date,
    machine_group_names: dict[int, str] | None = None,
) -> list[OrderGroup]:
    """Fetch and compute the report.

    Two stages, mirroring the source service's whereHas() + eager-load pair: find the
    orders that qualify, then load each qualifying order's FULL task chain (an order
    earns its place on one matching operation, but is displayed in full).

    The due-date predicate is applied here rather than in the query because this OData
    dialect cannot filter across a navigation property — see the spec.
    """
    machines = provider.list_machines()
    machine_ids = None
    if filters.machine_group_ids:
        wanted = set(filters.machine_group_ids)
        machine_ids = sorted(m.id for m in machines if m.machine_group_id in wanted)

    end_exclusive = datetime.combine(filters.end, time.min, tzinfo=timezone.utc) + timedelta(days=1)
    candidates = provider.list_operations_started_before(end_exclusive, machine_ids)
    candidate_position_ids = sorted({o.prod_order_pos_id for o in candidates if o.prod_order_pos_id})
    if not candidate_position_ids:
        return []

    candidate_positions = provider.list_positions_by_id(candidate_position_ids)
    qualifying_order_ids = sorted(
        {
            position.prod_order_id
            for position in candidate_positions
            if position.prod_order_id and _due_on_or_after(position.due_date, filters.start)
        }
    )
    if not qualifying_order_ids:
        return []

    positions = provider.list_positions_by_order(qualifying_order_ids)
    operations = provider.list_operations_by_position([p.id for p in positions])
    orders = provider.list_orders_by_id(qualifying_order_ids)

    groups = compute_order_groups(
        operations=operations,
        positions=positions,
        orders=orders,
        machines=machines,
        machine_group_names=machine_group_names or {},
        today=today,
    )
    return filter_overdue_eligible(groups, filters.machine_group_ids, filters.overdue_only)


def _due_on_or_after(due_date: str | None, start: date) -> bool:
    parsed = _parse(due_date)
    return parsed is not None and parsed.date() >= start
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_order_task_dependency_load.py tests/test_order_task_dependency.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/order_task_dependency.py tests/test_order_task_dependency_load.py
git commit -m "Orchestrate the two-stage fetch and resolve report filters"
```

---

### Task 5: Reports tab, role guard and nav entry

**Files:**
- Modify: `app/auth.py` (add `require_reports_access`)
- Create: `app/routers/reports.py`
- Create: `app/templates/reports_index.html`
- Modify: `app/main.py` (register the router)
- Modify: `app/templates/base.html` (nav link)
- Test: `tests/test_reports.py`

**Interfaces:**
- Consumes: `require_login`, `UserRole` (existing).
- Produces: `require_reports_access(current_user) -> User`; `REPORTS: list[ReportLink]` registry with `ReportLink(slug, title, description)`; router mounted at `/reports`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reports.py`:

```python
import pytest

from app.models.user import UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user


@pytest.mark.parametrize("role", [UserRole.admin, UserRole.devops, UserRole.team_lead])
def test_allowed_roles_can_open_the_reports_tab(web, role):
    client, session = web
    make_user(session, id=1, name="U", role=role, username="u", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "u")

    response = client.get("/reports")

    assert response.status_code == 200
    assert "Order Task Dependency" in response.text


def test_a_developer_is_refused(web):
    client, session = web
    make_user(session, id=1, name="D", role=UserRole.developer, username="d", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "d")

    assert client.get("/reports").status_code == 403


def test_the_nav_link_is_hidden_from_a_developer(web):
    client, session = web
    make_user(session, id=1, name="D", role=UserRole.developer, username="d", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "d")

    assert 'href="/reports"' not in client.get("/dashboard").text


def test_the_nav_link_is_shown_to_devops(web):
    client, session = web
    make_user(session, id=1, name="O", role=UserRole.devops, username="o", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "o")

    assert 'href="/reports"' in client.get("/dashboard").text


def test_anonymous_access_redirects_to_login(client):
    response = client.get("/reports", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_reports.py -v`
Expected: FAIL — `/reports` returns 404

- [ ] **Step 3: Add the guard**

In `app/auth.py`, after `require_admin`:

```python
def require_reports_access(current_user: User = Depends(require_login)) -> User:
    """Reports are readable by admin, devops and team leads.

    A flat role check, deliberately unlike can_approve_deployment_request(): there is no
    per-team scoping here — a lead who can see reports sees all of them.
    """
    if current_user.role not in (UserRole.admin, UserRole.devops, UserRole.team_lead):
        raise HTTPException(status_code=403, detail="Reports access requires admin, devops or team lead")
    return current_user
```

- [ ] **Step 4: Add the router and registry**

Create `app/routers/reports.py`:

```python
"""The Reports tab.

A landing page over a registry of report types, so adding a report later means one
REPORTS entry plus its own router module — not another top-level nav item.
"""

from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from app.auth import require_reports_access
from app.models.user import User
from app.static_version import STATIC_VERSION

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["static_version"] = STATIC_VERSION


@dataclass(frozen=True)
class ReportLink:
    slug: str
    title: str
    description: str


REPORTS = [
    ReportLink(
        slug="order-task-dependency",
        title="Order Task Dependency",
        description="Per order, which tasks are blocked by an unfinished predecessor and which are overdue.",
    ),
]


@router.get("/reports")
def reports_index(request: Request, current_user: User = Depends(require_reports_access)):
    return templates.TemplateResponse(
        request, "reports_index.html", {"current_user": current_user, "reports": REPORTS}
    )
```

- [ ] **Step 5: Add the landing template**

Create `app/templates/reports_index.html`:

```html
{% extends "base.html" %}
{% block title %}Reports{% endblock %}
{% block content %}
<h1>Reports</h1>
<div class="report-cards">
  {% for report in reports %}
  <a class="report-card" href="/reports/{{ report.slug }}">
    <h2>{{ report.title }}</h2>
    <p>{{ report.description }}</p>
  </a>
  {% endfor %}
</div>
{% endblock %}
```

- [ ] **Step 6: Register the router and nav link**

In `app/main.py`, add the import next to the other routers:

```python
from app.routers.reports import router as reports_router
```

and register it after `clients_router`:

```python
app.include_router(reports_router)
```

In `app/templates/base.html`, after the `/clients` link:

```html
        {% if current_user.role.value in ["admin", "devops", "team_lead"] %}
          <a href="/reports">Reports</a>
        {% endif %}
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_reports.py -v`
Expected: PASS (7 tests)

- [ ] **Step 8: Commit**

```bash
git add app/auth.py app/routers/reports.py app/templates/reports_index.html app/main.py app/templates/base.html tests/test_reports.py
git commit -m "Add the Reports tab with an admin/devops/team-lead guard"
```

---

### Task 6: The Order Task Dependency page

**Files:**
- Create: `app/routers/reports_order_task_dependency.py`
- Create: `app/templates/reports/order_task_dependency.html`
- Modify: `app/main.py`
- Test: `tests/test_reports_order_task_dependency.py`

**Interfaces:**
- Consumes: `load_report`, `ReportFilters`, `InvalidFilters`, `flatten` (Task 4); `require_reports_access` (Task 5); `Team` (existing).
- Produces: `GET /reports/order-task-dependency`; module-level `provider_for(settings)` seam that tests override via `app.dependency_overrides`; `_parse_report_filters(...)` shared with Tasks 7–8.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reports_order_task_dependency.py`:

```python
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


def test_an_invalid_date_shows_an_error_banner_not_a_500(web, report):
    response = signed_in(web).get(URL, params={"start": "not-a-date"})

    assert response.status_code == 200
    assert "YYYY-MM-DD" in response.text


def test_a_crm_failure_shows_an_error_banner_not_a_500(web, monkeypatch):
    # Signature must match how _load_groups calls load_report — including
    # machine_group_names — or this raises TypeError before the body runs and the test
    # silently stops exercising a transport failure.
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_reports_order_task_dependency.py -v`
Expected: FAIL — `ModuleNotFoundError: app.routers.reports_order_task_dependency`

- [ ] **Step 3: Implement the router**

Create `app/routers/reports_order_task_dependency.py`:

```python
"""The Order Task Dependency report page.

Reads live from the CRM on each request — there is no local table behind this report,
matching the source report's own behaviour (pick filters, view results). The HTML view
and both exports share _parse_report_filters()/_load_groups() so they can never disagree
about what is currently filtered.
"""

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_reports_access
from app.config import Settings, get_settings
from app.database import get_db
from app.models.team import Team
from app.models.user import User
from app.services.order_task_dependency import (
    InvalidFilters,
    ReportFilters,
    flatten,
    load_report,
)
from app.services.task_source import InHouseTaskSourceProvider
from app.static_version import STATIC_VERSION

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["static_version"] = STATIC_VERSION

CRM_FAILURE_MESSAGE = "The report could not be loaded from the CRM. Try again, or check that the CRM is reachable."


def build_provider(settings: Settings) -> InHouseTaskSourceProvider:
    """Seam for tests — patched out so no test ever needs a live CRM."""
    return InHouseTaskSourceProvider(settings)


def _machine_groups(db: Session) -> list[Team]:
    # The already-synced local mirror of the CRM's MachineGroups; no second CRM call
    # just to fill a dropdown.
    return list(db.scalars(select(Team).order_by(Team.name)))


def _parse_report_filters(
    start: str | None, end: str | None, machine_group_id: list[int], overdue_only: bool
) -> ReportFilters:
    return ReportFilters.parse(
        start=start or None,
        end=end or None,
        machine_group_ids=list(machine_group_id),
        overdue_only=overdue_only,
        today=date.today(),
    )


def _group_names(db: Session) -> dict[int, str]:
    return {team.id: team.name for team in _machine_groups(db)}


def _load_groups(db: Session, settings: Settings, filters: ReportFilters):
    return load_report(
        build_provider(settings),
        filters,
        today=date.today(),
        machine_group_names=_group_names(db),
    )


@router.get("/reports/order-task-dependency")
def order_task_dependency_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_reports_access),
    settings: Settings = Depends(get_settings),
    start: str | None = None,
    end: str | None = None,
    machine_group_id: list[int] = Query(default=[]),
    overdue_only: bool = False,
):
    groups: list = []
    error = None
    filters = None
    try:
        filters = _parse_report_filters(start, end, machine_group_id, overdue_only)
        groups = _load_groups(db, settings, filters)
    except InvalidFilters as exc:
        error = str(exc)
    except Exception:  # noqa: BLE001 — any CRM/transport failure degrades to a banner
        error = CRM_FAILURE_MESSAGE

    return templates.TemplateResponse(
        request,
        "reports/order_task_dependency.html",
        {
            "current_user": current_user,
            "groups": groups,
            "tasks": flatten(groups),
            "error": error,
            "filters": filters,
            "machine_groups": _machine_groups(db),
            "selected_machine_group_ids": list(machine_group_id),
            "start": start or "",
            "end": end or "",
            "overdue_only": overdue_only,
            "generated_at": datetime.now(timezone.utc),
        },
    )
```

- [ ] **Step 4: Add the template**

Create `app/templates/reports/order_task_dependency.html`:

```html
{% extends "base.html" %}
{% block title %}Order Task Dependency{% endblock %}
{% block content %}
<h1>Order Task Dependency</h1>

{% if error %}
<p class="error">{{ error }}</p>
{% endif %}

<form method="get" action="/reports/order-task-dependency" class="filter-bar">
  <label>From <input type="date" name="start" value="{{ start }}"></label>
  <label>To <input type="date" name="end" value="{{ end }}"></label>
  <label>Machine group
    <select name="machine_group_id" multiple size="4">
      {% for team in machine_groups %}
      <option value="{{ team.id }}" {% if team.id in selected_machine_group_ids %}selected{% endif %}>{{ team.name }}</option>
      {% endfor %}
    </select>
  </label>
  <label><input type="checkbox" name="overdue_only" {% if overdue_only %}checked{% endif %}> Overdue only</label>
  <button type="submit">Apply</button>
  <a class="button-link" href="/reports/order-task-dependency">Reset</a>
</form>

{% if groups %}
<p class="filter-summary">
  {{ tasks|length }} task{{ '' if tasks|length == 1 else 's' }} across {{ groups|length }} order{{ '' if groups|length == 1 else 's' }}
  <a class="button-link" href="/reports/order-task-dependency/export.xlsx?{{ request.url.query }}">Export to Excel</a>
  <a class="button-link" href="/reports/order-task-dependency/export.pdf?{{ request.url.query }}">Download PDF</a>
</p>

{% for group in groups %}
<h2>{{ group.order_custom_id }}</h2>
<table class="status-table">
  <thead>
    <tr>
      <th>Item</th><th>Pos</th><th>Task</th><th>Machine</th><th>Machine Group</th>
      <th>Status</th><th>Due Date</th><th>Blocked By</th><th>Overdue</th>
    </tr>
  </thead>
  <tbody>
    {% for task in group.tasks %}
    <tr {% if task.is_overdue %}class="row-overdue"{% endif %}>
      <td>{{ task.item_name or "" }}</td>
      <td>{{ task.pos or "" }}</td>
      <td>{{ task.name or "" }}</td>
      <td>{{ task.machine_name or "" }}</td>
      <td>{{ task.machine_group_name or "" }}</td>
      <td>{{ task.status or "" }}</td>
      <td>{{ task.due_date.strftime("%Y-%m-%d") if task.due_date else "" }}</td>
      <td>{{ task.blocked_by or "" }}</td>
      <td>{{ "Yes" if task.is_overdue else "" }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endfor %}
{% elif not error %}
<p class="empty-state">No orders match these filters.</p>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Register the router**

In `app/main.py`:

```python
from app.routers.reports_order_task_dependency import router as reports_otd_router
```

and after `app.include_router(reports_router)`:

```python
app.include_router(reports_otd_router)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_reports_order_task_dependency.py -v`
Expected: PASS (7 tests)

- [ ] **Step 7: Commit**

```bash
git add app/routers/reports_order_task_dependency.py app/templates/reports/order_task_dependency.html app/main.py tests/test_reports_order_task_dependency.py
git commit -m "Add the Order Task Dependency report page"
```

---

### Task 7: Excel export

**Files:**
- Modify: `app/services/export.py`
- Modify: `app/routers/reports_order_task_dependency.py`
- Test: `tests/test_export.py`, `tests/test_reports_order_task_dependency.py`

**Interfaces:**
- Consumes: `TaskRow` (Task 3), `_columns_to_xlsx` (existing), `_parse_report_filters`/`_load_groups` (Task 6).
- Produces: `order_task_dependency_rows_to_xlsx(tasks: list[TaskRow], sheet_title: str) -> bytes`; `GET /reports/order-task-dependency/export.xlsx`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_export.py`:

```python
from io import BytesIO

from openpyxl import load_workbook

from app.services.export import order_task_dependency_rows_to_xlsx
from app.services.order_task_dependency import TaskRow


def _task_row(**overrides):
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
    )
    defaults.update(overrides)
    return TaskRow(**defaults)


def test_order_task_dependency_xlsx_has_the_expected_header_row():
    content = order_task_dependency_rows_to_xlsx([_task_row()], "Order Task Dependency")

    sheet = load_workbook(BytesIO(content)).active
    assert [cell.value for cell in sheet[1]] == [
        "Order", "Item", "Pos", "Task", "Machine", "Machine Group",
        "Status", "Due Date", "Blocked By", "Overdue",
    ]


def test_order_task_dependency_xlsx_writes_one_flat_row_per_task():
    content = order_task_dependency_rows_to_xlsx([_task_row(), _task_row(operation_id=2, name="Drilling")], "S")

    sheet = load_workbook(BytesIO(content)).active
    assert sheet.max_row == 3  # header + 2 tasks
    # Due Date is None on this fixture: the getter writes "", which openpyxl
    # round-trips as None on read (the release-tracker test above documents the same).
    assert [cell.value for cell in sheet[2]] == [
        "PR-00001", "Item A", "0020", "Milling", "M1", "Team Rajib", "PLANNED", None, "Cutting", "Yes",
    ]


def test_order_task_dependency_xlsx_renders_a_non_overdue_task_blank():
    content = order_task_dependency_rows_to_xlsx([_task_row(is_overdue=False, blocked_by=None)], "S")

    sheet = load_workbook(BytesIO(content)).active
    # Blank, not the string "None" — openpyxl reads an empty-string write back as None.
    assert sheet["I2"].value is None
    assert sheet["J2"].value is None
```

Add to `tests/test_reports_order_task_dependency.py`:

```python
def test_the_excel_export_uses_the_same_filters_as_the_page(web, report):
    response = signed_in(web).get(f"{URL}/export.xlsx", params={"start": "2026-09-01", "overdue_only": "on"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert report["filters"].overdue_only is True


def test_the_excel_export_is_refused_for_a_developer(web, report):
    assert signed_in(web, role=UserRole.developer).get(f"{URL}/export.xlsx").status_code == 403
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_export.py -k order_task -v`
Expected: FAIL with `ImportError: cannot import name 'order_task_dependency_rows_to_xlsx'`

- [ ] **Step 3: Add the column spec**

In `app/services/export.py`, add the import

```python
from app.services.order_task_dependency import TaskRow
```

and, after `release_tracker_rows_to_xlsx`:

```python
ORDER_TASK_DEPENDENCY_COLUMNS = [
    ("Order", lambda r: r.order_custom_id or ""),
    ("Item", lambda r: r.item_name or ""),
    ("Pos", lambda r: r.pos or ""),
    ("Task", lambda r: r.name or ""),
    ("Machine", lambda r: r.machine_name or ""),
    ("Machine Group", lambda r: r.machine_group_name or ""),
    ("Status", lambda r: r.status or ""),
    ("Due Date", lambda r: r.due_date.strftime("%Y-%m-%d") if r.due_date else ""),
    ("Blocked By", lambda r: r.blocked_by or ""),
    ("Overdue", lambda r: "Yes" if r.is_overdue else ""),
]


def order_task_dependency_rows_to_xlsx(rows: list[TaskRow], sheet_title: str) -> bytes:
    """One flat row per task — the grouping by order is a presentation concern of the
    HTML/PDF views, not something a spreadsheet should have to unpick."""
    return _columns_to_xlsx(rows, ORDER_TASK_DEPENDENCY_COLUMNS, sheet_title)
```

- [ ] **Step 4: Add the route**

In `app/routers/reports_order_task_dependency.py`, add the imports

```python
from io import BytesIO

from fastapi.responses import PlainTextResponse, StreamingResponse

from app.services.export import order_task_dependency_rows_to_xlsx
```

and the route:

```python
@router.get("/reports/order-task-dependency/export.xlsx")
def order_task_dependency_export_xlsx(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_reports_access),
    settings: Settings = Depends(get_settings),
    start: str | None = None,
    end: str | None = None,
    machine_group_id: list[int] = Query(default=[]),
    overdue_only: bool = False,
):
    try:
        filters = _parse_report_filters(start, end, machine_group_id, overdue_only)
        groups = _load_groups(db, settings, filters)
    except InvalidFilters as exc:
        return PlainTextResponse(str(exc), status_code=400)
    except Exception:  # noqa: BLE001 — never hand back a corrupt workbook
        return PlainTextResponse(CRM_FAILURE_MESSAGE, status_code=502)

    content = order_task_dependency_rows_to_xlsx(flatten(groups), "Order Task Dependency")
    filename = f"order-task-dependency-{filters.start.isoformat()}-to-{filters.end.isoformat()}.xlsx"
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_export.py tests/test_reports_order_task_dependency.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/services/export.py app/routers/reports_order_task_dependency.py tests/test_export.py tests/test_reports_order_task_dependency.py
git commit -m "Export the Order Task Dependency report to Excel"
```

---

### Task 8: PDF export

**Files:**
- Modify: `requirements.txt`, `Dockerfile`
- Create: `app/services/report_pdf.py`, `app/templates/reports/order_task_dependency_pdf.html`
- Modify: `app/routers/reports_order_task_dependency.py`
- Test: `tests/test_report_pdf.py`, `tests/test_reports_order_task_dependency.py`

**Interfaces:**
- Consumes: `OrderGroup` (Task 3), `ReportFilters.describe` (Task 4).
- Produces: `render_order_task_dependency_pdf(groups, summary, generated_at) -> bytes`; `GET /reports/order-task-dependency/export.pdf`.

- [ ] **Step 1: Add the dependency**

Append to `requirements.txt`:

```
# HTML/CSS -> PDF for report exports. Chosen over reportlab so the PDF is a Jinja
# template rendered from the same rows as the HTML view, rather than hand-positioned.
weasyprint>=62,<66
```

Install it locally: `cd /home/abubakkar/Desktop/Versions/codebase/deployment_status && .venv/bin/pip install "weasyprint>=62,<66"`

In `Dockerfile`, replace the dependency-install block with:

```dockerfile
COPY requirements.txt .
# WeasyPrint renders through pango/cairo, which are not in python:*-slim.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b \
        libjpeg62-turbo libopenjp2-7 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_report_pdf.py`:

```python
from datetime import datetime, timezone

from app.services.order_task_dependency import OrderGroup, TaskRow
from app.services.report_pdf import render_order_task_dependency_pdf


def _task(**overrides):
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
    )
    defaults.update(overrides)
    return TaskRow(**defaults)


GENERATED_AT = datetime(2026, 9, 10, 8, 30, tzinfo=timezone.utc)


def test_renders_a_pdf_document():
    content = render_order_task_dependency_pdf(
        [OrderGroup(order_id=11, order_custom_id="PR-00001", tasks=[_task()])],
        summary="2026-09-01 to 2026-09-30 · All machine groups",
        generated_at=GENERATED_AT,
    )

    assert content.startswith(b"%PDF-")
    assert len(content) > 1000


def test_renders_with_no_orders():
    content = render_order_task_dependency_pdf([], summary="No filters", generated_at=GENERATED_AT)

    assert content.startswith(b"%PDF-")
```

Add to `tests/test_reports_order_task_dependency.py`:

```python
def test_the_pdf_export_returns_a_pdf(web, report):
    response = signed_in(web).get(f"{URL}/export.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")


def test_the_pdf_export_is_refused_for_a_developer(web, report):
    assert signed_in(web, role=UserRole.developer).get(f"{URL}/export.pdf").status_code == 403
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_report_pdf.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.report_pdf'`

- [ ] **Step 4: Add the PDF template**

Create `app/templates/reports/order_task_dependency_pdf.html`:

```html
<html>
<head>
  <meta charset="utf-8">
  <style>
    @page { size: A4 landscape; margin: 14mm; @bottom-right { content: "Page " counter(page) " of " counter(pages); font-size: 8pt; color: #666; } }
    body { font-family: "DejaVu Sans", sans-serif; font-size: 9pt; color: #111; }
    h1 { font-size: 15pt; margin: 0 0 2mm; }
    .summary { color: #555; font-size: 8.5pt; margin: 0 0 6mm; }
    h2 { font-size: 11pt; margin: 6mm 0 1.5mm; page-break-after: avoid; }
    table { width: 100%; border-collapse: collapse; page-break-inside: auto; }
    th, td { border: 0.4pt solid #bbb; padding: 1.4mm 1.8mm; text-align: left; vertical-align: top; }
    th { background: #eee; font-weight: bold; }
    tr { page-break-inside: avoid; }
    .overdue { background: #fdecea; }
    .empty { color: #666; font-style: italic; }
  </style>
</head>
<body>
  <h1>Order Task Dependency</h1>
  <p class="summary">{{ summary }} — generated {{ generated_at.strftime("%Y-%m-%d %H:%M UTC") }}</p>

  {% if not groups %}
  <p class="empty">No orders match these filters.</p>
  {% endif %}

  {% for group in groups %}
  <h2>{{ group.order_custom_id }}</h2>
  <table>
    <thead>
      <tr>
        <th>Item</th><th>Pos</th><th>Task</th><th>Machine</th><th>Machine Group</th>
        <th>Status</th><th>Due Date</th><th>Blocked By</th><th>Overdue</th>
      </tr>
    </thead>
    <tbody>
      {% for task in group.tasks %}
      <tr {% if task.is_overdue %}class="overdue"{% endif %}>
        <td>{{ task.item_name or "" }}</td>
        <td>{{ task.pos or "" }}</td>
        <td>{{ task.name or "" }}</td>
        <td>{{ task.machine_name or "" }}</td>
        <td>{{ task.machine_group_name or "" }}</td>
        <td>{{ task.status or "" }}</td>
        <td>{{ task.due_date.strftime("%Y-%m-%d") if task.due_date else "" }}</td>
        <td>{{ task.blocked_by or "" }}</td>
        <td>{{ "Yes" if task.is_overdue else "" }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% endfor %}
</body>
</html>
```

- [ ] **Step 5: Implement the renderer**

Create `app/services/report_pdf.py`:

```python
"""Report PDFs, rendered HTML -> PDF with WeasyPrint.

Sectioned per order rather than one flat table, mirroring the source report's PDF: the
grouping is the point of the document, and a submitted PDF reads better with each order
under its own heading.
"""

from datetime import datetime

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.services.order_task_dependency import OrderGroup

_env = Environment(
    loader=FileSystemLoader("app/templates"),
    autoescape=select_autoescape(["html"]),
)


def render_order_task_dependency_pdf(
    groups: list[OrderGroup], summary: str, generated_at: datetime
) -> bytes:
    # Imported here, not at module import time: WeasyPrint pulls in pango/cairo via
    # ctypes, so keeping it local means the rest of the app still imports on a machine
    # without those system libraries.
    from weasyprint import HTML

    html = _env.get_template("reports/order_task_dependency_pdf.html").render(
        groups=groups, summary=summary, generated_at=generated_at
    )
    return HTML(string=html).write_pdf()
```

- [ ] **Step 6: Add the route**

In `app/routers/reports_order_task_dependency.py`, add

```python
from fastapi.responses import Response

from app.services.report_pdf import render_order_task_dependency_pdf
```

and:

```python
@router.get("/reports/order-task-dependency/export.pdf")
def order_task_dependency_export_pdf(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_reports_access),
    settings: Settings = Depends(get_settings),
    start: str | None = None,
    end: str | None = None,
    machine_group_id: list[int] = Query(default=[]),
    overdue_only: bool = False,
):
    try:
        filters = _parse_report_filters(start, end, machine_group_id, overdue_only)
        groups = _load_groups(db, settings, filters)
    except InvalidFilters as exc:
        return PlainTextResponse(str(exc), status_code=400)
    except Exception:  # noqa: BLE001
        return PlainTextResponse(CRM_FAILURE_MESSAGE, status_code=502)

    content = render_order_task_dependency_pdf(
        groups, summary=filters.describe(_group_names(db)), generated_at=datetime.now(timezone.utc)
    )
    filename = f"order-task-dependency-{filters.start.isoformat()}-to-{filters.end.isoformat()}.pdf"
    return Response(
        content,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_report_pdf.py tests/test_reports_order_task_dependency.py -v`
Expected: PASS. If WeasyPrint raises `OSError: cannot load library 'libpango'`, install the system libs locally: `sudo apt-get install -y libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libjpeg62-turbo libopenjp2-7 fonts-dejavu-core`

- [ ] **Step 8: Commit**

```bash
git add requirements.txt Dockerfile app/services/report_pdf.py app/templates/reports/order_task_dependency_pdf.html app/routers/reports_order_task_dependency.py tests/test_report_pdf.py tests/test_reports_order_task_dependency.py
git commit -m "Export the Order Task Dependency report to PDF"
```

---

### Task 9: Verify against the real CRM and the local Docker stack

**Files:**
- Modify: `README.md` (document the Reports tab)
- Modify: `docs/superpowers/specs/2026-09-09-order-task-dependency-report-design.md` (record the measured live-volume finding)

- [ ] **Step 1: Run the whole suite**

Run: `cd /home/abubakkar/Desktop/Versions/codebase/deployment_status && .venv/bin/python -m pytest -v`
Expected: PASS, no regressions in the pre-existing tests.

- [ ] **Step 2: Smoke-test the real fetch against `crm.test.local`**

The `.env` already points at `http://crm.test.local/api` with working credentials. Run:

```bash
cd /home/abubakkar/Desktop/Versions/codebase/deployment_status && .venv/bin/python - <<'PY'
from datetime import date
from app.config import get_settings
from app.services.order_task_dependency import ReportFilters, flatten, load_report
from app.services.task_source import InHouseTaskSourceProvider
import time

today = date.today()
filters = ReportFilters.parse(start=None, end=None, machine_group_ids=[], overdue_only=False, today=today)
started = time.monotonic()
groups = load_report(InHouseTaskSourceProvider(get_settings()), filters, today=today)
elapsed = time.monotonic() - started
tasks = flatten(groups)
print(f"{len(groups)} orders / {len(tasks)} tasks in {elapsed:.1f}s")
print(f"blocked: {sum(1 for t in tasks if t.is_blocked)}  overdue: {sum(1 for t in tasks if t.is_overdue)}")
for t in tasks[:5]:
    print(f"  {t.order_custom_id} pos={t.pos} {t.name!r} status={t.status} blocked_by={t.blocked_by!r} overdue={t.is_overdue}")
PY
```

Expected: a non-zero order/task count, plausible blocked/overdue flags, and a recorded elapsed time. Note the elapsed time — it is the input to Step 5.

- [ ] **Step 3: Rebuild and run the Docker stack**

Run:

```bash
cd /home/abubakkar/Desktop/Versions/codebase/deployment_status && docker compose up -d --build
docker compose ps
```

Expected: the app container is healthy (the rebuild is required — Task 8 changed the Dockerfile).

- [ ] **Step 4: Check the pages in the running app**

Confirm, logged in as an admin/devops user at `http://127.0.0.1:8010`:
- `/dashboard` shows a **Reports** link in the nav.
- `/reports` lists Order Task Dependency.
- `/reports/order-task-dependency` renders orders with Blocked By / Overdue populated; the filters and Reset work.
- Export to Excel downloads a workbook that opens; Download PDF downloads a readable PDF.
- Logged in as a `developer`, the nav link is absent and visiting `/reports` directly gives 403.

- [ ] **Step 5: Record the measured volume in the spec**

In the spec's "Open items" section, replace the "Live data volume" bullet's final sentence with the measured numbers from Step 2, e.g.:

```markdown
  Measured on `crm.test.local` 2026-09-10: N orders / M tasks in Xs for the default
  one-month window. Re-measure against live volume before relying on it there.
```

- [ ] **Step 6: Document the tab in the README**

In `README.md`, after the `/release-tracker` bullet in the "Once logged in" list:

```markdown
- **`/reports`** — the Reports tab (admin, devops and team leads only). Today it holds
  one report, **Order Task Dependency** (`/reports/order-task-dependency`): per
  production order, which tasks are blocked by an unfinished predecessor and which are
  overdue, filterable by date range, machine group and overdue-only, with Excel and PDF
  export. It reads the CRM live on each request — there is no local table behind it, and
  nothing is cached. The dependency/overdue logic is computed here rather than by the
  CRM: the CRM's own version of this report exists only on a feature branch that will
  never reach the live system, so this app derives it from the raw OData entity sets that
  do exist there. See
  `docs/superpowers/specs/2026-09-09-order-task-dependency-report-design.md` — in
  particular the two OData traps documented there before changing any query.
```

- [ ] **Step 7: Commit**

```bash
git add README.md docs/superpowers/specs/2026-09-09-order-task-dependency-report-design.md
git commit -m "Document the Reports tab and record measured report volume"
```
