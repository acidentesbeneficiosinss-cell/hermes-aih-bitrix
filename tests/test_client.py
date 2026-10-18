from __future__ import annotations

import pytest
import requests

from aih_bitrix.bitrix.client import BitrixClient, BitrixError, redact

TOKEN = "abcdef0123456789"
WEBHOOK = f"https://portal.bitrix24.com.br/rest/1/{TOKEN}/"


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None, chunks=()):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self._chunks = list(chunks)
        self.closed = False

    def json(self):
        if self._payload is None:
            raise ValueError("sem json")
        return self._payload

    def iter_content(self, chunk_size=65536):
        yield from self._chunks

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[tuple[str, dict]] = []

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json))
        outcome = self.responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def get(self, url, stream=False, timeout=None, allow_redirects=True):
        self.gets.append((url, {"allow_redirects": allow_redirects}))
        return self.responses.pop(0)


def client(*responses, **kw):
    session = FakeSession(*responses)
    sleeps: list[float] = []
    kw.setdefault("min_interval", 0.0)
    return BitrixClient(WEBHOOK, session=session, sleep=sleeps.append, **kw), session, sleeps


def test_bitrix_error_knows_whether_retrying_can_ever_help():
    for code in ("NETWORK", "QUERY_LIMIT_EXCEEDED", "OPERATION_TIME_LIMIT", "INTERNAL_SERVER_ERROR", "HTTP_502", "HTTP_503"):
        assert BitrixError(code).retryable, code
    for code in ("ERROR_CORE", "TOO_LARGE", "DOWNLOAD_HOST", "HTTP_404", "HTTP_403", "NOT_FOUND"):
        assert not BitrixError(code).retryable, code


def test_call_posts_json_to_the_method_url_and_returns_the_payload():
    c, session, _ = client(FakeResponse(payload={"result": {"ok": 1}}))
    assert c.call("crm.contact.fields", {"a": 1}) == {"result": {"ok": 1}}
    assert session.posts == [(f"{WEBHOOK}crm.contact.fields.json", {"a": 1})]
    assert c.host == "portal.bitrix24.com.br"


def test_result_returns_only_the_result_key():
    c, _, _ = client(FakeResponse(payload={"result": [1, 2, 3], "total": 3}))
    assert c.result("crm.contact.list") == [1, 2, 3]


def test_api_error_raises_bitrix_error_without_leaking_the_token():
    payload = {"error": "ERROR_CORE", "error_description": f"falha em {WEBHOOK}x"}
    c, _, _ = client(FakeResponse(payload=payload))
    with pytest.raises(BitrixError) as info:
        c.call("crm.contact.get", {"id": 1})
    assert info.value.code == "ERROR_CORE"
    assert TOKEN not in str(info.value) and TOKEN not in info.value.description


def test_query_limit_exceeded_is_retried_with_backoff_then_succeeds():
    limit = FakeResponse(payload={"error": "QUERY_LIMIT_EXCEEDED", "error_description": "slow down"})
    c, session, sleeps = client(limit, limit, FakeResponse(payload={"result": 1}))
    assert c.result("crm.contact.get") == 1
    assert len(session.posts) == 3 and len(sleeps) == 2 and sleeps[1] > sleeps[0]


def test_http_5xx_and_network_errors_are_retried_and_finally_raised():
    c, session, _ = client(FakeResponse(status=502), requests.ConnectionError("boom"), FakeResponse(status=503), FakeResponse(status=502), max_retries=3)
    with pytest.raises(BitrixError) as info:
        c.call("crm.contact.get")
    assert len(session.posts) == 4 and TOKEN not in str(info.value)


def test_non_json_client_error_is_not_retried():
    c, session, _ = client(FakeResponse(status=404))
    with pytest.raises(BitrixError) as info:
        c.call("crm.contact.get")
    assert info.value.code == "HTTP_404" and len(session.posts) == 1


def test_min_interval_spaces_consecutive_calls():
    now = [100.0]
    sleeps: list[float] = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    session = FakeSession(FakeResponse(payload={"result": 1}), FakeResponse(payload={"result": 2}))
    c = BitrixClient(WEBHOOK, session=session, min_interval=0.5, sleep=fake_sleep, monotonic=lambda: now[0])
    c.call("a")
    now[0] += 0.2
    c.call("b")
    assert sleeps == [pytest.approx(0.3)]


def test_list_all_follows_next_until_exhausted_and_unwraps_nested_key():
    page1 = FakeResponse(payload={"result": {"items": [{"id": 1}, {"id": 2}]}, "next": 2})
    page2 = FakeResponse(payload={"result": {"items": [{"id": 3}]}})
    c, session, _ = client(page1, page2)
    assert c.list_all("crm.item.list", {"entityTypeId": 1146}, key="items") == [{"id": 1}, {"id": 2}, {"id": 3}]
    assert [p[1]["start"] for p in session.posts] == [0, 2]


def test_list_all_respects_the_limit():
    page = FakeResponse(payload={"result": [{"id": i} for i in range(50)], "next": 50})
    c, _, _ = client(page)
    assert len(c.list_all("crm.contact.list", {}, limit=10)) == 10


def test_download_returns_bytes_for_the_portal_host_without_following_redirects():
    url = f"https://portal.bitrix24.com.br/rest/1/{TOKEN}/download/?token=x"
    c, session, _ = client(FakeResponse(headers={"content-length": "6"}, chunks=[b"%PDF-1", b""]))
    assert c.download(url, max_bytes=100) == b"%PDF-1"
    assert session.gets[0][1]["allow_redirects"] is False


def test_download_rejects_other_hosts_and_non_https():
    c, session, _ = client()
    for url in ("https://evil.example.com/a.pdf", "http://portal.bitrix24.com.br/a.pdf"):
        with pytest.raises(BitrixError) as info:
            c.download(url, max_bytes=100)
        assert info.value.code == "DOWNLOAD_HOST"
    assert session.gets == []


def test_download_enforces_the_size_limit_by_header_and_by_stream():
    url = "https://portal.bitrix24.com.br/d"
    c, _, _ = client(FakeResponse(headers={"content-length": "500"}, chunks=[b"x"]))
    with pytest.raises(BitrixError) as by_header:
        c.download(url, max_bytes=100)
    assert by_header.value.code == "TOO_LARGE"
    c, _, _ = client(FakeResponse(chunks=[b"x" * 60, b"x" * 60]))
    with pytest.raises(BitrixError) as by_stream:
        c.download(url, max_bytes=100)
    assert by_stream.value.code == "TOO_LARGE"


def test_download_http_error_status_is_reported():
    c, _, _ = client(FakeResponse(status=403))
    with pytest.raises(BitrixError) as info:
        c.download("https://portal.bitrix24.com.br/d", max_bytes=100)
    assert info.value.code == "HTTP_403"


def test_redact_hides_the_token_everywhere():
    text = f"GET {WEBHOOK}profile.json e /rest/1/{TOKEN}/download/"
    cleaned = redact(text, WEBHOOK)
    assert TOKEN not in cleaned and "/rest/1/<TOKEN>/" in cleaned


def test_malformed_webhook_url_is_rejected_without_echoing_it():
    secret = "https://portal.bitrix24.com.br/segredo-no-lugar-errado"
    with pytest.raises(ValueError) as info:
        BitrixClient(secret)
    assert "segredo" not in str(info.value)
