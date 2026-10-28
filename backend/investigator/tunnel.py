"""Postgres over a WebSocket, for hosts that block outgoing database ports.

Some hosts allow outgoing connections only on web ports: Hugging Face Spaces, for one, allows
80, 443 and 8080, so an app there can't reach Postgres on 5432. Neon also accepts the Postgres
protocol inside a secure WebSocket on port 443 (that's how its serverless driver works:
wss://<endpoint host>/v2). Render, where the demo runs, allows 5432, so it connects directly;
`python -m investigator.deploy` verifies Neon through this tunnel either way.

This bridge listens on 127.0.0.1. For every connection asyncpg opens, it opens one WebSocket
to Neon and copies bytes both ways, untouched. asyncpg connects to the bridge without TLS:
the WebSocket itself is TLS-encrypted all the way to Neon, so nothing crosses the network in
the clear. Each pooled connection is one WebSocket, so the pool's size bounds them.
"""

import asyncio
import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from websockets.asyncio.client import connect

logger = logging.getLogger(__name__)


async def start_bridge(websocket_url: str) -> asyncio.Server:
    """Listen on a free local port; each TCP connection is relayed over its own WebSocket."""

    async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            async with connect(websocket_url, max_size=None, compression=None) as websocket:

                async def upstream() -> None:  # asyncpg -> database
                    while data := await reader.read(65536):
                        await websocket.send(data)  # bytes are sent as a binary frame

                async def downstream() -> None:  # database -> asyncpg
                    async for message in websocket:
                        writer.write(message)
                        await writer.drain()

                # Either side closing ends the relay: when Neon suspends, asyncpg sees the
                # connection close and the pool replaces it.
                tasks = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
                try:
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:
                        task.cancel()
                for task in done:
                    if task.exception():  # a dropped connection is normal: Neon suspends
                        logger.info("database tunnel closed: %r", task.exception())
        except Exception:  # one failed relay must not take down the bridge
            logger.exception("database tunnel: could not relay to %s", websocket_url)
        finally:
            writer.close()

    return await asyncio.start_server(relay, "127.0.0.1", 0)


def local_port(server: asyncio.Server) -> int:
    return server.sockets[0].getsockname()[1]


def tunnelled_url(database_url: str, port: int) -> str:
    """The same database, user and password, reached through the local bridge without TLS."""
    parts = urlsplit(database_url)
    credentials = parts.netloc.rpartition("@")[0]
    return urlunsplit(
        (parts.scheme, f"{credentials}@127.0.0.1:{port}", parts.path, "sslmode=disable", "")
    )


def websocket_url(database_url: str) -> str:
    """Neon's WebSocket endpoint for a connection string's host."""
    return f"wss://{urlsplit(database_url).hostname}/v2"


def asyncpg_url(database_url: str) -> str:
    """Drop `channel_binding`: Neon's connection strings include it, asyncpg doesn't know it
    and would send it to the server as a setting, which the server rejects."""
    parts = urlsplit(database_url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "channel_binding"]
    return urlunsplit(parts._replace(query=urlencode(query)))
