"""Integration-style tests for MCP server tools.

Uses respx to mock Binance API responses and FastMCP's in-process
test client to call tools without spawning a real server process.
"""

import httpx
import pytest
import respx
from fastmcp import Client

import server  # registers all tools on server.mcp

# ── Helpers ───────────────────────────────────────────────────────────────────


def mock_env(monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "test_key")
    monkeypatch.setenv("BINANCE_API_SECRET", "test_secret")


# ── Market data ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ping(monkeypatch):
    mock_env(monkeypatch)
    with respx.mock() as mock:
        mock.get("https://fapi.binance.com/fapi/v1/ping").mock(
            return_value=httpx.Response(200, json={})
        )
        async with Client(server.mcp) as c:
            result = await c.call_tool("ping", {})
    assert result.structured_content == {}


@pytest.mark.asyncio
async def test_get_ticker(monkeypatch):
    mock_env(monkeypatch)
    ticker_resp = {
        "lastPrice": "50000.00",
        "priceChange": "1000.00",
        "priceChangePercent": "2.04",
        "highPrice": "51000.00",
        "lowPrice": "49000.00",
        "volume": "12345.678",
        "quoteVolume": "617283900.00",
    }
    mark_resp = {
        "markPrice": "50010.00",
        "indexPrice": "49990.00",
        "lastFundingRate": "0.0001",
        "nextFundingTime": 1700000000000,
    }
    with respx.mock() as mock:
        mock.get("https://fapi.binance.com/fapi/v1/ticker/24hr").mock(
            return_value=httpx.Response(200, json=ticker_resp)
        )
        mock.get("https://fapi.binance.com/fapi/v1/premiumIndex").mock(
            return_value=httpx.Response(200, json=mark_resp)
        )
        async with Client(server.mcp) as c:
            result = await c.call_tool("get_ticker", {"symbol": "BTCUSDT"})

    data = result.structured_content
    assert data["symbol"] == "BTCUSDT"
    assert data["price"] == "50000.00"
    assert data["markPrice"] == "50010.00"
    assert data["fundingRate"] == "0.0001"


@pytest.mark.asyncio
async def test_get_order_book(monkeypatch):
    mock_env(monkeypatch)
    book = {"lastUpdateId": 1, "bids": [["50000", "1.0"]], "asks": [["50001", "0.5"]]}
    with respx.mock() as mock:
        mock.get("https://fapi.binance.com/fapi/v1/depth").mock(
            return_value=httpx.Response(200, json=book)
        )
        async with Client(server.mcp) as c:
            result = await c.call_tool("get_order_book", {"symbol": "BTCUSDT", "limit": 5})

    assert result.structured_content["bids"][0] == ["50000", "1.0"]


@pytest.mark.asyncio
async def test_get_klines(monkeypatch):
    mock_env(monkeypatch)
    # Binance returns list of lists
    raw = [
        [
            1700000000000,
            "49000",
            "51000",
            "48500",
            "50000",
            "100.0",
            1700003599999,
            "5000000",
            500,
            "60.0",
            "3000000",
            "0",
        ]
    ]
    with respx.mock() as mock:
        mock.get("https://fapi.binance.com/fapi/v1/klines").mock(
            return_value=httpx.Response(200, json=raw)
        )
        async with Client(server.mcp) as c:
            result = await c.call_tool(
                "get_klines", {"symbol": "BTCUSDT", "interval": "1h", "limit": 1}
            )

    candle = result.structured_content["result"][0]
    assert candle["open"] == "49000"
    assert candle["close"] == "50000"
    assert candle["trades"] == 500


@pytest.mark.asyncio
async def test_get_symbol_info(monkeypatch):
    mock_env(monkeypatch)
    exchange_info = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "status": "TRADING",
                "baseAsset": "BTC",
                "quoteAsset": "USDT",
                "pricePrecision": 2,
                "quantityPrecision": 3,
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
                "orderTypes": ["LIMIT", "MARKET"],
                "marginTypes": ["ISOLATED", "CROSSED"],
            }
        ]
    }
    with respx.mock() as mock:
        mock.get("https://fapi.binance.com/fapi/v1/exchangeInfo").mock(
            return_value=httpx.Response(200, json=exchange_info)
        )
        async with Client(server.mcp) as c:
            result = await c.call_tool("get_symbol_info", {"symbol": "BTCUSDT"})

    info = result.structured_content
    assert info["tickSize"] == "0.10"
    assert info["stepSize"] == "0.001"
    assert info["minNotional"] == "5"


# ── Account ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_balance(monkeypatch):
    mock_env(monkeypatch)
    balances = [
        {
            "asset": "USDT",
            "balance": "1000.00",
            "availableBalance": "800.00",
            "crossWalletBalance": "1000.00",
            "crossUnPnl": "50.00",
        },
        {
            "asset": "BNB",
            "balance": "0.00",
            "availableBalance": "0.00",
            "crossWalletBalance": "0.00",
            "crossUnPnl": "0.00",
        },
    ]
    with respx.mock() as mock:
        mock.get("https://fapi.binance.com/fapi/v2/balance").mock(
            return_value=httpx.Response(200, json=balances)
        )
        async with Client(server.mcp) as c:
            result = await c.call_tool("get_balance", {})

    # Zero-balance BNB should be filtered out
    assert len(result.structured_content["result"]) == 1
    assert result.structured_content["result"][0]["asset"] == "USDT"
    assert result.structured_content["result"][0]["unrealizedProfit"] == "50.00"


@pytest.mark.asyncio
async def test_get_positions_filters_zero(monkeypatch):
    mock_env(monkeypatch)
    positions = [
        {
            "symbol": "BTCUSDT",
            "positionAmt": "0.01",
            "entryPrice": "50000",
            "markPrice": "51000",
            "unRealizedProfit": "10.00",
            "leverage": "10",
            "marginType": "isolated",
            "isolatedMargin": "50.00",
            "liquidationPrice": "45000",
            "positionSide": "BOTH",
        },
        {
            "symbol": "ETHUSDT",
            "positionAmt": "0.000",
            "entryPrice": "0",
            "markPrice": "3000",
            "unRealizedProfit": "0",
            "leverage": "5",
            "marginType": "cross",
            "isolatedMargin": "0",
            "liquidationPrice": "0",
            "positionSide": "BOTH",
        },
    ]
    with respx.mock() as mock:
        mock.get("https://fapi.binance.com/fapi/v2/positionRisk").mock(
            return_value=httpx.Response(200, json=positions)
        )
        async with Client(server.mcp) as c:
            result = await c.call_tool("get_positions", {"symbol": "BTCUSDT"})

    assert len(result.structured_content["result"]) == 1
    assert result.structured_content["result"][0]["symbol"] == "BTCUSDT"
    assert result.structured_content["result"][0]["side"] == "LONG"


@pytest.mark.asyncio
async def test_get_account_summary(monkeypatch):
    mock_env(monkeypatch)
    account = {
        "totalWalletBalance": "1000.00",
        "totalUnrealizedProfit": "50.00",
        "totalMarginBalance": "1050.00",
        "totalInitialMargin": "100.00",
        "totalMaintMargin": "50.00",
        "availableBalance": "900.00",
        "maxWithdrawAmount": "900.00",
        "positions": [
            {"positionAmt": "0.01"},
            {"positionAmt": "0.000"},
        ],
    }
    with respx.mock() as mock:
        mock.get("https://fapi.binance.com/fapi/v2/account").mock(
            return_value=httpx.Response(200, json=account)
        )
        async with Client(server.mcp) as c:
            result = await c.call_tool("get_account_summary", {})

    assert result.structured_content["openPositionsCount"] == 1
    assert result.structured_content["availableBalance"] == "900.00"


# ── Orders ────────────────────────────────────────────────────────────────────

ORDER_STUB = {
    "orderId": 123456,
    "clientOrderId": "myOrder1",
    "symbol": "BTCUSDT",
    "status": "NEW",
    "type": "LIMIT",
    "side": "BUY",
    "positionSide": "BOTH",
    "price": "49000.00",
    "origQty": "0.01",
    "executedQty": "0.00",
    "avgPrice": "0.00",
    "stopPrice": "0.00",
    "timeInForce": "GTC",
    "reduceOnly": False,
    "closePosition": False,
    "updateTime": 1700000000000,
}


@pytest.mark.asyncio
async def test_place_limit_order(monkeypatch):
    mock_env(monkeypatch)
    with respx.mock() as mock:
        mock.post("https://fapi.binance.com/fapi/v1/order").mock(
            return_value=httpx.Response(200, json=ORDER_STUB)
        )
        async with Client(server.mcp) as c:
            result = await c.call_tool(
                "place_order",
                {
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "order_type": "LIMIT",
                    "quantity": 0.01,
                    "price": 49000.0,
                    "time_in_force": "GTC",
                },
            )

    assert result.structured_content["orderId"] == 123456
    assert result.structured_content["status"] == "NEW"


@pytest.mark.asyncio
async def test_place_market_order(monkeypatch):
    mock_env(monkeypatch)
    filled = {**ORDER_STUB, "type": "MARKET", "status": "FILLED", "executedQty": "0.01"}
    with respx.mock() as mock:
        mock.post("https://fapi.binance.com/fapi/v1/order").mock(
            return_value=httpx.Response(200, json=filled)
        )
        async with Client(server.mcp) as c:
            result = await c.call_tool(
                "place_order",
                {
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "order_type": "MARKET",
                    "quantity": 0.01,
                },
            )

    assert result.structured_content["status"] == "FILLED"


@pytest.mark.asyncio
async def test_place_stop_market_close(monkeypatch):
    mock_env(monkeypatch)
    stop = {
        **ORDER_STUB,
        "type": "STOP_MARKET",
        "side": "SELL",
        "closePosition": True,
        "stopPrice": "45000.00",
    }
    with respx.mock() as mock:
        mock.post("https://fapi.binance.com/fapi/v1/order").mock(
            return_value=httpx.Response(200, json=stop)
        )
        async with Client(server.mcp) as c:
            result = await c.call_tool(
                "place_order",
                {
                    "symbol": "BTCUSDT",
                    "side": "SELL",
                    "order_type": "STOP_MARKET",
                    "stop_price": 45000.0,
                    "close_position": True,
                },
            )

    assert result.structured_content["closePosition"] is True


@pytest.mark.asyncio
async def test_cancel_order(monkeypatch):
    mock_env(monkeypatch)
    canceled = {**ORDER_STUB, "status": "CANCELED"}
    with respx.mock() as mock:
        mock.delete("https://fapi.binance.com/fapi/v1/order").mock(
            return_value=httpx.Response(200, json=canceled)
        )
        async with Client(server.mcp) as c:
            result = await c.call_tool("cancel_order", {"symbol": "BTCUSDT", "order_id": 123456})

    assert result.structured_content["status"] == "CANCELED"


@pytest.mark.asyncio
async def test_cancel_order_requires_id(monkeypatch):
    mock_env(monkeypatch)
    with respx.mock():
        async with Client(server.mcp) as c:
            with pytest.raises(Exception, match="order_id"):
                await c.call_tool("cancel_order", {"symbol": "BTCUSDT"})


@pytest.mark.asyncio
async def test_cancel_all_orders(monkeypatch):
    mock_env(monkeypatch)
    with respx.mock() as mock:
        mock.delete("https://fapi.binance.com/fapi/v1/allOpenOrders").mock(
            return_value=httpx.Response(
                200, json={"code": 200, "msg": "The operation of cancel all open order is done."}
            )
        )
        async with Client(server.mcp) as c:
            result = await c.call_tool("cancel_all_orders", {"symbol": "BTCUSDT"})

    assert result.structured_content["code"] == 200


@pytest.mark.asyncio
async def test_modify_order(monkeypatch):
    mock_env(monkeypatch)
    modified = {**ORDER_STUB, "price": "48000.00"}
    with respx.mock() as mock:
        mock.put("https://fapi.binance.com/fapi/v1/order").mock(
            return_value=httpx.Response(200, json=modified)
        )
        async with Client(server.mcp) as c:
            result = await c.call_tool(
                "modify_order",
                {
                    "symbol": "BTCUSDT",
                    "order_id": 123456,
                    "side": "BUY",
                    "quantity": 0.01,
                    "price": 48000.0,
                },
            )

    assert result.structured_content["price"] == "48000.00"


@pytest.mark.asyncio
async def test_get_open_orders(monkeypatch):
    mock_env(monkeypatch)
    with respx.mock() as mock:
        mock.get("https://fapi.binance.com/fapi/v1/openOrders").mock(
            return_value=httpx.Response(200, json=[ORDER_STUB])
        )
        async with Client(server.mcp) as c:
            result = await c.call_tool("get_open_orders", {"symbol": "BTCUSDT"})

    assert len(result.structured_content["result"]) == 1
    assert result.structured_content["result"][0]["orderId"] == 123456


# ── Position management ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_leverage(monkeypatch):
    mock_env(monkeypatch)
    with respx.mock() as mock:
        mock.post("https://fapi.binance.com/fapi/v1/leverage").mock(
            return_value=httpx.Response(
                200, json={"leverage": 10, "maxNotionalValue": "1000000", "symbol": "BTCUSDT"}
            )
        )
        async with Client(server.mcp) as c:
            result = await c.call_tool("set_leverage", {"symbol": "BTCUSDT", "leverage": 10})

    assert result.structured_content["leverage"] == 10


@pytest.mark.asyncio
async def test_set_margin_type(monkeypatch):
    mock_env(monkeypatch)
    with respx.mock() as mock:
        mock.post("https://fapi.binance.com/fapi/v1/marginType").mock(
            return_value=httpx.Response(200, json={"code": 200, "msg": "success"})
        )
        async with Client(server.mcp) as c:
            result = await c.call_tool(
                "set_margin_type", {"symbol": "BTCUSDT", "margin_type": "ISOLATED"}
            )

    assert result.structured_content["code"] == 200


@pytest.mark.asyncio
async def test_set_margin_type_already_set(monkeypatch):
    """Error -4046 (already that type) should be swallowed and return success."""
    mock_env(monkeypatch)
    with respx.mock() as mock:
        mock.post("https://fapi.binance.com/fapi/v1/marginType").mock(
            return_value=httpx.Response(
                400, json={"code": -4046, "msg": "No need to change margin type."}
            )
        )
        async with Client(server.mcp) as c:
            result = await c.call_tool(
                "set_margin_type", {"symbol": "BTCUSDT", "margin_type": "ISOLATED"}
            )

    assert result.structured_content["code"] == 200


@pytest.mark.asyncio
async def test_adjust_isolated_margin(monkeypatch):
    mock_env(monkeypatch)
    with respx.mock() as mock:
        mock.post("https://fapi.binance.com/fapi/v1/positionMargin").mock(
            return_value=httpx.Response(
                200,
                json={
                    "amount": 100.0,
                    "code": 200,
                    "msg": "Successfully modify position margin.",
                    "type": 1,
                },
            )
        )
        async with Client(server.mcp) as c:
            result = await c.call_tool(
                "adjust_isolated_margin",
                {
                    "symbol": "BTCUSDT",
                    "amount": 100.0,
                    "direction": "add",
                },
            )

    assert result.structured_content["code"] == 200


@pytest.mark.asyncio
async def test_get_position_mode(monkeypatch):
    mock_env(monkeypatch)
    with respx.mock() as mock:
        mock.get("https://fapi.binance.com/fapi/v1/positionSide/dual").mock(
            return_value=httpx.Response(200, json={"dualSidePosition": False})
        )
        async with Client(server.mcp) as c:
            result = await c.call_tool("get_position_mode", {})

    assert result.structured_content["mode"] == "One-way"
    assert result.structured_content["hedgeMode"] is False


@pytest.mark.asyncio
async def test_get_leverage_brackets(monkeypatch):
    mock_env(monkeypatch)
    brackets = [
        {
            "symbol": "BTCUSDT",
            "brackets": [
                {
                    "bracket": 1,
                    "initialLeverage": 125,
                    "notionalCap": 50000,
                    "maintMarginRatio": 0.004,
                },
                {
                    "bracket": 2,
                    "initialLeverage": 100,
                    "notionalCap": 250000,
                    "maintMarginRatio": 0.005,
                },
            ],
        }
    ]
    with respx.mock() as mock:
        mock.get("https://fapi.binance.com/fapi/v1/leverageBracket").mock(
            return_value=httpx.Response(200, json=brackets)
        )
        async with Client(server.mcp) as c:
            result = await c.call_tool("get_leverage_brackets", {"symbol": "BTCUSDT"})

    assert result.structured_content["result"][0]["initialLeverage"] == 125
