"""Tests for /ws/requests (app/main.py) — the live-update ping behind app/ws.py.

Both checks here run against the real app via TestClient.websocket_connect, not
app/ws.py's ConnectionManager in isolation — the interesting behavior is the end-to-end
wiring (session auth on the websocket route, manager.notify() actually firing from a
sync route), not the broadcast loop itself.
"""

from datetime import datetime, timezone

import pytest
from starlette.websockets import WebSocketDisconnect

from app.models.deployment_request import DeploymentRequest, RequestStatus
from app.models.user import UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user


def test_requests_ws_rejects_unauthenticated_connection(web):
    client, _session = web

    # The server closes with code 1008 (policy violation) before ever accepting, which
    # TestClient surfaces as WebSocketDisconnect right on __enter__ — confirms the
    # connection was actively rejected, not silently left open.
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/requests"):
            pass
    assert exc_info.value.code == 1008


def test_requests_ws_receives_ping_on_approve(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    make_user(
        session, id=2, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD
    )
    session.add(
        DeploymentRequest(
            task_id="PR-WS-1",
            requested_by=1,
            status=RequestStatus.pending_approval,
            created_at=datetime.now(timezone.utc),
        )
    )
    session.commit()
    login_as(client, "rajib")

    with client.websocket_connect("/ws/requests") as websocket:
        # The websocket's own session was captured from the cookie present at connect
        # time (still "rajib"'s) — logging in as a different user afterward, on the same
        # TestClient, only affects subsequent HTTP requests, not this already-open
        # connection. Approving as a second user proves the ping isn't just an artifact
        # of the same request/session that opened the socket.
        login_as(client, "root")
        response = client.post("/requests/1/approve", follow_redirects=False)
        assert response.status_code == 303

        message = websocket.receive_text()
        assert message == "changed"
