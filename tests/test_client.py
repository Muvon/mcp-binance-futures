"""Unit tests for BinanceClient — HTTP layer, signing, error handling.

Uses respx to mock httpx without hitting the real API.
"""

import hashlib
import hmac
import time
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from client import BASE_URL, BinanceClient, BinanceError


# ── Fixtures ──────────────────────────────────────────────────────────────────

API_KEY = "test_api_key"
API_SECRET = "test_api_secret"


@pytest.fixture
def client():
    return BinanceClient(API_KEY, API_SECRET)


# ── from_env ──────────────────────────────────────────────────────────────────

def test_from_env_ok(monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "k")
    monkeypatch.setenv("BINANCE_API_SECRET", "s")
    c = BinanceClient.from_env()
    assert c._api_key == "k"


def test_from_env_missing(monkeypatch):
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="BINANCE_API_KEY"):
        BinanceClient.from_env()


# ── Signing ───────────────────────────────────────────────────────────────────

def test_sign_adds_timestamp_and_signature(client):
    with patch("time.time", return_value=1_700_000_000.0):
        params = client._sign({"symbol": "BTCUSDT"})

    assert params["timestamp"] == 1_700_000_000_000
    assert params["recvWindow"] == 5000
    assert "signature" in params

    # Verify the signature is correct HMAC-SHA256
    from urllib.parse import urlencode
    check_params = {k: v for k, v in params.items() if k != "signature"}
    expected_sig = hmac.new(
        API_SECRET.encode(), urlencode(check_params).encode(), hashlib.sha256
    ).hexdigest()
    assert params["signature"] == expected_sig


# ── Public GET ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_success(client):
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/fapi/v1/ping").mock(return_value=httpx.Response(200, json={}))
        result = await client.get("/fapi/v1/ping")
    assert result == {}


@pytest.mark.asyncio
async def test_get_passes_params(client):
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.get("/fapi/v1/depth").mock(
            return_value=httpx.Response(200, json={"bids": [], "asks": []})
        )
        await client.get("/fapi/v1/depth", {"symbol": "BTCUSDT", "limit": 20})

    assert "symbol=BTCUSDT" in str(route.calls[0].request.url)


# ── Signed GET ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_signed_includes_signature(client):
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.get("/fapi/v2/balance").mock(
            return_value=httpx.Response(200, json=[])
        )
        await client.get_signed("/fapi/v2/balance")

    url = str(route.calls[0].request.url)
    assert "signature=" in url
    assert "timestamp=" in url
    assert "x-mbx-apikey" in dict(route.calls[0].request.headers)


# ── Signed POST ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_signed_sends_body(client):
    payload = {"orderId": 123, "symbol": "BTCUSDT", "status": "NEW"}
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/fapi/v1/order").mock(
            return_value=httpx.Response(200, json=payload)
        )
        result = await client.post_signed("/fapi/v1/order", {"symbol": "BTCUSDT"})

    body = route.calls[0].request.content.decode()
    assert "signature=" in body
    assert result["orderId"] == 123


# ── Error handling ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_binance_api_error_raised(client):
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/fapi/v1/ping").mock(
            return_value=httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."})
        )
        with pytest.raises(BinanceError) as exc_info:
            await client.get("/fapi/v1/ping")

    assert exc_info.value.code == -1121
    assert "Invalid symbol" in exc_info.value.msg


@pytest.mark.asyncio
async def test_http_error_without_json_raises(client):
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/fapi/v1/ping").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )
        with pytest.raises(BinanceError):
            await client.get("/fapi/v1/ping")


@pytest.mark.asyncio
async def test_binance_error_code_200_in_body_is_ok(client):
    """Binance sometimes returns code=200 in body — should NOT raise."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/fapi/v1/ping").mock(
            return_value=httpx.Response(200, json={"code": 200, "msg": "ok"})
        )
        result = await client.get("/fapi/v1/ping")
    assert result["code"] == 200


# ── DELETE ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_signed(client):
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.delete("/fapi/v1/order").mock(
            return_value=httpx.Response(200, json={"orderId": 99, "status": "CANCELED"})
        )
        result = await client.delete_signed("/fapi/v1/order", {"symbol": "BTCUSDT", "orderId": 99})

    assert result["status"] == "CANCELED"
    body = route.calls[0].request.content.decode()
    assert "signature=" in body


# ── Context manager ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_context_manager_closes():
    async with BinanceClient(API_KEY, API_SECRET) as c:
        assert not c._http.is_closed
    assert c._http.is_closed
