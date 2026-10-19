"""NEAR: liquid NEAR + staked NEAR (staking pools) + NEP-141 tokens, priced via DexScreener."""
import base64
import json
import os
from typing import List

from .common import Holding, dexscreener_prices, http_json, rpc

DEFAULT_RPC = "https://rpc.mainnet.fastnear.com"
FASTNEAR_API = "https://api.fastnear.com/v1"
WNEAR = "wrap.near"


def _view(rpc_url: str, contract: str, method: str, args: dict):
    res = rpc(rpc_url, "query", {
        "request_type": "call_function",
        "finality": "final",
        "account_id": contract,
        "method_name": method,
        "args_base64": base64.b64encode(json.dumps(args).encode()).decode(),
    })
    return json.loads(bytes(res["result"]).decode())


def fetch_holdings(address: str, settings: dict) -> List[Holding]:
    rpc_url = os.environ.get("NEAR_RPC_URL") or DEFAULT_RPC
    min_liq = settings["min_liquidity_usd"]

    account = rpc(rpc_url, "query", {
        "request_type": "view_account", "finality": "final", "account_id": address})
    liquid = int(account["amount"]) / 1e24

    staked = 0.0
    pools = http_json(f"{FASTNEAR_API}/account/{address}/staking").get("pools") or []
    for pool in pools:
        try:
            staked += int(_view(rpc_url, pool["pool_id"], "get_account_total_balance",
                                {"account_id": address})) / 1e24
        except Exception:
            pass  # dead/removed pool

    tokens = http_json(f"{FASTNEAR_API}/account/{address}/ft").get("tokens") or []
    raw = {t["contract_id"]: int(t["balance"]) for t in tokens
           if t.get("balance") and int(t["balance"]) > 0}

    prices = dexscreener_prices("near", list({WNEAR, *raw}), min_liq)
    near_price = prices.get(WNEAR, {}).get("price", 0)

    holdings = [Holding("NEAR", liquid, near_price, token_id=WNEAR)]
    if staked:
        holdings.append(Holding("NEAR (staked)", staked, near_price, token_id=WNEAR))
    for contract, bal in raw.items():
        if contract not in prices:
            continue
        try:
            meta = _view(rpc_url, contract, "ft_metadata", {})
        except Exception:
            continue
        amount = bal / 10 ** int(meta.get("decimals", 0))
        p = prices[contract]
        holdings.append(Holding(meta.get("symbol") or p["symbol"], amount, p["price"], p["liquidity"], contract))
    return holdings
