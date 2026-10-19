"""Shared helpers for chain adapters: HTTP, JSON-RPC, DexScreener pricing."""
import json
import time
import urllib.request
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

USER_AGENT = "wallet-notifier/1.0"


@dataclass
class Holding:
    symbol: str
    amount: float
    price: float  # USD per unit
    liquidity: Optional[float] = None  # USD depth of the pool the price came from
    token_id: str = ""  # mint / contract address; same id across wallets = same asset

    @property
    def value(self) -> float:
        return self.amount * self.price


def http_json(url: str, body: Optional[dict] = None, retries: int = 3, timeout: int = 30):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except Exception as e:  # network blips, 429s, 5xx
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"request failed: {url} ({last_err})")


def rpc(url: str, method: str, params) -> dict:
    resp = http_json(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if "error" in resp:
        raise RuntimeError(f"{method} error: {resp['error']}")
    return resp["result"]


def chunks(items: List[str], n: int) -> Iterable[List[str]]:
    for i in range(0, len(items), n):
        yield items[i:i + n]


def dexscreener_prices(chain_id: str, addresses: List[str], min_liquidity: float) -> Dict[str, dict]:
    """Return {address: {"price": float, "symbol": str}} using the deepest pool per token.

    Tokens whose best pool has less than `min_liquidity` USD are skipped, which
    keeps airdropped spam tokens from inflating the portfolio value.
    """
    best: Dict[str, dict] = {}
    for batch in chunks(addresses, 30):
        url = f"https://api.dexscreener.com/tokens/v1/{chain_id}/{','.join(batch)}"
        try:
            pairs = http_json(url) or []
        except RuntimeError:
            continue
        wanted = set(batch)
        for p in pairs:
            liq = float((p.get("liquidity") or {}).get("usd") or 0)
            price_usd = float(p.get("priceUsd") or 0)
            base, quote = p["baseToken"], p["quoteToken"]
            if base["address"] in wanted:
                addr, sym, price = base["address"], base["symbol"], price_usd
            elif quote["address"] in wanted and float(p.get("priceNative") or 0) > 0:
                addr, sym = quote["address"], quote["symbol"]
                price = price_usd / float(p["priceNative"])
            else:
                continue
            if price <= 0 or liq < min_liquidity:
                continue
            if addr not in best or liq > best[addr]["liquidity"]:
                best[addr] = {"price": price, "symbol": sym, "liquidity": liq}
    return best
