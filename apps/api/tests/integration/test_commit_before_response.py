"""D-8: a write is saved before the client is told it succeeded.

Since FastAPI 0.118 the code after `yield` in a dependency runs after the
response is sent, unless the dependency is declared with scope="function"
(https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/).
`get_db` commits after its `yield`, so without that scope a client could be
told "created" before the row existed, or even if saving it failed.

The normal test client waits for the whole request, so these tests watch the
order directly: when the commit finishes and when the response starts.
"""

from __future__ import annotations

from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


def _recording(app, order: list[str]):
    """Wrap *app* so the start of each response is noted in *order*."""

    async def recording_app(scope, receive, send):
        async def send_and_note(message):
            if message["type"] == "http.response.start":
                order.append(f"response {message['status']}")
            await send(message)

        await app(scope, receive, send_and_note)

    return recording_app


async def _register_and_watch(client: AsyncClient, email: str, commit) -> tuple[int, list[str]]:
    order: list[str] = []
    app = client._transport.app  # the app the test client was built with

    async def noted_commit(self):
        await commit(self, order)

    with patch.object(AsyncSession, "commit", noted_commit):
        # raise_app_exceptions=False: see the status the client actually gets.
        transport = ASGITransport(app=_recording(app, order), raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    return r.status_code, order


_real_commit = AsyncSession.commit


class TestCommitBeforeResponse:
    async def test_registering_is_saved_before_the_response_starts(self, client: AsyncClient):
        async def commit(session, order):
            await _real_commit(session)
            order.append("commit")

        status, order = await _register_and_watch(client, "d8-order@example.com", commit)

        assert status == 201
        assert "commit" in order
        assert order.index("commit") < order.index("response 201")

    async def test_a_save_that_fails_is_not_reported_as_success(self, client: AsyncClient):
        async def commit(session, order):
            order.append("commit failed")
            raise RuntimeError("database went away")

        status, order = await _register_and_watch(client, "d8-fail@example.com", commit)

        assert status == 500
        assert "response 201" not in order
