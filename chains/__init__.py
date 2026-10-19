"""Chain registry. To add a chain: write chains/<name>.py exposing
fetch_holdings(address, settings) -> List[Holding], then register it here."""
from typing import List

from . import near, solana
from .common import Holding

CHAINS = {
    "solana": {"name": "Solana", "fetch": solana.fetch_holdings},
    "near": {"name": "NEAR", "fetch": near.fetch_holdings},
}


def fetch_wallet(wallet: dict, settings: dict) -> List[Holding]:
    holdings = CHAINS[wallet["chain"]]["fetch"](wallet["address"], settings)
    # A bag worth more than its entire pool can't be sold at that price (usually an
    # airdrop or a dead token), so leave it out rather than inflate the total.
    return [h for h in holdings if h.liquidity is None or h.value <= h.liquidity]
