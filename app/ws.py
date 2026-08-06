"""In-memory WebSocket broadcast behind /requests' live-update ping.

Deliberately dumb, by design: every mutation to a DeploymentRequest calls notify() right
after its db.commit(), which fans out a bare "changed" string to every currently-connected
/ws/requests client (see app/main.py's websocket route); the client just reloads the page
on receipt (request_list.html) rather than the server pushing any actual data. All the
existing server-rendered HTML and the desktop-notification detection logic stay exactly as
they were — this only changes *when* the client reloads, from a 30-second timer to
"immediately when something actually changed."

Single-process only, on purpose for now: connections live in a plain in-memory set, so a
broadcast only reaches clients connected to *this* worker process. See
docs/websocket-scaling.md for what changes once this needs to run as more than one worker
or replica — nothing here would need to change shape, but notify() would publish to a
shared channel instead of broadcasting directly.
"""

import asyncio

from starlette.websockets import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def connect(self, websocket: WebSocket) -> None:
        # Captured lazily, on the first connection, rather than at app startup — a
        # websocket route always runs on the one event loop this worker process ever
        # uses (the same one that thread-pools the sync HTTP routes), so there's nothing
        # to gain from binding it earlier, and it sidesteps any assumption about exactly
        # when a given ASGI server/test harness fires its startup lifecycle.
        self._loop = asyncio.get_running_loop()
        await websocket.accept()
        self._connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    async def _broadcast(self, message: str) -> None:
        dead = []
        for connection in list(self._connections):
            try:
                await connection.send_text(message)
            except Exception:
                # Client's gone but we haven't processed its disconnect yet — drop it
                # from the set rather than letting a stale socket break later broadcasts.
                dead.append(connection)
        for connection in dead:
            self._connections.discard(connection)

    def notify(self, message: str = "changed") -> None:
        """Call from a sync route, right after db.commit() — schedules the broadcast
        onto the event loop from whatever worker thread the sync route happens to be
        running in (Starlette runs sync `def` routes in a thread pool, not on the loop
        directly, so this can't just be a plain `await`)."""
        if self._loop is None:
            return  # nobody has ever connected yet; nothing to notify
        asyncio.run_coroutine_threadsafe(self._broadcast(message), self._loop)


manager = ConnectionManager()
