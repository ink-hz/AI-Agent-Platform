"""Run the committed contract cases through loopback TCP with genuine fixture sessions."""
from pathlib import Path
from tempfile import TemporaryDirectory
import socket
import threading
import time
import httpx
import uvicorn
from tests import test_hr_position_cloud_reading as cases

with TemporaryDirectory(prefix='hr-position-tcp-') as directory:
    database_generator = cases.database.__wrapped__()
    database = next(database_generator)
    try:
        for index, test in enumerate([
            cases.test_cloud_standard_partial_confirmation_is_current_and_stale_safe,
            cases.test_cloud_position_result_list_and_exact_old_revision_remain_readable,
        ]):
            fixture = cases.cloud_api.__wrapped__(Path(directory) / str(index), database)
            asgi_client, *context = fixture
            listener = socket.socket()
            listener.bind(('127.0.0.1', 0))
            listener.listen(128)
            server = uvicorn.Server(uvicorn.Config(asgi_client.app, log_level='error', lifespan='off'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
            thread.start()
            deadline = time.monotonic() + 10
            while not server.started:
                if not thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError('local TCP server failed to start')
                time.sleep(.01)
            try:
                # These are freshly issued disposable test sessions; never print the token.
                cookie_header = '; '.join(f'{item.name}={item.value}' for item in asgi_client.cookies.jar)
                with httpx.Client(base_url=f'http://127.0.0.1:{listener.getsockname()[1]}', headers={'Cookie': cookie_header}, timeout=10, trust_env=False) as client:
                    test((client, *context))
                print('TCP PASS:', test.__name__)
            finally:
                server.should_exit = True
                thread.join(5)
                listener.close()
                asgi_client.close()
                if thread.is_alive():
                    raise RuntimeError('local TCP server did not stop')
    finally:
        database_generator.close()
print('2 TCP contract cases passed; local identity-provider and model boundaries substituted; no production or worker fault test.')
