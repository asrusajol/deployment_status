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


def test_pdf_template_renders_the_task_rows():
    from app.services.report_pdf import _env

    html = _env.get_template("reports/order_task_dependency_pdf.html").render(
        groups=[OrderGroup(order_id=11, order_custom_id="PR-00001", tasks=[_task()])],
        summary="filters",
        generated_at=GENERATED_AT,
    )

    assert "PR-00001" in html
    assert "Milling" in html
    assert "Cutting" in html  # blocked_by must reach the document
