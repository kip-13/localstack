import logging
from typing import List

import pytest
import requests
from hypercorn.run import Config
from hypercorn.typing import ASGI3Framework
from werkzeug import Request, Response

from localstack.http.asgi import ASGIAdapter
from localstack.http.hypercorn import HypercornServer
from localstack.utils import net
from localstack.utils.sync import poll_condition

LOG = logging.getLogger(__name__)


@pytest.fixture()
def serve_app():
    _servers = []

    def _create(app: ASGI3Framework, config: Config = None) -> HypercornServer:
        if not config:
            config = Config()
            config.bind = f"localhost:{net.get_free_tcp_port()}"

        srv = HypercornServer(app, config)
        _servers.append(srv)
        srv.start()
        assert srv.wait_is_up(timeout=10), "gave up waiting for server to start up"
        return srv

    yield _create

    for server in _servers:
        server.shutdown()
        assert poll_condition(
            lambda: not server.is_up(), timeout=10
        ), "gave up waiting for server to shut down"


def test_serve_app(serve_app):
    request_list: List[Request] = []

    @Request.application
    def app(request: Request) -> Response:
        request_list.append(request)
        return Response("ok", 200)

    server = serve_app(ASGIAdapter(app))

    response0 = requests.get(server.url + "/foobar?foo=bar", headers={"x-amz-target": "testing"})
    assert response0.ok
    assert response0.text == "ok"

    response1 = requests.get(server.url + "/compute", data='{"foo": "bar"}')
    assert response1.ok
    assert response1.text == "ok"

    request0 = request_list[0]
    assert request0.path.endswith("/foobar")
    assert request0.headers["x-amz-target"] == "testing"
    assert dict(request0.args) == {"foo": "bar"}

    request1 = request_list[1]
    assert request1.path.endswith("/compute")
    assert request1.get_data() == b'{"foo": "bar"}'


def test_generator_creates_chunked_transfer_encoding(serve_app):
    # this test makes sure that creating a response with a generator automatically creates a
    # transfer-encoding=chunked response

    @Request.application
    def app(_request: Request) -> Response:
        def _gen():
            yield "foo"
            yield "bar\n"
            yield "baz\n"

        return Response(_gen(), 200)

    server = serve_app(ASGIAdapter(app))

    response = requests.get(server.url)

    assert response.headers["Transfer-Encoding"] == "chunked"

    it = response.iter_lines()

    assert next(it) == b"foobar"
    assert next(it) == b"baz"
