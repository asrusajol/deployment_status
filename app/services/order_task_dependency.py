"""The Order Task Dependency report's computation.

A port of shopfloor-suite's OrderTaskDependencyReportService::mapOrder(). That service
is only on a feature branch that will never reach the live CRM, so the logic lives here
instead and runs against raw OData rows — see
docs/superpowers/specs/2026-09-09-order-task-dependency-report-design.md.

Everything in this module is pure: rows in, rows out, no HTTP and no clock of its own
(`today` is always passed in), which is what makes the sequencing rules cheap to test.
"""

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

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
