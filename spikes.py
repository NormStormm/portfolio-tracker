"""Spike watch: runs every 30 min and alerts when a held token's price jumps.

A token alerts when, compared with its lowest price in the last `spike_window_min`
minutes, it is up at least `spike_pct`% AND that move added at least `spike_min_usd`
to your holdings of it (summed across wallets). It's price-based, so buying more of
a token or moving it between wallets never looks like a spike.

After an alert, the same token stays quiet until it climbs another `spike_pct`% above
the alerted price (or 24h pass).
"""
import html
import math
import sys
from datetime import datetime, timedelta, timezone

from chains import CHAINS, fetch_wallet

GRACE_MIN = 15  # GitHub's scheduled runs drift; look back a little past the window
ALERT_COOLDOWN = timedelta(hours=24)


def fmt_price(p: float) -> str:
    if p >= 1:
        return f"${p:,.2f}"
    decimals = min(12, -math.floor(math.log10(p)) + 3) if p > 0 else 2
    return f"${p:.{decimals}f}"


def fmt_usd(v: float) -> str:
    return f"${v:,.0f}"


def check_spikes(cfg: dict, state: dict, now: datetime = None):
    s = cfg["settings"]
    now = now or datetime.now(timezone.utc)
    history = state.get("prices", {})
    alerted = {k: v for k, v in state.get("alerted", {}).items()
               if now - datetime.fromisoformat(v["ts"]) < ALERT_COOLDOWN}

    # Gather every token held across wallets: key -> symbol, price, [(wallet, amount)]
    tokens = {}
    fetched_chains = set()
    for w in cfg["wallets"]:
        if w["chain"] not in CHAINS:
            continue
        try:
            holdings = fetch_wallet(w, s)
        except Exception as e:
            print(f"[{w['chain']}:{w['address']}] fetch failed: {e}", file=sys.stderr)
            continue
        fetched_chains.add(w["chain"])
        for h in holdings:
            if not h.token_id or h.price <= 0 or h.amount <= 0:
                continue
            key = f"{w['chain']}:{h.token_id}"
            t = tokens.setdefault(key, {"symbol": h.symbol, "price": h.price, "positions": {}})
            label = w.get("label") or w["address"][:6] + "…"
            t["positions"][label] = t["positions"].get(label, 0) + h.amount

    window_start = now - timedelta(minutes=s["spike_window_min"] + GRACE_MIN)
    new_history = {}
    alerts = []
    for key, t in tokens.items():
        past = [(ts, p) for ts, p in history.get(key, [])
                if datetime.fromisoformat(ts) >= window_start]
        new_history[key] = past + [(now.isoformat(), t["price"])]
        if not past:
            continue  # first time we've seen it; nothing to compare yet

        base_ts, base = min(past, key=lambda x: x[1])
        ref = max(base, alerted.get(key, {}).get("price", 0))
        price = t["price"]
        pct = (price / base - 1) * 100
        gain = sum(t["positions"].values()) * (price - base)
        if price >= ref * (1 + s["spike_pct"] / 100) and gain >= s["spike_min_usd"]:
            minutes = (now - datetime.fromisoformat(base_ts)).total_seconds() / 60
            alerts.append((gain, key, t, base, pct, minutes))
            alerted[key] = {"price": price, "ts": now.isoformat()}

    # If a whole chain failed this run, keep its history so the next run can still compare.
    for key, pts in history.items():
        if key not in new_history and key.split(":", 1)[0] not in fetched_chains:
            new_history[key] = [(ts, p) for ts, p in pts if datetime.fromisoformat(ts) >= window_start]

    new_state = {"prices": new_history, "alerted": alerted}
    if not alerts:
        return None, new_state

    lines = []
    for gain, key, t, base, pct, minutes in sorted(alerts, key=lambda a: a[0], reverse=True):
        sym = html.escape(t["symbol"])
        chain = CHAINS[key.split(":", 1)[0]]["name"]
        lines.append(f"🚨 <b>Spike: {sym} +{pct:.0f}%</b> in the last {minutes:.0f} min · {chain}")
        lines.append(f"Price {fmt_price(base)} → {fmt_price(t['price'])}")
        for label, amt in sorted(t["positions"].items(), key=lambda x: x[1], reverse=True):
            before, after = amt * base, amt * t["price"]
            if after >= 1:
                lines.append(f"   {html.escape(label)}: {fmt_usd(before)} → {fmt_usd(after)} "
                             f"(+{fmt_usd(after - before)})")
        if len(t["positions"]) > 1:
            lines.append(f"   <b>Total: +{fmt_usd(gain)}</b>")
        lines.append("")
    return "\n".join(lines).rstrip(), new_state
