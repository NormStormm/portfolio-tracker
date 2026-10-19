"""Solana: native SOL + SPL / Token-2022 balances, priced via Jupiter (DexScreener fallback)."""
import os
from typing import Dict, List

from .common import Holding, chunks, dexscreener_prices, http_json, rpc

DEFAULT_RPC = "https://api.mainnet-beta.solana.com"
WSOL = "So11111111111111111111111111111111111111112"
TOKEN_PROGRAMS = [
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # SPL Token
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",  # Token-2022
]


def _jupiter_prices(mints: List[str], min_liquidity: float) -> Dict[str, dict]:
    prices = {}
    for batch in chunks(mints, 50):
        try:
            data = http_json(f"https://lite-api.jup.ag/price/v3?ids={','.join(batch)}")
        except RuntimeError:
            continue
        for mint, info in (data or {}).items():
            if info and info.get("usdPrice") and float(info.get("liquidity") or 0) >= min_liquidity:
                prices[mint] = {"price": float(info["usdPrice"]), "liquidity": float(info["liquidity"])}
    return prices


def fetch_holdings(address: str, settings: dict) -> List[Holding]:
    rpc_url = os.environ.get("SOLANA_RPC_URL") or DEFAULT_RPC
    min_liq = settings["min_liquidity_usd"]

    sol = rpc(rpc_url, "getBalance", [address])["value"] / 1e9

    balances: Dict[str, float] = {}
    for program in TOKEN_PROGRAMS:
        res = rpc(rpc_url, "getTokenAccountsByOwner",
                  [address, {"programId": program}, {"encoding": "jsonParsed"}])
        for acct in res["value"]:
            info = acct["account"]["data"]["parsed"]["info"]
            amt = float(info["tokenAmount"].get("uiAmount") or 0)
            if amt > 0:
                balances[info["mint"]] = balances.get(info["mint"], 0) + amt

    mints = list(balances)
    prices = _jupiter_prices([WSOL] + mints, min_liq)
    missing = [m for m in mints if m not in prices]
    ds = dexscreener_prices("solana", missing, min_liq) if missing else {}
    symbols = {m: v["symbol"] for m, v in ds.items()}
    prices.update(ds)

    if missing_syms := [m for m in mints if m in prices and m not in symbols]:
        symbols.update(_symbols(missing_syms))

    holdings = [Holding("SOL", sol, prices.get(WSOL, {}).get("price", 0), token_id=WSOL)]
    for mint, amt in balances.items():
        if mint in prices:
            p = prices[mint]
            holdings.append(Holding(symbols.get(mint, mint[:4] + "…"), amt, p["price"], p["liquidity"], mint))
    return holdings


def _symbols(mints: List[str]) -> Dict[str, str]:
    out = {}
    for batch in chunks(mints, 100):
        try:
            data = http_json(f"https://lite-api.jup.ag/tokens/v2/search?query={','.join(batch)}")
        except RuntimeError:
            continue
        for t in data or []:
            if t.get("id") and t.get("symbol"):
                out[t["id"]] = t["symbol"]
    return out
