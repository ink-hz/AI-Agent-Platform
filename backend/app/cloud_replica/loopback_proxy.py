from __future__ import annotations

import asyncio


async def _copy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while payload := await reader.read(64 * 1024):
            writer.write(payload)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.write_eof()
        except (AttributeError, ConnectionError, OSError):
            pass


async def _forward(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    target_host: str,
    target_port: int,
) -> None:
    upstream_writer: asyncio.StreamWriter | None = None
    try:
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(target_host, target_port), timeout=3
        )
        await asyncio.gather(
            _copy(reader, upstream_writer),
            _copy(upstream_reader, writer),
        )
    except (ConnectionError, OSError, TimeoutError):
        pass
    finally:
        writer.close()
        if upstream_writer is not None:
            upstream_writer.close()
        await asyncio.gather(
            writer.wait_closed(),
            *(
                (upstream_writer.wait_closed(),)
                if upstream_writer is not None
                else ()
            ),
            return_exceptions=True,
        )


async def start_proxy(
    *, host: str, port: int, target_host: str, target_port: int
) -> asyncio.Server:
    return await asyncio.start_server(
        lambda reader, writer: _forward(
            reader,
            writer,
            target_host=target_host,
            target_port=target_port,
        ),
        host,
        port,
    )


async def _main() -> None:
    server = await start_proxy(
        host="0.0.0.0",
        port=8080,
        target_host="platform-api",
        target_port=8080,
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(_main())
