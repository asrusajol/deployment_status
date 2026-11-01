"""Every RequestStatus must be fully wired up.

Adding a status to this app means touching five separate constants and each
miss fails differently — a status with no STATUS_TAGS entry renders with a
borrowed tag (the Requests row falls back to "in progress"), so it looks
like something it isn't, while a missing sort-set entry just silently sinks the row into
history (which is how the pending_intake ordering regression shipped with a
green suite). These tests iterate the enum itself, so a status added later
fails them without anyone remembering this file exists.
"""

import pytest

from app.models.deployment_request import (
    DeploymentRequest,
    RequestStatus,
)
from app.routers.dashboard import (
    FINISHED_REQUEST_STATUSES,
    OPEN_REQUEST_STATUS_ORDER,
    STATUS_LABELS,
    STATUS_TAGS,
)


@pytest.mark.parametrize("status", list(RequestStatus), ids=lambda s: s.value)
def test_every_status_has_a_tag(status):
    assert status in STATUS_TAGS


@pytest.mark.parametrize("status", list(RequestStatus), ids=lambda s: s.value)
def test_every_status_has_a_label(status):
    assert status in STATUS_LABELS


@pytest.mark.parametrize("status", list(RequestStatus), ids=lambda s: s.value)
def test_every_status_is_either_open_or_finished(status):
    in_open = status in OPEN_REQUEST_STATUS_ORDER
    in_finished = status in FINISHED_REQUEST_STATUSES
    assert in_open != in_finished, (
        f"{status.value} is in "
        f"{'both' if in_open else 'neither'} of OPEN_REQUEST_STATUS_ORDER / "
        "FINISHED_REQUEST_STATUSES — it must be in exactly one, or it sorts wrong silently"
    )


def test_new_statuses_exist():
    assert RequestStatus.returned.value == "returned"
    assert RequestStatus.withdrawn.value == "withdrawn"
