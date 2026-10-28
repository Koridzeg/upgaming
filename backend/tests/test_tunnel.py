"""The WebSocket bridge, end to end: asyncpg -> bridge -> WebSocket -> a real Postgres.

A small WebSocket server stands in for Neon's endpoint: it relays each WebSocket to the test
database over TCP, which is what Neon's proxy does on its side.
"""

import asyncio
from urllib.parse import urlsplit

import asyncpg
from websockets.asyncio.server import serve

from investigator.tunnel import (
    asyncpg_url,
    local_port,
    start_bridge,
    tunnelled_url,
    websocket_url,
)
from tests.conftest import TEST_DATABASE_URL


async def fake_neon(database_url: str):
    """A WebSocket endpoint that relays the Postgres protocol to `database_url` over TCP."""
    target = urlsplit(database_url)

    async def handler(websocket):
        reader, writer = await asyncio.open_connection(target.hostname, target.port)

        async def downstream():
            while data := await reader.read(65536):
                await websocket.send(data)
            # Postgres hung up (asyncpg sent Terminate): pass that on, as Neon's proxy does.
            # asyncpg's close() waits for exactly this.
            await websocket.close()

        relay = asyncio.create_task(downstream())
        async for message in websocket:
            writer.write(message)
            await writer.drain()
        relay.cancel()
        writer.close()

    return await serve(handler, "127.0.0.1", 0)


def test_queries_run_through_the_bridge():
    async def scenario():
        server = await fake_neon(TEST_DATABASE_URL)
        ws_port = server.sockets[0].getsockname()[1]
        bridge = await start_bridge(f"ws://127.0.0.1:{ws_port}/v2")
        url = tunnelled_url(TEST_DATABASE_URL, local_port(bridge))
        try:
            pool = await asyncpg.create_pool(url, min_size=2, max_size=2)
            # two connections at once: each must get its own WebSocket
            results = await asyncio.gather(
                pool.fetchval("SELECT 40 + 2"), pool.fetchval("SELECT current_database()")
            )
            await pool.close()
            return results
        finally:
            bridge.close()
            server.close()

    assert asyncio.run(scenario()) == [42, "investigator_test"]


NEON = (
    "postgresql://app:s3cret@ep-cool-name-123.us-east-1.aws.neon.tech/neondb"
    "?sslmode=require&channel_binding=require"
)


def test_neon_urls_are_rewritten_for_asyncpg_and_the_bridge():
    assert asyncpg_url(NEON).endswith("/neondb?sslmode=require")  # channel_binding dropped
    assert websocket_url(NEON) == "wss://ep-cool-name-123.us-east-1.aws.neon.tech/v2"
    assert (
        tunnelled_url(NEON, 6543) == "postgresql://app:s3cret@127.0.0.1:6543/neondb?sslmode=disable"
    )
