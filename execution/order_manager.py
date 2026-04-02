"""Binance Futures Testnet order management."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Any
from urllib import error, parse, request

from execution import config

logger = logging.getLogger(__name__)

# demo-fapi.binance.com 鍦ㄩ儴鍒嗙綉缁滅幆澧冧笅琚锛屼娇鐢ㄥ鐢ㄥ煙鍚?
_TESTNET_BASE_URL = "https://testnet.binancefuture.com"
_MAINNET_BASE_URL = "https://fapi.binance.com"


class OrderManagerError(RuntimeError):
    """Raised when Binance order requests fail."""


@dataclass(frozen=True)
class SymbolFilters:
    symbol: str
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal


class OrderManager:
    """Minimal Binance Futures REST wrapper for order execution."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = True,
        symbol: str = config.SYMBOL,
        timeout_s: float = config.HTTP_TIMEOUT_S,
    ) -> None:
        if not api_key or not api_secret:
            raise ValueError("Binance testnet API credentials are required")

        self.api_key = api_key
        self.api_secret = api_secret
        self.symbol = symbol
        self.timeout_s = timeout_s
        self.base_url = _TESTNET_BASE_URL if testnet else _MAINNET_BASE_URL
        self._time_offset_ms = 0
        self._last_time_sync_ms = 0
        self.filters = self._load_symbol_filters()
        self._sync_time_offset(force=True)

    def place_limit_entry(
        self,
        direction: str,
        qty: float,
        price: float,
        signal_name: str,
        horizon_min: int,
        time_in_force: str = "GTC",
    ) -> dict[str, Any]:
        side = self._entry_side(direction)
        norm_price = self._normalize_price(price)
        norm_qty = self._normalize_entry_qty(qty, norm_price)
        client_order_id = self._build_client_order_id(signal_name, horizon_min)

        params = {
            "symbol": self.symbol,
            "side": side,
            "type": "LIMIT",
            "timeInForce": str(time_in_force or "GTC").upper(),
            "quantity": self._decimal_to_str(norm_qty),
            "price": self._decimal_to_str(norm_price),
            "newClientOrderId": client_order_id,
            "newOrderRespType": "ACK",
        }

        try:
            data = self._request_json(
                "POST", "/fapi/v1/order", params=params, signed=True
            )
        except OrderManagerError as exc:
            logger.warning(f"[EXEC] Limit entry failed {signal_name}: {exc}")
            return {"status": "rejected", "reason": str(exc)}

        return {
            "status": "placed",
            "order_id": str(data.get("orderId", "")),
            "price": float(norm_price),
            "qty": float(norm_qty),
            "client_order_id": client_order_id,
            "raw": data,
        }

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._request_json(
                "DELETE",
                "/fapi/v1/order",
                params={"symbol": self.symbol, "orderId": order_id},
                signed=True,
            )
            return True
        except OrderManagerError as exc:
            logger.warning(f"[EXEC] Cancel failed order_id={order_id}: {exc}")
            return False

    def close_position(self, direction: str, qty: float) -> dict[str, Any]:
        """Close position: try LIMIT first (maker fee), fallback to MARKET after timeout."""
        result = self._close_position_limit(
            direction, qty, timeout_s=config.CLOSE_LIMIT_TIMEOUT_S
        )
        if result.get("status") == "closed":
            logger.info(f"[EXEC] Closed via LIMIT (maker fee) direction={direction}")
            return result

        logger.info(
            f"[EXEC] LIMIT close timed out, falling back to MARKET direction={direction}"
        )
        return self._close_position_market(direction, qty)

    def _close_position_limit(
        self, direction: str, qty: float, timeout_s: int = 15
    ) -> dict[str, Any]:
        """Try to close with a LIMIT order at best price for maker fee."""
        side = self._close_side(direction)
        norm_qty = self._normalize_qty(qty)

        # For closing LONG (sell): place at ask - 1 tick (maker)
        # For closing SHORT (buy): place at bid + 1 tick (maker)
        close_direction = "short" if direction.lower() == "long" else "long"
        price = self.get_best_price(close_direction)
        norm_price = self._normalize_price(price)

        params = {
            "symbol": self.symbol,
            "side": side,
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": self._decimal_to_str(norm_qty),
            "price": self._decimal_to_str(norm_price),
            "reduceOnly": "true",
            "newOrderRespType": "ACK",
        }

        try:
            data = self._request_json(
                "POST", "/fapi/v1/order", params=params, signed=True
            )
        except OrderManagerError as exc:
            logger.warning(f"[EXEC] LIMIT close failed: {exc}")
            return {"status": "rejected", "reason": str(exc)}

        order_id = str(data.get("orderId", ""))

        # Poll for fill
        import time as _time

        deadline = _time.time() + timeout_s
        while _time.time() < deadline:
            _time.sleep(2)
            status = self.get_order_status(order_id)
            if status["status"] == "FILLED":
                avg_price = status["avg_price"]
                update_time = self._to_int(status.get("update_time")) or int(
                    time.time() * 1000
                )
                return {
                    "status": "closed",
                    "order_id": order_id,
                    "avg_price": avg_price,
                    "qty": self._to_float(status.get("executed_qty"))
                    or float(norm_qty),
                    "update_time": update_time,
                    "fee_type": "maker",
                    "raw": status.get("raw", {}),
                }
            if status["status"] in ("CANCELED", "EXPIRED", "REJECTED"):
                break

        # Cancel unfilled limit order
        self.cancel_order(order_id)
        return {"status": "rejected", "reason": "limit_close_timeout"}

    def _close_position_market(self, direction: str, qty: float) -> dict[str, Any]:
        """Fallback: close with MARKET order (taker fee)."""
        side = self._close_side(direction)
        norm_qty = self._normalize_qty(qty)
        params = {
            "symbol": self.symbol,
            "side": side,
            "type": "MARKET",
            "quantity": self._decimal_to_str(norm_qty),
            "reduceOnly": "true",
            "newOrderRespType": "RESULT",
        }

        try:
            data = self._request_json(
                "POST", "/fapi/v1/order", params=params, signed=True
            )
        except OrderManagerError as exc:
            logger.warning(f"[EXEC] MARKET close failed direction={direction}: {exc}")
            return {"status": "rejected", "reason": str(exc)}

        avg_price = self._to_float(data.get("avgPrice")) or self._to_float(
            data.get("price")
        )
        update_time = self._to_int(data.get("updateTime")) or int(time.time() * 1000)
        return {
            "status": "closed",
            "order_id": str(data.get("orderId", "")),
            "avg_price": avg_price,
            "qty": self._to_float(data.get("executedQty")) or float(norm_qty),
            "update_time": update_time,
            "fee_type": "taker",
            "raw": data,
        }

    def get_open_positions(self) -> list[dict[str, Any]]:
        data = self._request_json("GET", "/fapi/v2/positionRisk", signed=True)
        if not isinstance(data, list):
            return []

        positions: list[dict[str, Any]] = []
        for row in data:
            if row.get("symbol") != self.symbol:
                continue

            qty = self._to_float(row.get("positionAmt"))
            if not qty:
                continue

            positions.append(
                {
                    "symbol": self.symbol,
                    "direction": "long" if qty > 0 else "short",
                    "qty": abs(qty),
                    "entry_price": self._to_float(row.get("entryPrice")),
                    "raw_qty": qty,
                }
            )
        return positions

    def set_leverage(self, leverage: int) -> None:
        """Set account leverage for the trading symbol."""
        self._request_json(
            "POST",
            "/fapi/v1/leverage",
            params={"symbol": self.symbol, "leverage": leverage},
            signed=True,
        )
        logger.info(f"[EXEC] Leverage set to {leverage}x ({self.symbol})")

    def get_usdt_balance(self) -> float:
        """Return wallet balance for the USDT asset."""
        data = self._request_json("GET", "/fapi/v2/account", signed=True)
        for asset in data.get("assets", []):
            if asset.get("asset") == "USDT":
                return float(asset.get("walletBalance", 0))
        return 0.0

    def calc_qty(self, position_pct: float, leverage: int, price: float) -> float:
        """Convert balance, leverage, and price into an order quantity."""
        balance = self.get_usdt_balance()
        margin = balance * position_pct
        notional = margin * leverage
        qty = notional / price
        logger.info(
            f"[EXEC] Position calc: balance={balance:.2f} USDT, "
            f"margin={margin:.2f} USDT, notional={notional:.2f} USDT, qty={qty:.4f}"
        )
        return qty

    def place_limit_entry_with_retry(
        self,
        direction: str,
        qty: float,
        mid_price: float,
        signal_name: str,
        horizon_min: int,
        offset1: float = 0.0002,
        offset2: float = 0.0005,
    ) -> dict[str, Any]:
        """Legacy helper that retries a passive limit order once."""
        import time as _time

        for attempt, offset in enumerate([offset1, offset2], 1):
            if direction.lower() == "long":
                price = mid_price * (1 - offset)
            else:
                price = mid_price * (1 + offset)

            result = self.place_limit_entry(
                direction, qty, price, signal_name, horizon_min
            )
            if result.get("status") != "placed":
                logger.warning(f"[EXEC] Limit order attempt {attempt} failed: {result}")
                continue

            order_id = result["order_id"]
            logger.info(
                f"[EXEC] Limit order placed attempt={attempt}, price={price:.2f}, id={order_id}"
            )

            # 绛夊緟30绉掓鏌ユ槸鍚︽垚浜?
            deadline = _time.time() + 30
            while _time.time() < deadline:
                _time.sleep(2)
                status = self.get_order_status(order_id)
                if status["status"] == "FILLED":
                    result["avg_price"] = status["avg_price"]
                    result["attempt"] = attempt
                    logger.info(
                        f"[EXEC] Limit order filled: price={status['avg_price']:.2f}"
                    )
                    return result
                if status["status"] in ("CANCELED", "EXPIRED", "REJECTED"):
                    logger.warning(
                        f"[EXEC] Limit order unexpected status: {status['status']}"
                    )
                    break

            # 瓒呮椂鎾ゅ崟
            self.cancel_order(order_id)
            logger.info(
                f"[EXEC] Limit order attempt {attempt} timed out, trying next offset"
            )

        return {"status": "rejected", "reason": "all_attempts_failed"}

    def get_book_ticker(self) -> dict[str, float]:
        data = self._request_json(
            "GET",
            "/fapi/v1/ticker/bookTicker",
            params={"symbol": self.symbol},
            signed=False,
        )
        bid = self._to_float(data.get("bidPrice"))
        ask = self._to_float(data.get("askPrice"))
        if bid is None and ask is None:
            raise OrderManagerError("bookTicker missing bid/ask")
        if bid is None:
            bid = ask or 0.0
        if ask is None:
            ask = bid or 0.0
        return {
            "bid": float(bid),
            "ask": float(ask),
            "spread": max(0.0, float(ask) - float(bid)),
            "tick_size": float(self.filters.tick_size),
        }

    def get_best_price(self, direction: str) -> float:
        book = self.get_book_ticker()
        bid = Decimal(str(book["bid"]))
        ask = Decimal(str(book["ask"]))
        tick = self.filters.tick_size
        spread = ask - bid

        if direction.lower() == "long":
            if spread > tick and bid > 0:
                price = bid + tick
            else:
                price = bid if bid > 0 else ask
        elif direction.lower() == "short":
            if spread > tick and ask > 0:
                price = ask - tick
            else:
                price = ask if ask > 0 else bid
        else:
            raise ValueError(f"Unsupported direction: {direction}")

        return float(self._normalize_price(price))

    def get_order_status(self, order_id: str) -> dict[str, Any]:
        data = self._request_json(
            "GET",
            "/fapi/v1/order",
            params={"symbol": self.symbol, "orderId": order_id},
            signed=True,
        )
        return {
            "status": str(data.get("status", "")).upper(),
            "avg_price": self._to_float(data.get("avgPrice"))
            or self._to_float(data.get("price")),
            "executed_qty": self._to_float(data.get("executedQty")),
            "orig_qty": self._to_float(data.get("origQty")),
            "update_time": self._to_int(data.get("updateTime"))
            or int(time.time() * 1000),
            "raw": data,
        }

    def _load_symbol_filters(self) -> SymbolFilters:
        data = self._request_json("GET", "/fapi/v1/exchangeInfo", signed=False)
        for symbol_info in data.get("symbols", []):
            if symbol_info.get("symbol") != self.symbol:
                continue

            filters = {f.get("filterType"): f for f in symbol_info.get("filters", [])}
            price_filter = filters.get("PRICE_FILTER", {})
            lot_filter = filters.get("LOT_SIZE", {})
            notional_filter = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL", {})
            return SymbolFilters(
                symbol=self.symbol,
                tick_size=Decimal(str(price_filter.get("tickSize", "0.1"))),
                step_size=Decimal(str(lot_filter.get("stepSize", "0.001"))),
                min_qty=Decimal(str(lot_filter.get("minQty", "0.001"))),
                min_notional=Decimal(
                    str(
                        notional_filter.get("notional")
                        or notional_filter.get("minNotional")
                        or "100"
                    )
                ),
            )

        raise OrderManagerError(f"Symbol {self.symbol} not found in exchangeInfo")

    def _request_json(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
        retry_on_clock_skew: bool = True,
    ) -> Any:
        req_method = method.upper()
        req_params = dict(params or {})
        if signed:
            req_params["timestamp"] = self._signed_timestamp_ms()
            req_params["recvWindow"] = 10000

        query = parse.urlencode(req_params, doseq=True)
        if signed:
            signature = hmac.new(
                self.api_secret.encode("utf-8"),
                query.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            query = (
                f"{query}&signature={signature}" if query else f"signature={signature}"
            )

        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"

        req = request.Request(url=url, method=req_method)
        req.add_header("Accept", "application/json")
        if signed:
            req.add_header("X-MBX-APIKEY", self.api_key)
        if req_method in {"POST", "PUT", "DELETE"}:
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            req.data = b""

        try:
            with request.urlopen(req, timeout=self.timeout_s) as resp:
                payload = resp.read().decode("utf-8")
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if signed and retry_on_clock_skew and '"code":-1021' in body:
                logger.warning(
                    "[EXEC] Clock skew detected, syncing Binance serverTime and retrying"
                )
                self._sync_time_offset(force=True)
                return self._request_json(
                    method=method,
                    path=path,
                    params=params,
                    signed=signed,
                    retry_on_clock_skew=False,
                )
            raise OrderManagerError(f"HTTP {exc.code} {exc.reason}: {body}") from exc
        except error.URLError as exc:
            raise OrderManagerError(f"Network error: {exc.reason}") from exc

        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise OrderManagerError(f"Invalid JSON response: {payload[:200]}") from exc

    def _sync_time_offset(self, force: bool = False) -> None:
        now_ms = int(time.time() * 1000)
        if (
            not force
            and self._last_time_sync_ms
            and (now_ms - self._last_time_sync_ms) < 60_000
        ):
            return

        try:
            data = self._request_json(
                "GET",
                "/fapi/v1/time",
                signed=False,
                retry_on_clock_skew=False,
            )
        except OrderManagerError as exc:
            logger.warning(f"[EXEC] Binance time sync failed: {exc}")
            return

        server_time = (
            self._to_int(data.get("serverTime")) if isinstance(data, dict) else None
        )
        if server_time is None:
            logger.warning("[EXEC] Binance /time response missing serverTime")
            return

        local_now = int(time.time() * 1000)
        self._time_offset_ms = server_time - local_now
        self._last_time_sync_ms = local_now

    def _signed_timestamp_ms(self) -> int:
        self._sync_time_offset(force=False)
        return int(time.time() * 1000) + self._time_offset_ms

    def _normalize_price(self, price: float | Decimal) -> Decimal:
        return self._quantize_to_step(Decimal(str(price)), self.filters.tick_size)

    def _normalize_qty(self, qty: float | Decimal) -> Decimal:
        quantized = self._quantize_to_step(Decimal(str(qty)), self.filters.step_size)
        if quantized < self.filters.min_qty:
            quantized = self.filters.min_qty
        return quantized

    def _normalize_entry_qty(self, qty: float | Decimal, price: Decimal) -> Decimal:
        quantized = self._normalize_qty(qty)
        if price <= 0 or self.filters.min_notional <= 0:
            return quantized

        if quantized * price >= self.filters.min_notional:
            return quantized

        required_qty = self.filters.min_notional / price
        adjusted = self._quantize_up_to_step(required_qty, self.filters.step_size)
        if adjusted < self.filters.min_qty:
            adjusted = self.filters.min_qty

        if adjusted != quantized:
            logger.info(
                "[EXEC] Qty adjusted %s -> %s to meet min notional %s USDT",
                self._decimal_to_str(quantized),
                self._decimal_to_str(adjusted),
                self._decimal_to_str(self.filters.min_notional),
            )
        return adjusted

    @staticmethod
    def _quantize_to_step(value: Decimal, step: Decimal) -> Decimal:
        if step <= 0:
            return value
        units = (value / step).to_integral_value(rounding=ROUND_DOWN)
        return units * step

    @staticmethod
    def _quantize_up_to_step(value: Decimal, step: Decimal) -> Decimal:
        if step <= 0:
            return value
        units = (value / step).to_integral_value(rounding=ROUND_UP)
        return units * step

    @staticmethod
    def _decimal_to_str(value: Decimal) -> str:
        text = format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"

    @staticmethod
    def _build_client_order_id(signal_name: str, horizon_min: int) -> str:
        # Binance 鍙帴鍙?ASCII 瀛楁瘝鏁板瓧锛屼腑鏂囧瓧绗﹀繀椤绘帓闄?
        clean_name = "".join(ch for ch in signal_name if ch.isascii() and ch.isalnum())[
            :20
        ]
        return f"cx{clean_name}{horizon_min}{int(time.time() * 1000) % 1000000}"

    @staticmethod
    def _entry_side(direction: str) -> str:
        if direction.lower() == "long":
            return "BUY"
        if direction.lower() == "short":
            return "SELL"
        raise ValueError(f"Unsupported entry direction: {direction}")

    @staticmethod
    def _close_side(direction: str) -> str:
        if direction.lower() == "long":
            return "SELL"
        if direction.lower() == "short":
            return "BUY"
        raise ValueError(f"Unsupported close direction: {direction}")

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None






