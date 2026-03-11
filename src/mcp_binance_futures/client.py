"""Binance USDT-M Futures REST API client.

Handles authentication (HMAC-SHA256), request signing, and HTTP transport.
All signed endpoints require BINANCE_API_KEY and BINANCE_API_SECRET env vars.
"""

import hashlib
import hmac
import os
import time
from typing import Any, cast
from urllib.parse import urlencode

import httpx

BASE_URL = "https://fapi.binance.com"
RECV_WINDOW = 5000  # ms — keep tight per Binance recommendation


class BinanceError(Exception):
    """Raised when Binance returns a non-2xx response or an error payload."""

    def __init__(self, code: int, msg: str) -> None:
        self.code = code
        self.msg = msg
        super().__init__(f"Binance error {code}: {msg}")


class BinanceClient:
    """Async HTTP client for Binance USDT-M Futures API.

    Lifecycle: create via BinanceClient.create(), close with await client.close().
    Use as async context manager for automatic cleanup.
    """

    def __init__(self, api_key: str, api_secret: str, base_url: str = BASE_URL) -> None:
        self._api_key = api_key
        self._api_secret = api_secret.encode()  # pre-encode for hmac
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"X-MBX-APIKEY": api_key},
            timeout=10.0,
        )

    @classmethod
    def from_env(cls) -> "BinanceClient":
        """Create client from BINANCE_API_KEY / BINANCE_API_SECRET env vars.
        Raises RuntimeError at startup if either is missing.
        """
        api_key = os.environ.get("BINANCE_API_KEY", "")
        api_secret = os.environ.get("BINANCE_API_SECRET", "")
        if not api_key or not api_secret:
            raise RuntimeError(
                "BINANCE_API_KEY and BINANCE_API_SECRET environment variables must be set"
            )
        return cls(api_key, api_secret)

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "BinanceClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ── Signing ──────────────────────────────────────────────────────────────

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        """Inject timestamp + recvWindow, append HMAC-SHA256 signature."""
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = RECV_WINDOW
        query = urlencode(params)
        sig = hmac.new(self._api_secret, query.encode(), hashlib.sha256).hexdigest()
        params["signature"] = sig
        return params

    # ── Response handling ─────────────────────────────────────────────────────

    @staticmethod
    def _raise_for_error(response: httpx.Response) -> Any:
        """Parse response, raise BinanceError on API-level errors."""
        try:
            data = response.json()
        except Exception:
            if not response.is_success:
                raise BinanceError(response.status_code, response.text) from None
            return response.text

        if isinstance(data, dict) and "code" in data and int(data["code"]) != 200:
            raise BinanceError(int(data["code"]), data.get("msg", "unknown error"))

        if not response.is_success:
            msg = data.get("msg", response.text) if isinstance(data, dict) else response.text
            raise BinanceError(response.status_code, msg)

        return data

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    async def get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | list[Any]:
        """Public GET — no signing."""
        r = await self._http.get(path, params=params or {})
        return cast("dict[str, Any] | list[Any]", self._raise_for_error(r))

    async def get_signed(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | list[Any]:
        """Signed GET (USER_DATA)."""
        r = await self._http.get(path, params=self._sign(params or {}))
        return cast("dict[str, Any] | list[Any]", self._raise_for_error(r))

    async def post_signed(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Signed POST (TRADE / USER_DATA)."""
        signed = self._sign(params or {})
        r = await self._http.post(path, data=signed)
        return cast(dict[str, Any], self._raise_for_error(r))

    async def put_signed(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Signed PUT (order modification)."""
        signed = self._sign(params or {})
        r = await self._http.put(path, data=signed)
        return cast(dict[str, Any], self._raise_for_error(r))

    async def delete_signed(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Signed DELETE (cancel orders)."""
        signed = self._sign(params or {})
        r = await self._http.request("DELETE", path, data=signed)
        return cast(dict[str, Any], self._raise_for_error(r))
