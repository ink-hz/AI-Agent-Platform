import asyncio

from app.cloud_replica.loopback_proxy import start_proxy


def test_loopback_proxy_forwards_bytes_without_application_secrets():
    async def scenario():
        async def echo(reader, writer):
            payload = await reader.read(1024)
            writer.write(payload[::-1])
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        target = await asyncio.start_server(echo, "127.0.0.1", 0)
        target_port = target.sockets[0].getsockname()[1]
        proxy = await start_proxy(
            host="127.0.0.1",
            port=0,
            target_host="127.0.0.1",
            target_port=target_port,
        )
        proxy_port = proxy.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
            writer.write(b"cloud-health")
            await writer.drain()
            assert await reader.read(1024) == b"htlaeh-duolc"
            writer.close()
            await writer.wait_closed()
        finally:
            proxy.close()
            target.close()
            await proxy.wait_closed()
            await target.wait_closed()

    asyncio.run(scenario())
