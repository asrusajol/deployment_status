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
