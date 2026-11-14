"""Nessie client: live-mode behavior is tested against httpx.MockTransport, so no
network and no API key are needed."""

import logging

import httpx
import pytest

from app import config
from app.services import nessie

SECRET = "secret-key-123"


@pytest.fixture
def live(monkeypatch):
    """Live mode with a stubbed HTTP transport. Returns a list that records requests."""
    monkeypatch.setattr(config, "USE_MOCK_DATA", False)
    monkeypatch.setattr(config, "NESSIE_API_KEY", SECRET)
    monkeypatch.setattr(config, "NESSIE_BASE_URL", "https://nessie.test")
    seen: list[httpx.Request] = []

    def install(handler):
        def wrapped(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        monkeypatch.setattr(nessie, "_http", httpx.Client(transport=httpx.MockTransport(wrapped)))
        return seen

    return install


def test_merchant_category_is_returned_raw_as_a_string(live):
    # Real Nessie returns category as a string; normalization happens later in ingest.
    live(lambda r: httpx.Response(200, json={"_id": "m1", "name": "Amazon", "category": "shopping"}))
    result = nessie.fetch_merchants(["m1"])
    assert result["m1"]["category"] == "shopping"


def test_api_key_is_sent_as_query_param(live):
    seen = live(lambda r: httpx.Response(200, json=[]))
    nessie.fetch_accounts("cust1")
    assert seen[0].url.params["key"] == SECRET
    assert seen[0].url.scheme == "https"


def test_missing_merchant_is_skipped_not_fatal(live):
    live(lambda r: httpx.Response(404, json={"message": "not found"}))
    assert nessie.fetch_merchants(["gone"]) == {}


def test_merchant_ids_are_fetched_once(live):
    seen = live(lambda r: httpx.Response(200, json={"_id": "m1", "name": "A", "category": "x"}))
    nessie.fetch_merchants(["m1", "m1", "m1"])
    assert len(seen) == 1


def test_server_error_message_never_contains_key_or_url(live):
    live(lambda r: httpx.Response(503, text="down"))
    with pytest.raises(nessie.NessieError) as exc:
        nessie.fetch_accounts("cust1")
    message = str(exc.value)
    assert "503" in message
    assert SECRET not in message
    assert "nessie.test" not in message


def test_connection_error_message_never_contains_key_or_url(live):
    def boom(request):
        raise httpx.ConnectError("connect failed", request=request)

    live(boom)
    with pytest.raises(nessie.NessieError) as exc:
        nessie.fetch_accounts("cust1")
    assert SECRET not in str(exc.value)
    assert "nessie.test" not in str(exc.value)
    assert exc.value.__cause__ is None  # no chained exception carrying the URL


def test_timeout_becomes_nessie_error(live):
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    live(slow)
    with pytest.raises(nessie.NessieError, match="timed out"):
        nessie.fetch_accounts("cust1")


@pytest.mark.parametrize("bad_id", ["../etc/passwd", "a b", "", "x" * 65, "id?key=1", "a/b"])
def test_invalid_ids_are_rejected_before_any_request(live, bad_id):
    seen = live(lambda r: httpx.Response(200, json=[]))
    with pytest.raises(nessie.InvalidIdError):
        nessie.fetch_accounts(bad_id)
    assert seen == []


@pytest.mark.parametrize("good_id", ["cust_demo", "6553f1a2b3c4d5e6f7a8b9c0", "01514c1d-d13c-4879-a669-f7472fa490d3"])
def test_valid_id_shapes_are_accepted(good_id):
    assert nessie.validate_id(good_id) == good_id


@pytest.mark.parametrize("name", ["httpx", "httpcore"])
def test_http_request_logging_is_silenced_because_urls_contain_the_key(name):
    assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING


# ---------- mock mode ----------
def test_mock_mode_serves_demo_customer(monkeypatch):
    monkeypatch.setattr(config, "USE_MOCK_DATA", True)
    cid = nessie.default_customer_id()
    assert cid == "cust_demo"
    assert nessie.fetch_customer(cid)["_id"] == "cust_demo"
    accounts = nessie.fetch_accounts(cid)
    assert [a["_id"] for a in accounts] == ["acct_demo"]
    assert len(nessie.fetch_purchases("acct_demo")) == 60


def test_mock_mode_unknown_customer_is_none(monkeypatch):
    monkeypatch.setattr(config, "USE_MOCK_DATA", True)
    assert nessie.fetch_customer("someone_else") is None
    assert nessie.fetch_accounts("someone_else") == []
