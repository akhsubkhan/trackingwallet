#!/usr/bin/env python3
"""Pelacak wallet crypto besar (whale) di dunia dan Indonesia.

Tanpa dependensi eksternal, tanpa API key:
  - Saldo BTC   : mempool.space
  - Saldo ETH   : RPC publik Ethereum (publicnode)
  - Harga USD/IDR: CoinGecko

Contoh:
  python3 tracker.py balances                 # semua grup
  python3 tracker.py balances --group indonesia
  python3 tracker.py watch --interval 300 --min-change 10
"""

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_WALLETS = BASE_DIR / "wallets.json"
DEFAULT_STATE = BASE_DIR / "state.json"

BTC_API = "https://mempool.space/api/address/{}"
ETH_RPC = "https://ethereum-rpc.publicnode.com"
PRICE_API = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd,idr"
COINGECKO_IDS = {"btc": "bitcoin", "eth": "ethereum"}
USER_AGENT = "trackingwallet/1.0"


def http_json(url, payload=None, timeout=20):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def btc_balance(address):
    stats = http_json(BTC_API.format(address))
    sats = 0
    for key in ("chain_stats", "mempool_stats"):
        s = stats[key]
        sats += s["funded_txo_sum"] - s["spent_txo_sum"]
    return sats / 1e8


def eth_balance(address):
    payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance", "params": [address, "latest"]}
    result = http_json(ETH_RPC, payload)
    if "error" in result:
        raise RuntimeError(result["error"].get("message", result["error"]))
    return int(result["result"], 16) / 1e18


BALANCE_FETCHERS = {"btc": btc_balance, "eth": eth_balance}


def fetch_prices():
    try:
        return http_json(PRICE_API)
    except Exception as exc:  # harga opsional; saldo tetap ditampilkan
        print(f"[peringatan] gagal mengambil harga: {exc}", file=sys.stderr)
        return {}


def load_wallets(path, group):
    data = json.loads(Path(path).read_text())
    groups = [group] if group != "all" else [g for g in data if not g.startswith("_")]
    wallets = []
    for g in groups:
        if g not in data:
            sys.exit(f"Grup '{g}' tidak ada di {path}")
        for w in data[g]:
            if not w.get("address"):
                continue  # placeholder yang belum diisi
            if w["chain"] not in BALANCE_FETCHERS:
                print(f"[peringatan] chain '{w['chain']}' belum didukung: {w['name']}", file=sys.stderr)
                continue
            wallets.append({**w, "group": g})
    return wallets


def snapshot(wallets):
    rows = []
    for w in wallets:
        try:
            bal = BALANCE_FETCHERS[w["chain"]](w["address"])
            rows.append({**w, "balance": bal})
        except Exception as exc:
            print(f"[error] {w['name']}: {exc}", file=sys.stderr)
    return rows


def fmt_money(value, currency):
    if currency == "idr":
        return "Rp " + f"{value:,.0f}".replace(",", ".")
    return f"${value:,.0f}"


def print_table(rows, prices):
    rows = sorted(rows, key=lambda r: value_of(r, prices, "usd"), reverse=True)
    header = f"{'GRUP':<10} {'NAMA':<34} {'SALDO':>22} {'USD':>18} {'IDR':>26}"
    print(header)
    print("-" * len(header))
    for r in rows:
        bal = f"{r['balance']:,.4f} {r['chain'].upper()}"
        usd = fmt_money(value_of(r, prices, "usd"), "usd") if prices else "-"
        idr = fmt_money(value_of(r, prices, "idr"), "idr") if prices else "-"
        print(f"{r['group']:<10} {r['name'][:34]:<34} {bal:>22} {usd:>18} {idr:>26}")


def value_of(row, prices, currency):
    price = prices.get(COINGECKO_IDS[row["chain"]], {}).get(currency, 0)
    return row["balance"] * price


def cmd_balances(args):
    wallets = load_wallets(args.wallets, args.group)
    if not wallets:
        sys.exit("Tidak ada wallet dengan alamat terisi.")
    rows = snapshot(wallets)
    prices = fetch_prices()
    if args.json:
        print(json.dumps({"prices": prices, "wallets": rows}, indent=2))
    else:
        print_table(rows, prices)


def cmd_watch(args):
    wallets = load_wallets(args.wallets, args.group)
    if not wallets:
        sys.exit("Tidak ada wallet dengan alamat terisi.")
    state_path = Path(args.state)
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    print(f"Memantau {len(wallets)} wallet tiap {args.interval}s "
          f"(alert jika perubahan >= {args.min_change} koin). Ctrl+C untuk berhenti.")
    while True:
        for r in snapshot(wallets):
            key = f"{r['chain']}:{r['address']}"
            prev = state.get(key)
            if prev is not None:
                diff = r["balance"] - prev
                if abs(diff) >= args.min_change:
                    arah = "MASUK" if diff > 0 else "KELUAR"
                    ts = time.strftime("%Y-%m-%d %H:%M:%S")
                    print(f"[{ts}] {arah} {abs(diff):,.4f} {r['chain'].upper()} | {r['name']} "
                          f"| saldo {prev:,.4f} -> {r['balance']:,.4f}", flush=True)
            state[key] = r["balance"]
        state_path.write_text(json.dumps(state, indent=2))
        if args.once:
            break
        time.sleep(args.interval)


def main():
    p = argparse.ArgumentParser(description="Pelacak wallet crypto besar (dunia & Indonesia)")
    p.add_argument("--wallets", default=str(DEFAULT_WALLETS), help="file daftar wallet (JSON)")
    p.add_argument("--group", default="all", help="global | indonesia | all (default)")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("balances", help="tampilkan saldo dan nilai USD/IDR")
    b.add_argument("--json", action="store_true", help="output JSON")
    b.set_defaults(func=cmd_balances)

    w = sub.add_parser("watch", help="pantau perubahan saldo secara berkala")
    w.add_argument("--interval", type=int, default=300, help="detik antar pengecekan (default 300)")
    w.add_argument("--min-change", type=float, default=1.0, help="minimal perubahan koin untuk alert")
    w.add_argument("--state", default=str(DEFAULT_STATE), help="file penyimpanan saldo terakhir")
    w.add_argument("--once", action="store_true", help="cek sekali lalu keluar (untuk cron)")
    w.set_defaults(func=cmd_watch)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
