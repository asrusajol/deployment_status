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
