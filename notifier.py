#!/usr/bin/env python3
"""Twice-daily crypto wallet valuation report, delivered to Telegram.

Usage:
  python notifier.py            # fetch, send to Telegram, save state
  python notifier.py --dry-run  # fetch and print the message; don't send or save
  python notifier.py --watch    # spike check (runs every 30 min); only messages if something jumped
  python notifier.py --chat-id  # print the chat ID of whoever last messaged your bot
"""
import argparse
import html
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from chains import CHAINS, fetch_wallet
from spikes import check_spikes

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "wallets.json"
STATE_PATH = Path(os.environ.get("STATE_PATH", ROOT / "state.json"))
WATCH_STATE_PATH = Path(os.environ.get("WATCH_STATE_PATH", ROOT / "watch_state.json"))


def load_config() -> dict:
    # WALLETS_JSON (e.g. a GitHub secret) overrides the file so addresses can stay out of git.
    raw = os.environ.get("WALLETS_JSON") or CONFIG_PATH.read_text()
    cfg = json.loads(raw)
    cfg.setdefault("settings", {})
    s = cfg["settings"]
    s.setdefault("timezone", "Pacific/Honolulu")
    s.setdefault("min_liquidity_usd", 10000)
    s.setdefault("top_holdings", 3)
    s.setdefault("min_holding_usd", 1)
    s.setdefault("spike_pct", 25)          # alert when a token is up this much...
    s.setdefault("spike_min_usd", 250)     # ...and the move added at least this many dollars...
    s.setdefault("spike_window_min", 60)   # ...within this many minutes
    return cfg


def load_state(path: Path = STATE_PATH) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def fmt_usd(v: float) -> str:
    return f"${v:,.2f}" if abs(v) < 1e6 else f"${v / 1e6:,.2f}M"


def fmt_change(now: float, prev) -> str:
    if prev is None:
        return "<i>(first report)</i>"
    diff = now - prev
    pct = (diff / prev * 100) if prev else 0.0
    if abs(diff) < 0.005:
        return "• no change"
    arrow = "🟢 ▲" if diff > 0 else "🔴 ▼"
    sign = "+" if diff > 0 else "−"
    return f"{arrow} {sign}{fmt_usd(abs(diff))} ({sign}{abs(pct):.1f}%)"


def build_report(cfg: dict, prev_state: dict):
    s = cfg["settings"]
    tz = ZoneInfo(s["timezone"])
    now = datetime.now(timezone.utc)
    prev_wallets = prev_state.get("wallets", {})

    lines = [f"💼 <b>Portfolio update</b> — {now.astimezone(tz):%b %-d, %-I:%M %p %Z}"]
    if prev_state.get("timestamp"):
        hours = (now - datetime.fromisoformat(prev_state["timestamp"])).total_seconds() / 3600
        lines.append(f"<i>Change vs. last update {hours:.0f}h ago</i>")
    lines.append("")

    new_wallets = {}
    total = comparable_now = prev_total = 0.0
    failed = []
    for w in cfg["wallets"]:
        chain = CHAINS.get(w["chain"])
        label = html.escape(w.get("label") or w["address"][:6] + "…")
        key = f"{w['chain']}:{w['address']}"
        if not chain:
            lines += [f"<b>{label}</b> · ⚠️ unknown chain '{html.escape(w['chain'])}'", ""]
            continue
        try:
            holdings = fetch_wallet(w, s)
        except Exception as e:
            print(f"[{key}] fetch failed: {e}", file=sys.stderr)
            failed.append(label)
            lines += [f"<b>{label}</b> · {chain['name']}", "⚠️ couldn't fetch balance this run", ""]
            if key in prev_wallets:  # carry forward so next run still has a baseline
                new_wallets[key] = prev_wallets[key]
            continue

        value = sum(h.value for h in holdings)
        prev = prev_wallets.get(key, {}).get("value")
        new_wallets[key] = {"value": value}
        total += value
        if prev is not None:
            comparable_now += value
            prev_total += prev

        lines.append(f"<b>{label}</b> · {chain['name']}")
        lines.append(f"<code>{fmt_usd(value)}</code>  {fmt_change(value, prev)}")
        top = sorted((h for h in holdings if h.value >= s["min_holding_usd"]),
                     key=lambda h: h.value, reverse=True)[: s["top_holdings"]]
        if top:
            lines.append("   " + " · ".join(f"{html.escape(h.symbol)} {fmt_usd(h.value)}" for h in top))
        lines.append("")

    # Total change only counts wallets that have a baseline, so adding a wallet
    # doesn't show up as a huge gain.
    lines.append("━━━━━━━━━━━━")
    total_change = fmt_change(comparable_now, prev_total) if prev_total else fmt_change(total, None)
    lines.append(f"<b>Total</b>  <code>{fmt_usd(total)}</code>  {total_change}")
    if failed:
        lines.append(f"<i>Total excludes: {', '.join(failed)}</i>")

    new_state = {"timestamp": now.isoformat(), "wallets": new_wallets}
    return "\n".join(lines), new_state


def send_telegram(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    body = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                       "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        if not json.load(resp).get("ok"):
            raise RuntimeError("Telegram rejected the message")


def print_chat_id() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30) as resp:
        updates = json.load(resp).get("result", [])
    if not updates:
        sys.exit("No messages yet. Send your bot any message in Telegram, then run this again.")
    chat = updates[-1]["message"]["chat"]
    print(f"TELEGRAM_CHAT_ID={chat['id']}  ({chat.get('username') or chat.get('title')})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print instead of sending; don't save state")
    ap.add_argument("--chat-id", action="store_true", help="look up your Telegram chat ID")
    ap.add_argument("--watch", action="store_true", help="check for price spikes instead of sending the report")
    args = ap.parse_args()

    if args.chat_id:
        print_chat_id()
        return

    cfg = load_config()
    if args.watch:
        text, new_state = check_spikes(cfg, load_state(WATCH_STATE_PATH))
        if args.dry_run:
            print(text or "no spikes")
            return
        if text:
            send_telegram(text)
        WATCH_STATE_PATH.write_text(json.dumps(new_state))
        print("alert sent" if text else "no spikes")
        return

    text, new_state = build_report(cfg, load_state())

    if args.dry_run:
        print(text)
        return
    send_telegram(text)
    STATE_PATH.write_text(json.dumps(new_state, indent=2))
    print("sent")


if __name__ == "__main__":
    main()
