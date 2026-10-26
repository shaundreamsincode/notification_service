import asyncio
import json
import socket
import threading
import time

import httpx
import pytest

from app.delivery.outcomes import (
    ErrorCategory,
    OutcomeUnknown,
    PermanentRejection,
    SendResult,
    TransientRejection,
)
from app.delivery.providers.base import HttpProvider, OutgoingMessage, derive_timeouts
from app.templates.registry import RenderedMessage

PHONE = "+14155550123"
REQ = httpx.Request("POST", "http://provider.test/send")


class JsonProvider(HttpProvider):
    """A minimal adapter: POST JSON, expect {"id": ...} on acceptance."""

    def __init__(self, deadline_seconds=1.0, transport=None, url="http://provider.test/send"):
        super().__init__(deadline_seconds, transport)
        self.url = url

    def build_request(self, message):
        return httpx.Request(
            "POST",
            self.url,
            json={"to": message.recipient, "body": message.content.text},
            headers={"Idempotency-Key": message.idempotency_key},
        )

    def parse_success(self, response):
        return SendResult(response.json()["id"])


def message() -> OutgoingMessage:
    return OutgoingMessage("sms", PHONE, RenderedMessage(None, None, "hi"), "U-1")


def provider_for(handler, deadline=1.0) -> JsonProvider:
    return JsonProvider(deadline, httpx.MockTransport(handler))


def respond(status, body=None):
    def handler(request):
        return httpx.Response(status, json=body or {})

    return handler


def raises(exc):
    def handler(request):
        raise exc

    return handler


# --- 6.4: classification, table-driven -----------------------------------------

CASES = [
    # (id, handler, expected class, expected category)
    ("429", respond(429), TransientRejection, ErrorCategory.PROVIDER_RATE_LIMITED),
    ("500", respond(500), TransientRejection, ErrorCategory.PROVIDER_UNAVAILABLE),
    ("503", respond(503), TransientRejection, ErrorCategory.PROVIDER_UNAVAILABLE),
    ("connect-timeout", raises(httpx.ConnectTimeout("x", request=REQ)), TransientRejection, ErrorCategory.PROVIDER_UNAVAILABLE),
    ("dns-or-refused", raises(httpx.ConnectError("x", request=REQ)), TransientRejection, ErrorCategory.PROVIDER_UNAVAILABLE),
    ("400", respond(400), PermanentRejection, ErrorCategory.PROVIDER_REJECTED),
    ("401", respond(401), PermanentRejection, ErrorCategory.PROVIDER_REJECTED),
    ("404", respond(404), PermanentRejection, ErrorCategory.PROVIDER_REJECTED),
    ("422", respond(422), PermanentRejection, ErrorCategory.PROVIDER_REJECTED),
    ("read-timeout", raises(httpx.ReadTimeout("x", request=REQ)), OutcomeUnknown, ErrorCategory.PROVIDER_TIMEOUT),
    ("write-timeout", raises(httpx.WriteTimeout("x", request=REQ)), OutcomeUnknown, ErrorCategory.PROVIDER_TIMEOUT),
    ("pool-timeout", raises(httpx.PoolTimeout("x", request=REQ)), OutcomeUnknown, ErrorCategory.PROVIDER_TIMEOUT),
    ("dropped-after-write", raises(httpx.RemoteProtocolError("x", request=REQ)), OutcomeUnknown, ErrorCategory.PROVIDER_UNCLASSIFIED),
    ("read-error", raises(httpx.ReadError("x", request=REQ)), OutcomeUnknown, ErrorCategory.PROVIDER_UNCLASSIFIED),
    ("unparseable-2xx", lambda r: httpx.Response(200, content=b"<html>ok</html>"), OutcomeUnknown, ErrorCategory.PROVIDER_UNCLASSIFIED),
    ("2xx-wrong-shape", respond(202, {"unexpected": True}), OutcomeUnknown, ErrorCategory.PROVIDER_UNCLASSIFIED),
    ("redirect", lambda r: httpx.Response(302, headers={"Location": "http://elsewhere"}), OutcomeUnknown, ErrorCategory.PROVIDER_UNCLASSIFIED),
    ("unlisted-exception", raises(RuntimeError(f"boom {PHONE}")), OutcomeUnknown, ErrorCategory.PROVIDER_UNCLASSIFIED),
    ("unlisted-httpx-exception", raises(httpx.UnsupportedProtocol("x")), OutcomeUnknown, ErrorCategory.PROVIDER_UNCLASSIFIED),
]  # fmt: skip


@pytest.mark.parametrize(
    ("handler", "expected", "category"), [c[1:] for c in CASES], ids=[c[0] for c in CASES]
)
def test_every_http_result_maps_to_one_outcome_class(handler, expected, category):
    with pytest.raises(expected) as info:
        provider_for(handler).send(message())
    assert type(info.value) is expected
    assert info.value.category is category


@pytest.mark.parametrize("status", [200, 201, 202])
def test_recognizable_acceptance_is_confirmed(status):
    result = provider_for(respond(status, {"id": "SM123"})).send(message())
    assert result == SendResult("SM123")


def test_request_carries_the_idempotency_key():
    seen = {}

    def handler(request):
        seen["key"] = request.headers["Idempotency-Key"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "SM1"})

    provider_for(handler).send(message())
    assert seen["key"] == "U-1" and seen["body"]["to"] == PHONE


def test_unlisted_exception_does_not_expose_its_text():
    with pytest.raises(OutcomeUnknown) as info:
        provider_for(raises(RuntimeError(f"boom {PHONE}"))).send(message())
    assert PHONE not in str(info.value) and PHONE not in repr(info.value)
    assert info.value.__cause__ is None


# --- 6.3: the overall deadline --------------------------------------------------


@pytest.mark.parametrize("deadline", [0.01, 0.5, 1, 7.3, 15, 60, 3600])
def test_phase_timeouts_never_sum_past_the_deadline(deadline):
    t = derive_timeouts(deadline)
    assert t.connect is not None and t.read is not None
    assert t.write is not None and t.pool is not None
    assert t.connect + t.read + t.write + t.pool <= deadline


class DrippingServer:
    """Answers promptly with headers, then drips the body a byte at a time.

    No single read ever stalls long, so httpx's per-phase read timeout never
    fires; only an overall deadline can stop the call.
    """

    def __init__(self, drip_interval=0.05, drip_bytes=400):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.drip_interval, self.drip_bytes = drip_interval, drip_bytes
        threading.Thread(target=self._serve, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/send"

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._drip, args=(conn,), daemon=True).start()

    def _drip(self, conn):
        try:
            conn.recv(65536)
            conn.sendall(
                f"HTTP/1.1 200 OK\r\nContent-Length: {self.drip_bytes}\r\n"
                "Content-Type: application/json\r\n\r\n".encode()
            )
            for _ in range(self.drip_bytes):
                time.sleep(self.drip_interval)
                conn.sendall(b" ")
        except OSError:
            pass
        finally:
            conn.close()

    def close(self):
        self.sock.close()


def test_dripping_response_is_cut_off_at_the_deadline_as_unknown():
    server = DrippingServer()  # would take ~20s to finish
    try:
        provider = JsonProvider(deadline_seconds=0.6, url=server.url)
        started = time.monotonic()
        with pytest.raises(OutcomeUnknown) as info:
            provider.send(message())
        elapsed = time.monotonic() - started
    finally:
        server.close()

    assert info.value.category is ErrorCategory.PROVIDER_TIMEOUT
    assert 0.5 <= elapsed < 1.5


def test_connect_failure_is_still_transient_under_the_deadline():
    # 10.255.255.1 is unroutable: depending on the network this hangs (connect
    # timeout) or fails fast (unreachable). Neither delivered anything.
    provider = JsonProvider(deadline_seconds=1.0, url="http://10.255.255.1:81/send")
    started = time.monotonic()
    with pytest.raises(TransientRejection):
        provider.send(message())
    assert time.monotonic() - started < 1.5


def test_refused_connection_is_transient():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # nothing listens here now
    with pytest.raises(TransientRejection):
        JsonProvider(1.0, url=f"http://127.0.0.1:{port}/send").send(message())


def test_slow_handler_is_cut_off_even_without_httpx_timeouts():
    async def slow(request):
        await asyncio.sleep(5)
        return httpx.Response(200, json={"id": "late"})

    started = time.monotonic()
    with pytest.raises(OutcomeUnknown) as info:
        provider_for(slow, deadline=0.3).send(message())
    assert info.value.category is ErrorCategory.PROVIDER_TIMEOUT
    assert time.monotonic() - started < 1.0
