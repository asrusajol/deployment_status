from datetime import date

import pytest

from app.enums import ProdOrderPosOperationStatus as Status
from app.services.order_task_dependency import (
    compute_order_groups,
    filter_overdue_eligible,
    flatten,
    hide_closed_tasks,
)
from app.services.task_source import MachineInfo, OperationInfo, OrderInfo, PositionInfo

TODAY = date(2026, 9, 10)
FUTURE = "2026-12-01T00:00:00+00:00"
PAST = "2026-01-01T00:00:00+00:00"


def op(id, pos, status, *, position=12, name=None, machine_id=44, start="2026-09-01T00:00:00+00:00", end=None):
    return OperationInfo(
        id=id,
        prod_order_pos_id=position,
        pos=pos,
        name=name or f"op-{id}",
        status=status,
        start=start,
        end=end,
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


def test_a_null_due_date_is_never_overdue():
    tasks = flatten(compute([op(1, "0010", "PLANNED")], position_rows=positions(due_date=None)))

    assert tasks[0].is_overdue is False


def test_a_task_scheduled_to_end_after_its_due_date_is_scheduled_past_due():
    tasks = flatten(
        compute(
            [op(1, "0010", "PLANNED", end="2026-09-20T00:00:00+00:00")],
            position_rows=positions(due_date="2026-09-10T00:00:00+00:00"),
        )
    )

    assert tasks[0].is_scheduled_past_due is True


def test_a_task_scheduled_to_end_before_its_due_date_is_not_scheduled_past_due():
    tasks = flatten(
        compute(
            [op(1, "0010", "PLANNED", end="2026-09-01T00:00:00+00:00")],
            position_rows=positions(due_date="2026-09-10T00:00:00+00:00"),
        )
    )

    assert tasks[0].is_scheduled_past_due is False


def test_a_null_due_date_is_never_scheduled_past_due():
    tasks = flatten(
        compute(
            [op(1, "0010", "PLANNED", end="2026-09-20T00:00:00+00:00")],
            position_rows=positions(due_date=None),
        )
    )

    assert tasks[0].is_scheduled_past_due is False


def test_a_fully_unscheduled_task_is_never_scheduled_past_due():
    tasks = flatten(
        compute(
            [op(1, "0010", "PLANNED", start=None, end=None)],
            position_rows=positions(due_date="2026-09-10T00:00:00+00:00"),
        )
    )

    assert tasks[0].is_scheduled_past_due is False


def test_a_task_with_no_end_falls_back_to_start_for_scheduled_past_due():
    tasks = flatten(
        compute(
            [op(1, "0010", "PLANNED", start="2026-09-20T00:00:00+00:00", end=None)],
            position_rows=positions(due_date="2026-09-10T00:00:00+00:00"),
        )
    )

    assert tasks[0].is_scheduled_past_due is True


def test_an_order_whose_tasks_are_all_closed_is_dropped():
    groups = compute([op(1, "0010", Status.CLOSED.value), op(2, "0020", Status.CLOSED.value)])

    assert groups == []


def test_an_order_with_a_single_closed_task_is_dropped_like_any_other_all_closed_order():
    # Guards against special-casing the one-task case out of the all-closed rule.
    assert compute([op(1, "0010", Status.CLOSED.value)]) == []


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


def test_parse_accepts_the_z_suffix_python_310_rejects():
    from app.services.order_task_dependency import _parse

    assert _parse("2026-04-06T00:00:00Z") == _parse("2026-04-06T00:00:00+00:00")


def test_parse_raises_on_a_present_but_unparseable_timestamp():
    from app.services.order_task_dependency import InvalidCrmTimestamp, _parse

    with pytest.raises(InvalidCrmTimestamp):
        _parse("06/04/2026 garbage")


def test_parse_still_treats_an_absent_timestamp_as_none():
    from app.services.order_task_dependency import _parse

    assert _parse(None) is None
    assert _parse("") is None


def test_hide_closed_tasks_drops_only_the_closed_tasks_in_a_mixed_order():
    groups = compute([op(1, "0010", Status.CLOSED.value), op(2, "0020", "PLANNED")])

    visible = hide_closed_tasks(groups, show_closed=False)

    assert len(visible) == 1
    assert [t.status for t in visible[0].tasks] == ["PLANNED"]


def test_hide_closed_tasks_preserves_the_full_chains_computed_dependency_fields():
    # op 1 CLOSED, op 2 PLANNED depends on op 1 (not blocked, since predecessor closed).
    groups = compute([op(1, "0010", Status.CLOSED.value, name="Cutting"), op(2, "0020", "PLANNED")])
    full_chain_task = flatten(groups)[1]

    visible = hide_closed_tasks(groups, show_closed=False)
    surviving_task = visible[0].tasks[0]

    assert surviving_task.operation_id == 2
    assert surviving_task.has_dependency == full_chain_task.has_dependency == True
    assert surviving_task.is_blocked == full_chain_task.is_blocked == False
    assert surviving_task.blocked_by == full_chain_task.blocked_by is None


def test_hide_closed_tasks_drops_an_order_whose_visible_tasks_all_disappear():
    # Two positions in one order; one position's only task is closed.
    operations = [
        op(1, "0010", Status.CLOSED.value, position=12),
        op(2, "0010", "PLANNED", position=13),
    ]
    rows = [
        PositionInfo(id=12, prod_order_id=11, name="Item A", due_date=FUTURE),
        PositionInfo(id=13, prod_order_id=11, name="Item B", due_date=FUTURE),
    ]
    groups = compute(operations, position_rows=rows)

    visible = hide_closed_tasks(groups, show_closed=False)

    assert len(visible) == 1
    assert [t.operation_id for t in visible[0].tasks] == [2]


def test_hide_closed_tasks_with_show_closed_true_removes_nothing():
    groups = compute([op(1, "0010", Status.CLOSED.value), op(2, "0020", "PLANNED")])

    assert hide_closed_tasks(groups, show_closed=True) == groups
