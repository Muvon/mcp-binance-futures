"""MCP server for Binance USDT-M Futures trading.

Exposes tools for market data, account state, order management,
and position/margin control — all scoped to a single symbol where
it makes sense so an LLM can reason about one instrument at a time.

Environment variables required:
    BINANCE_API_KEY     — Binance API key
    BINANCE_API_SECRET  — Binance API secret
"""

from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal, cast

from fastmcp import Context, FastMCP
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from pydantic import Field

from mcp_binance_futures.client import BinanceClient, BinanceError

# ── Lifespan: one shared client for the server's lifetime ─────────────────────


@asynccontextmanager
async def lifespan(server: FastMCP) -> Any:
    client = BinanceClient.from_env()
    try:
        yield {"client": client}
    finally:
        await client.close()


mcp = FastMCP(
    name="Binance Futures",
    instructions=(
        "Tools for Binance USDT-M Futures: market data, account balances, "
        "open positions, order placement/modification/cancellation, leverage "
        "and margin-type control. Most tools accept a `symbol` parameter "
        "(e.g. 'BTCUSDT') to scope the operation to one instrument."
    ),
    lifespan=lifespan,
)

# Add error handling middleware to catch all exceptions and convert to MCP errors
mcp.add_middleware(ErrorHandlingMiddleware(include_traceback=False))


# ── Helpers ───────────────────────────────────────────────────────────────────


def _client(ctx: Context) -> BinanceClient:
    """Extract BinanceClient from lifespan context."""
    assert ctx.request_context is not None
    return cast(BinanceClient, ctx.request_context.lifespan_context["client"])


def _strip_none(d: dict[str, Any]) -> dict[str, Any]:
    """Remove keys whose value is None so they are not sent to Binance."""
    return {k: v for k, v in d.items() if v is not None}


# ═════════════════════════════════════════════════════════════════════════════
# MARKET DATA  (public — no auth)
# ═════════════════════════════════════════════════════════════════════════════


@mcp.tool
async def ping(ctx: Context) -> dict[str, Any]:
    """Test connectivity to the Binance Futures API. Returns {} on success."""
    return cast(dict[str, Any], await _client(ctx).get("/fapi/v1/ping"))


@mcp.tool
async def get_ticker(
    ctx: Context,
    symbol: Annotated[str, Field(description="Trading pair, e.g. 'BTCUSDT'")],
) -> dict:
    """Get latest price, 24 h stats, and mark/index prices for a symbol.

    Returns a merged dict with:
    - price, priceChange, priceChangePct, high, low, volume, quoteVolume
    - markPrice, indexPrice, fundingRate, nextFundingTime
    """
    c = _client(ctx)
    ticker, mark = await _gather(
        c.get("/fapi/v1/ticker/24hr", {"symbol": symbol}),
        c.get("/fapi/v1/premiumIndex", {"symbol": symbol}),
    )
    return {
        "symbol": symbol,
        "price": ticker["lastPrice"],
        "priceChange": ticker["priceChange"],
        "priceChangePct": ticker["priceChangePercent"],
        "high": ticker["highPrice"],
        "low": ticker["lowPrice"],
        "volume": ticker["volume"],
        "quoteVolume": ticker["quoteVolume"],
        "markPrice": mark["markPrice"],
        "indexPrice": mark["indexPrice"],
        "fundingRate": mark["lastFundingRate"],
        "nextFundingTime": mark["nextFundingTime"],
    }


@mcp.tool
async def get_order_book(
    ctx: Context,
    symbol: Annotated[str, Field(description="Trading pair, e.g. 'BTCUSDT'")],
    limit: Annotated[
        Literal[5, 10, 20, 50, 100, 500, 1000],
        Field(description="Depth levels: 5, 10, 20, 50, 100, 500, 1000"),
    ] = 20,
) -> dict[str, Any]:
    """Get order book bids and asks for a symbol.

    Returns top `limit` bids and asks as [[price, qty], ...] lists.
    """
    return cast(
        dict[str, Any], await _client(ctx).get("/fapi/v1/depth", {"symbol": symbol, "limit": limit})
    )


@mcp.tool
async def get_recent_trades(
    ctx: Context,
    symbol: Annotated[str, Field(description="Trading pair, e.g. 'BTCUSDT'")],
    limit: Annotated[int, Field(description="Number of trades (max 1000)", ge=1, le=1000)] = 50,
) -> list[dict[str, Any]]:
    """Get the most recent public trades for a symbol."""
    return cast(
        list[dict[str, Any]],
        await _client(ctx).get("/fapi/v1/trades", {"symbol": symbol, "limit": limit}),
    )


@mcp.tool
async def get_klines(
    ctx: Context,
    symbol: Annotated[str, Field(description="Trading pair, e.g. 'BTCUSDT'")],
    interval: Annotated[
        Literal[
            "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w"
        ],
        Field(description="Candlestick interval"),
    ] = "1h",
    limit: Annotated[int, Field(description="Number of candles (max 1500)", ge=1, le=1500)] = 100,
) -> list[dict]:
    """Get OHLCV candlestick data for a symbol.

    Returns list of dicts with: openTime, open, high, low, close, volume,
    closeTime, quoteVolume, trades, takerBuyVolume, takerBuyQuoteVolume.
    """
    raw = await _client(ctx).get(
        "/fapi/v1/klines",
        {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        },
    )
    keys = [
        "openTime",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "closeTime",
        "quoteVolume",
        "trades",
        "takerBuyVolume",
        "takerBuyQuoteVolume",
        "_ignore",
    ]
    return [{k: v for k, v in zip(keys, row, strict=False) if k != "_ignore"} for row in raw]


@mcp.tool
async def get_symbol_info(
    ctx: Context,
    symbol: Annotated[str, Field(description="Trading pair, e.g. 'BTCUSDT'")],
) -> dict:
    """Get trading rules for a symbol: tick size, lot size, min notional, max leverage, etc."""
    info = await _client(ctx).get("/fapi/v1/exchangeInfo")
    for s in info["symbols"]:
        if s["symbol"] == symbol.upper():
            # Extract the most useful filter values inline
            filters = {f["filterType"]: f for f in s.get("filters", [])}
            return {
                "symbol": s["symbol"],
                "status": s["status"],
                "baseAsset": s["baseAsset"],
                "quoteAsset": s["quoteAsset"],
                "pricePrecision": s["pricePrecision"],
                "quantityPrecision": s["quantityPrecision"],
                "tickSize": filters.get("PRICE_FILTER", {}).get("tickSize"),
                "stepSize": filters.get("LOT_SIZE", {}).get("stepSize"),
                "minQty": filters.get("LOT_SIZE", {}).get("minQty"),
                "minNotional": filters.get("MIN_NOTIONAL", {}).get("notional"),
                "maxLeverage": s.get("leverageBracket", [{}])[0].get("initialLeverage")
                if s.get("leverageBracket")
                else None,
                "marginTypes": s.get("marginTypes", []),
                "orderTypes": s.get("orderTypes", []),
            }
    raise ValueError(f"Symbol '{symbol}' not found on Binance Futures")


# ═════════════════════════════════════════════════════════════════════════════
# ACCOUNT  (signed USER_DATA)
# ═════════════════════════════════════════════════════════════════════════════


@mcp.tool
async def get_balance(ctx: Context) -> list[dict]:
    """Get futures wallet balances for all assets with non-zero balance.

    Returns list of: asset, balance, availableBalance, crossWalletBalance,
    unrealizedProfit.
    """
    data = await _client(ctx).get_signed("/fapi/v2/balance")
    return [
        {
            "asset": b["asset"],
            "balance": b["balance"],
            "availableBalance": b["availableBalance"],
            "crossWalletBalance": b["crossWalletBalance"],
            "unrealizedProfit": b["crossUnPnl"],
        }
        for b in data
        if float(b["balance"]) != 0
    ]


@mcp.tool
async def get_positions(
    ctx: Context,
    symbol: Annotated[
        str | None,
        Field(description="Filter to one symbol, e.g. 'BTCUSDT'. Omit for all open positions."),
    ] = None,
) -> list[dict]:
    """Get current open positions (non-zero size).

    Per position: symbol, side, size, entryPrice, markPrice, unrealizedPnl,
    percentage, leverage, marginType, isolatedMargin, liquidationPrice.
    """
    params = {"symbol": symbol} if symbol else {}
    data = await _client(ctx).get_signed("/fapi/v2/positionRisk", params)
    result = []
    for p in data:
        size = float(p["positionAmt"])
        if size == 0:
            continue
        entry = float(p["entryPrice"])
        pnl = float(p["unRealizedProfit"])
        notional = abs(size * entry)
        pct = (pnl / notional * 100) if notional else 0
        result.append(
            {
                "symbol": p["symbol"],
                "side": "LONG" if size > 0 else "SHORT",
                "size": p["positionAmt"],
                "entryPrice": p["entryPrice"],
                "markPrice": p["markPrice"],
                "unrealizedPnl": p["unRealizedProfit"],
                "pnlPct": round(pct, 4),
                "leverage": p["leverage"],
                "marginType": p["marginType"],
                "isolatedMargin": p["isolatedMargin"],
                "liquidationPrice": p["liquidationPrice"],
                "positionSide": p["positionSide"],
            }
        )
    return result


@mcp.tool
async def get_account_summary(ctx: Context) -> dict:
    """Get account-level summary: total balance, unrealized PnL, margin ratio, positions count."""
    data = await _client(ctx).get_signed("/fapi/v2/account")
    return {
        "totalWalletBalance": data["totalWalletBalance"],
        "totalUnrealizedProfit": data["totalUnrealizedProfit"],
        "totalMarginBalance": data["totalMarginBalance"],
        "totalInitialMargin": data["totalInitialMargin"],
        "totalMaintMargin": data["totalMaintMargin"],
        "availableBalance": data["availableBalance"],
        "maxWithdrawAmount": data["maxWithdrawAmount"],
        "openPositionsCount": sum(1 for p in data["positions"] if float(p["positionAmt"]) != 0),
    }


# ═════════════════════════════════════════════════════════════════════════════
# ORDERS  (signed TRADE)
# ═════════════════════════════════════════════════════════════════════════════


@mcp.tool
async def get_open_orders(
    ctx: Context,
    symbol: Annotated[str, Field(description="Trading pair, e.g. 'BTCUSDT'")],
) -> list[dict]:
    """Get all open orders for a symbol.

    Returns list of: orderId, clientOrderId, type, side, price, origQty,
    executedQty, status, timeInForce, reduceOnly, positionSide.
    """
    data = await _client(ctx).get_signed("/fapi/v1/openOrders", {"symbol": symbol})
    return [_format_order(o) for o in data]


@mcp.tool
async def get_order(
    ctx: Context,
    symbol: Annotated[str, Field(description="Trading pair, e.g. 'BTCUSDT'")],
    order_id: Annotated[int | None, Field(description="Binance order ID")] = None,
    client_order_id: Annotated[str | None, Field(description="Your custom client order ID")] = None,
) -> dict:
    """Get details of a specific order by orderId or clientOrderId."""
    if not order_id and not client_order_id:
        raise ValueError("Provide either order_id or client_order_id")
    params = _strip_none(
        {"symbol": symbol, "orderId": order_id, "origClientOrderId": client_order_id}
    )
    return _format_order(await _client(ctx).get_signed("/fapi/v1/order", params))


@mcp.tool
async def get_order_history(
    ctx: Context,
    symbol: Annotated[str, Field(description="Trading pair, e.g. 'BTCUSDT'")],
    limit: Annotated[
        int, Field(description="Number of orders to return (max 1000)", ge=1, le=1000)
    ] = 50,
) -> list[dict]:
    """Get recent order history for a symbol (all statuses)."""
    data = await _client(ctx).get_signed("/fapi/v1/allOrders", {"symbol": symbol, "limit": limit})
    return [_format_order(o) for o in data]


@mcp.tool
async def place_order(
    ctx: Context,
    symbol: Annotated[str, Field(description="Trading pair, e.g. 'BTCUSDT'")],
    side: Annotated[Literal["BUY", "SELL"], Field(description="Order direction")],
    order_type: Annotated[
        Literal[
            "LIMIT",
            "MARKET",
            "STOP",
            "STOP_MARKET",
            "TAKE_PROFIT",
            "TAKE_PROFIT_MARKET",
            "TRAILING_STOP_MARKET",
        ],
        Field(description="Order type"),
    ],
    quantity: Annotated[
        float | None,
        Field(description="Order quantity in base asset. Required for most types.", gt=0),
    ] = None,
    price: Annotated[
        float | None, Field(description="Limit price. Required for LIMIT, STOP, TAKE_PROFIT.")
    ] = None,
    stop_price: Annotated[
        float | None,
        Field(
            description="Trigger price. Required for STOP, STOP_MARKET, TAKE_PROFIT, TAKE_PROFIT_MARKET."
        ),
    ] = None,
    time_in_force: Annotated[
        Literal["GTC", "IOC", "FOK", "GTX"] | None,
        Field(description="Time in force. Required for LIMIT orders."),
    ] = None,
    reduce_only: Annotated[
        bool | None, Field(description="If True, order can only reduce an existing position.")
    ] = None,
    close_position: Annotated[
        bool | None,
        Field(
            description=(
                "If True, closes the entire position at trigger. "
                "ONLY valid with order_type STOP_MARKET or TAKE_PROFIT_MARKET. "
                "Cannot be used with MARKET, LIMIT, or other types."
            )
        ),
    ] = None,
    position_side: Annotated[
        Literal["BOTH", "LONG", "SHORT"] | None,
        Field(description="Required in Hedge Mode. Use BOTH for One-way mode."),
    ] = None,
    client_order_id: Annotated[
        str | None, Field(description="Optional custom order ID (max 36 chars).")
    ] = None,
    callback_rate: Annotated[
        float | None,
        Field(
            description="Trailing stop callback rate in % (0.1–5). Only for TRAILING_STOP_MARKET."
        ),
    ] = None,
) -> dict:
    """Place a new futures order.

    Common patterns:
    - Market buy:  side=BUY, type=MARKET, quantity=0.01
    - Limit sell:  side=SELL, type=LIMIT, quantity=0.01, price=50000, time_in_force=GTC
    - Stop loss:   side=SELL, type=STOP_MARKET, stop_price=45000, close_position=True
    - Take profit: side=SELL, type=TAKE_PROFIT_MARKET, stop_price=60000, close_position=True
    """
    if close_position and order_type not in ("STOP_MARKET", "TAKE_PROFIT_MARKET"):
        raise ValueError(
            f"close_position=True is only valid with STOP_MARKET or TAKE_PROFIT_MARKET, "
            f"got order_type={order_type!r}"
        )
    if reduce_only and close_position:
        raise ValueError("reduce_only and close_position cannot both be True")
    if order_type == "LIMIT" and not time_in_force:
        raise ValueError("time_in_force is required for LIMIT orders")
    if callback_rate is not None and order_type != "TRAILING_STOP_MARKET":
        raise ValueError(
            f"callback_rate is only valid for TRAILING_STOP_MARKET, got order_type={order_type!r}"
        )
    params = _strip_none(
        {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": quantity,
            "price": price,
            "stopPrice": stop_price,
            "timeInForce": time_in_force,
            "reduceOnly": str(reduce_only).lower() if reduce_only is not None else None,
            "closePosition": str(close_position).lower() if close_position is not None else None,
            "positionSide": position_side,
            "newClientOrderId": client_order_id,
            "callbackRate": callback_rate,
        }
    )
    return _format_order(await _client(ctx).post_signed("/fapi/v1/order", params))


@mcp.tool
async def modify_order(
    ctx: Context,
    symbol: Annotated[str, Field(description="Trading pair, e.g. 'BTCUSDT'")],
    side: Annotated[
        Literal["BUY", "SELL"], Field(description="Must match the original order side")
    ],
    order_id: Annotated[int | None, Field(description="Binance order ID to modify")] = None,
    client_order_id: Annotated[str | None, Field(description="Client order ID to modify")] = None,
    quantity: Annotated[float | None, Field(description="New quantity", gt=0)] = None,
    price: Annotated[float | None, Field(description="New limit price")] = None,
) -> dict:
    """Modify price or quantity of an existing open LIMIT order (PUT /fapi/v1/order)."""
    if not order_id and not client_order_id:
        raise ValueError("Provide either order_id or client_order_id")
    params = _strip_none(
        {
            "symbol": symbol,
            "orderId": order_id,
            "origClientOrderId": client_order_id,
            "side": side,
            "quantity": quantity,
            "price": price,
        }
    )
    return _format_order(await _client(ctx).put_signed("/fapi/v1/order", params))


@mcp.tool
async def cancel_order(
    ctx: Context,
    symbol: Annotated[str, Field(description="Trading pair, e.g. 'BTCUSDT'")],
    order_id: Annotated[int | None, Field(description="Binance order ID to cancel")] = None,
    client_order_id: Annotated[str | None, Field(description="Client order ID to cancel")] = None,
) -> dict:
    """Cancel a single open order by orderId or clientOrderId."""
    if not order_id and not client_order_id:
        raise ValueError("Provide either order_id or client_order_id")
    params = _strip_none(
        {"symbol": symbol, "orderId": order_id, "origClientOrderId": client_order_id}
    )
    return _format_order(await _client(ctx).delete_signed("/fapi/v1/order", params))


@mcp.tool
async def cancel_all_orders(
    ctx: Context,
    symbol: Annotated[str, Field(description="Trading pair, e.g. 'BTCUSDT'")],
) -> dict:
    """Cancel ALL open orders for a symbol in one call."""
    return await _client(ctx).delete_signed("/fapi/v1/allOpenOrders", {"symbol": symbol})


@mcp.tool
async def get_trade_history(
    ctx: Context,
    symbol: Annotated[str, Field(description="Trading pair, e.g. 'BTCUSDT'")],
    limit: Annotated[int, Field(description="Number of trades (max 1000)", ge=1, le=1000)] = 50,
) -> list[dict]:
    """Get your personal trade execution history for a symbol (fills)."""
    return await _client(ctx).get_signed("/fapi/v1/userTrades", {"symbol": symbol, "limit": limit})


# ═════════════════════════════════════════════════════════════════════════════
# POSITION MANAGEMENT  (signed TRADE)
# ═════════════════════════════════════════════════════════════════════════════


@mcp.tool
async def set_leverage(
    ctx: Context,
    symbol: Annotated[str, Field(description="Trading pair, e.g. 'BTCUSDT'")],
    leverage: Annotated[int, Field(description="Leverage multiplier (1–125)", ge=1, le=125)],
) -> dict:
    """Set leverage for a symbol. Returns the new leverage and max notional value."""
    return await _client(ctx).post_signed(
        "/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage}
    )


@mcp.tool
async def set_margin_type(
    ctx: Context,
    symbol: Annotated[str, Field(description="Trading pair, e.g. 'BTCUSDT'")],
    margin_type: Annotated[
        Literal["ISOLATED", "CROSSED"], Field(description="ISOLATED or CROSSED margin mode")
    ],
) -> dict:
    """Switch margin type for a symbol between ISOLATED and CROSSED.

    Note: Cannot change margin type while a position or open order exists.
    """
    try:
        return await _client(ctx).post_signed(
            "/fapi/v1/marginType",
            {
                "symbol": symbol,
                "marginType": margin_type,
            },
        )
    except BinanceError as e:
        # -4046 = "No need to change margin type" — treat as success
        if e.code == -4046:
            return {"code": 200, "msg": f"Margin type already {margin_type}"}
        raise


@mcp.tool
async def adjust_isolated_margin(
    ctx: Context,
    symbol: Annotated[str, Field(description="Trading pair, e.g. 'BTCUSDT'")],
    amount: Annotated[float, Field(description="Amount to add or remove", gt=0)],
    direction: Annotated[
        Literal["add", "remove"],
        Field(description="'add' to increase margin, 'remove' to decrease"),
    ],
    position_side: Annotated[
        Literal["BOTH", "LONG", "SHORT"] | None, Field(description="Required in Hedge Mode")
    ] = None,
) -> dict:
    """Add or remove margin from an isolated position.

    Only valid when the symbol is in ISOLATED margin mode with an open position.
    """
    params = _strip_none(
        {
            "symbol": symbol,
            "amount": amount,
            "type": 1 if direction == "add" else 2,  # Binance: 1=add, 2=remove
            "positionSide": position_side,
        }
    )
    return await _client(ctx).post_signed("/fapi/v1/positionMargin", params)


@mcp.tool
async def set_position_mode(
    ctx: Context,
    hedge_mode: Annotated[
        bool, Field(description="True = Hedge Mode (LONG+SHORT), False = One-way Mode")
    ],
) -> dict:
    """Switch between One-way Mode and Hedge Mode for the account.

    Note: Cannot change while any positions or open orders exist.
    """
    return await _client(ctx).post_signed(
        "/fapi/v1/positionSide/dual",
        {
            "dualSidePosition": str(hedge_mode).lower(),
        },
    )


@mcp.tool
async def get_position_mode(ctx: Context) -> dict:
    """Get current position mode: Hedge Mode or One-way Mode."""
    data = await _client(ctx).get_signed("/fapi/v1/positionSide/dual")
    return {
        "hedgeMode": data["dualSidePosition"],
        "mode": "Hedge" if data["dualSidePosition"] else "One-way",
    }


@mcp.tool
async def get_leverage_brackets(
    ctx: Context,
    symbol: Annotated[str, Field(description="Trading pair, e.g. 'BTCUSDT'")],
) -> list[dict]:
    """Get leverage brackets for a symbol: max leverage per notional tier with maintenance margin rates."""
    data = await _client(ctx).get_signed("/fapi/v1/leverageBracket", {"symbol": symbol})
    # Response is a list; find the matching symbol
    for entry in data:
        if entry["symbol"] == symbol.upper():
            return entry["brackets"]
    return data[0]["brackets"] if data else []


# ═════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═════════════════════════════════════════════════════════════════════════════


def _format_order(o: dict) -> dict:
    """Normalize an order dict to the fields most useful for decision-making."""
    return {
        "orderId": o.get("orderId"),
        "clientOrderId": o.get("clientOrderId"),
        "symbol": o.get("symbol"),
        "status": o.get("status"),
        "type": o.get("type"),
        "side": o.get("side"),
        "positionSide": o.get("positionSide"),
        "price": o.get("price"),
        "origQty": o.get("origQty"),
        "executedQty": o.get("executedQty"),
        "avgPrice": o.get("avgPrice"),
        "stopPrice": o.get("stopPrice"),
        "timeInForce": o.get("timeInForce"),
        "reduceOnly": o.get("reduceOnly"),
        "closePosition": o.get("closePosition"),
        "updateTime": o.get("updateTime"),
    }


async def _gather(*coros):
    """Run coroutines concurrently and return results in order."""
    import asyncio

    return await asyncio.gather(*coros)


# ── Entry point ───────────────────────────────────────────────────────────────


def main():
    mcp.run()


if __name__ == "__main__":
    main()
