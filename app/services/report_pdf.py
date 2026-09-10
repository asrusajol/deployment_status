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
