# mcp-binance-futures

MCP server for **Binance USDT-M Futures** trading. Exposes tools for market data, account state, order management, and position/margin control — designed to give an LLM everything it needs to monitor, place, and manage futures trades.

Built with [FastMCP](https://github.com/jlowin/fastmcp) and [httpx](https://www.python-httpx.org/).

<a href="https://glama.ai/mcp/servers/Muvon/mcp-binance-futures">
  <img width="380" height="200" src="https://glama.ai/mcp/servers/Muvon/mcp-binance-futures/badge" alt="mcp-binance-futures MCP server" />
</a>

---

## Tools

### Market Data (public, no auth)

| Tool | Description |
|---|---|
| `ping` | Test API connectivity |
| `get_ticker` | Price, 24 h stats, mark price, funding rate for a symbol |
| `get_order_book` | Top N bids/asks for a symbol |
| `get_recent_trades` | Latest public trades |
| `get_klines` | OHLCV candlestick data (1m → 1w) |
| `get_symbol_info` | Trading rules: tick size, lot size, min notional, order types |

### Account (signed)

| Tool | Description |
|---|---|
| `get_balance` | Wallet balances (non-zero assets only) |
| `get_positions` | Open positions with PnL, leverage, margin type — optionally scoped to one symbol |
| `get_account_summary` | Total balance, unrealized PnL, margin usage, open position count |

### Orders (signed)

| Tool | Description |
|---|---|
| `place_order` | Place LIMIT, MARKET, STOP, STOP\_MARKET, TAKE\_PROFIT, TAKE\_PROFIT\_MARKET, TRAILING\_STOP\_MARKET |
| `modify_order` | Change price or quantity of an open LIMIT order |
| `cancel_order` | Cancel a single order by ID |
| `cancel_all_orders` | Cancel all open orders for a symbol |
| `get_open_orders` | List all open orders for a symbol |
| `get_order` | Get a specific order by ID |
| `get_order_history` | Recent order history (all statuses) |
| `get_trade_history` | Personal fill history for a symbol |

### Position Management (signed)

| Tool | Description |
|---|---|
| `set_leverage` | Set leverage multiplier (1–125×) for a symbol |
| `set_margin_type` | Switch between `ISOLATED` and `CROSSED` margin |
| `adjust_isolated_margin` | Add or remove margin from an isolated position |
| `set_position_mode` | Switch between One-way and Hedge Mode |
| `get_position_mode` | Get current position mode |
| `get_leverage_brackets` | Leverage tiers with maintenance margin rates |

---

## Setup

### Requirements

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip

### Install

```bash
# with uv (recommended)
uv sync

# or with pip
pip install -e .
```

### API Keys

Create a Binance API key with **Futures trading** enabled. Set environment variables:

```bash
export BINANCE_API_KEY="your_api_key"
export BINANCE_API_SECRET="your_api_secret"
```

> **Security**: Use IP whitelisting on your Binance API key. Never commit keys to version control.

---

## Running

```bash
# stdio transport (default — for MCP clients like Claude Desktop)
python server.py

# or via the installed script
mcp-binance-futures
```

---

## MCP Client Configuration

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "binance-futures": {
      "command": "python",
      "args": ["/path/to/mcp-binance-futures/server.py"],
      "env": {
        "BINANCE_API_KEY": "your_api_key",
        "BINANCE_API_SECRET": "your_api_secret"
      }
    }
  }
}
```

### With uv

```json
{
  "mcpServers": {
    "binance-futures": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/mcp-binance-futures", "mcp-binance-futures"],
      "env": {
        "BINANCE_API_KEY": "your_api_key",
        "BINANCE_API_SECRET": "your_api_secret"
      }
    }
  }
}
```

---

## Testing

```bash
# install dev dependencies
uv sync --extra dev

# run all tests
pytest

# run with output
pytest -v
```

Tests use `respx` to mock all HTTP calls — no real API keys or network required.

---

## Common Usage Patterns

### Open a long position with stop loss and take profit

```
1. get_ticker(symbol="BTCUSDT")          → check current price
2. get_balance()                          → check available margin
3. get_positions(symbol="BTCUSDT")        → confirm no existing position
4. set_leverage(symbol="BTCUSDT", leverage=10)
5. set_margin_type(symbol="BTCUSDT", margin_type="ISOLATED")
6. place_order(symbol="BTCUSDT", side="BUY", order_type="MARKET", quantity=0.01)
7. place_order(symbol="BTCUSDT", side="SELL", order_type="STOP_MARKET",
               stop_price=45000, close_position=True)
8. place_order(symbol="BTCUSDT", side="SELL", order_type="TAKE_PROFIT_MARKET",
               stop_price=55000, close_position=True)
```

### Modify a limit order

```
1. get_open_orders(symbol="BTCUSDT")      → find the order ID
2. modify_order(symbol="BTCUSDT", order_id=123456, side="BUY",
                quantity=0.01, price=48500)
```

### Emergency close all

```
1. cancel_all_orders(symbol="BTCUSDT")
2. place_order(symbol="BTCUSDT", side="SELL", order_type="MARKET",
               quantity=<position_size>, reduce_only=True)
```

---

## Architecture

```
server.py      — FastMCP server, all tool definitions
client.py      — Async HTTP client: signing, transport, error handling
tests/
  test_client.py  — Unit tests for BinanceClient (signing, HTTP, errors)
  test_server.py  — Integration tests for all MCP tools
```

The client and server are intentionally kept in separate files: `client.py` handles all Binance API mechanics (HMAC signing, error parsing, HTTP verbs) while `server.py` contains only tool logic and MCP wiring. This makes both independently testable.