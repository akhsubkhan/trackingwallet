#!/usr/bin/env python3
"""Pelacak wallet crypto besar (whale) di dunia dan Indonesia.

Tanpa dependensi eksternal, tanpa API key:
  - BTC          : mempool.space
  - ETH / BSC    : RPC publik (publicnode)
  - Solana       : RPC publik Solana
  - Tron         : TronGrid
  - Harga USD/IDR: CoinGecko

Contoh:
  python3 tracker.py balances                 # semua grup
  python3 tracker.py --group indonesia balances
  python3 tracker.py watch --interval 300 --min-usd 1000000
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_WALLETS = BASE_DIR / "wallets.json"
DEFAULT_STATE = BASE_DIR / "state.json"
USER_AGENT = "trackingwallet/1.0"

BTC_API = "https://mempool.space/api/address/{}"
EVM_RPC = {
    "eth": "https://ethereum-rpc.publicnode.com",
    "bsc": "https://bsc-rpc.publicnode.com",
}
SOL_RPC = "https://api.mainnet-beta.solana.com"
TRON_API = "https://api.trongrid.io/v1/accounts/{}"
PRICE_API = "https://api.coingecko.com/api/v3/simple/price?ids={}&vs_currencies=usd,idr"

# Koin native tiap chain: (simbol, id CoinGecko)
NATIVE = {
    "btc": ("BTC", "bitcoin"),
    "eth": ("ETH", "ethereum"),
    "bsc": ("BNB", "binancecoin"),
    "sol": ("SOL", "solana"),
    "tron": ("TRX", "tron"),
}

# Token yang didukung per chain: simbol -> (kontrak/mint, desimal, id CoinGecko)
TOKENS = {
    "eth": {
        "USDT": ("0xdAC17F958D2ee523a2206206994597C13D831ec7", 6, "tether"),
        "USDC": ("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", 6, "usd-coin"),
    },
    "bsc": {
        "USDT": ("0x55d398326f99059fF775485246999027B3197955", 18, "tether"),
    },
    "sol": {
        "USDT": ("Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", 6, "tether"),
        "USDC": ("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 6, "usd-coin"),
    },
    "tron": {
        "USDT": ("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", 6, "tether"),
    },
}


def http_json(url, payload=None, timeout=20):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def rpc(url, method, params):
    result = http_json(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if "error" in result:
        raise RuntimeError(result["error"].get("message", result["error"]))
    return result["result"]


# --- Saldo native ---------------------------------------------------------

def btc_balance(address):
    stats = http_json(BTC_API.format(address))
    sats = 0
    for key in ("chain_stats", "mempool_stats"):
        s = stats[key]
        sats += s["funded_txo_sum"] - s["spent_txo_sum"]
    return sats / 1e8


def evm_balance(chain, address):
    return int(rpc(EVM_RPC[chain], "eth_getBalance", [address, "latest"]), 16) / 1e18


def sol_balance(address):
    return rpc(SOL_RPC, "getBalance", [address])["value"] / 1e9


def tron_account(address):
    data = http_json(TRON_API.format(address))["data"]
    return data[0] if data else {}


def tron_balance(address):
    return tron_account(address).get("balance", 0) / 1e6


def native_balance(chain, address):
    if chain == "btc":
        return btc_balance(address)
    if chain in EVM_RPC:
        return evm_balance(chain, address)
    if chain == "sol":
        return sol_balance(address)
    if chain == "tron":
        return tron_balance(address)
    raise ValueError(f"chain tidak didukung: {chain}")


# --- Saldo token ----------------------------------------------------------

def evm_token_balance(chain, address, contract, decimals):
    data = "0x70a08231" + address.lower().removeprefix("0x").rjust(64, "0")  # balanceOf(address)
    raw = rpc(EVM_RPC[chain], "eth_call", [{"to": contract, "data": data}, "latest"])
    return int(raw, 16) / 10 ** decimals if raw not in ("0x", None) else 0.0


def sol_token_balance(address, mint):
    res = rpc(SOL_RPC, "getTokenAccountsByOwner", [address, {"mint": mint}, {"encoding": "jsonParsed"}])
    total = 0.0
    for acc in res["value"]:
        total += float(acc["account"]["data"]["parsed"]["info"]["tokenAmount"]["uiAmountString"])
    return total


def tron_token_balance(address, contract, decimals):
    for entry in tron_account(address).get("trc20", []):
        if contract in entry:
            return int(entry[contract]) / 10 ** decimals
    return 0.0


def token_balance(chain, address, symbol):
    contract, decimals, _ = TOKENS[chain][symbol]
    if chain in EVM_RPC:
        return evm_token_balance(chain, address, contract, decimals)
    if chain == "sol":
        return sol_token_balance(address, contract)
    if chain == "tron":
        return tron_token_balance(address, contract, decimals)
    raise ValueError(f"token tidak didukung di chain {chain}")


# --- Harga & daftar wallet ------------------------------------------------

def fetch_prices():
    ids = {cg for _, cg in NATIVE.values()} | {t[2] for toks in TOKENS.values() for t in toks.values()}
    try:
        return http_json(PRICE_API.format(urllib.parse.quote(",".join(sorted(ids)))))
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
            if w["chain"] not in NATIVE:
                print(f"[peringatan] chain '{w['chain']}' belum didukung: {w['name']}", file=sys.stderr)
                continue
            for t in w.get("tokens", []):
                if t not in TOKENS.get(w["chain"], {}):
                    print(f"[peringatan] token '{t}' belum didukung di {w['chain']}: {w['name']}", file=sys.stderr)
            wallets.append({**w, "group": g})
    return wallets


def snapshot(wallets):
    """Satu baris per (wallet, aset): koin native + token yang diminta."""
    rows = []
    for w in wallets:
        symbol, cg_id = NATIVE[w["chain"]]
        assets = [(symbol, cg_id, lambda w=w: native_balance(w["chain"], w["address"]))]
        for t in w.get("tokens", []):
            if t in TOKENS.get(w["chain"], {}):
                assets.append((t, TOKENS[w["chain"]][t][2],
                               lambda w=w, t=t: token_balance(w["chain"], w["address"], t)))
        for sym, cg, fetch in assets:
            try:
                rows.append({**w, "asset": sym, "price_id": cg, "balance": fetch()})
            except Exception as exc:
                print(f"[error] {w['name']} ({sym}): {exc}", file=sys.stderr)
    return rows


def value_of(row, prices, currency):
    return row["balance"] * prices.get(row["price_id"], {}).get(currency, 0)


def fmt_money(value, currency):
    if currency == "idr":
        return "Rp " + f"{value:,.0f}".replace(",", ".")
    return f"${value:,.0f}"


def print_table(rows, prices):
    rows = sorted(rows, key=lambda r: value_of(r, prices, "usd"), reverse=True)
    header = f"{'GRUP':<10} {'NAMA':<34} {'SALDO':>24} {'USD':>18} {'IDR':>26}"
    print(header)
    print("-" * len(header))
    for r in rows:
        bal = f"{r['balance']:,.4f} {r['asset']}"
        usd = fmt_money(value_of(r, prices, "usd"), "usd") if prices else "-"
        idr = fmt_money(value_of(r, prices, "idr"), "idr") if prices else "-"
        print(f"{r['group']:<10} {r['name'][:34]:<34} {bal:>24} {usd:>18} {idr:>26}")


# --- Notifikasi Telegram --------------------------------------------------

def send_telegram(text):
    """Kirim pesan jika TELEGRAM_BOT_TOKEN & TELEGRAM_CHAT_ID di-set."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    if chat_id == token.split(":")[0]:
        print("[peringatan] TELEGRAM_CHAT_ID berisi ID bot sendiri; isi dengan chat ID Anda "
              "(lihat README bagian Notifikasi Telegram)", file=sys.stderr)
        return
    try:
        http_json(f"https://api.telegram.org/bot{token}/sendMessage", {"chat_id": chat_id, "text": text})
    except urllib.error.HTTPError as exc:
        try:
            reason = json.load(exc).get("description", exc)
        except Exception:
            reason = exc
        print(f"[peringatan] gagal kirim Telegram: {reason}", file=sys.stderr)
    except Exception as exc:
        print(f"[peringatan] gagal kirim Telegram: {exc}", file=sys.stderr)


# --- Perintah -------------------------------------------------------------

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
    telegram = bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))
    print(f"Memantau {len(wallets)} wallet tiap {args.interval}s "
          f"(alert jika perubahan >= ${args.min_usd:,.0f}; Telegram {'aktif' if telegram else 'nonaktif'}). "
          "Ctrl+C untuk berhenti.")
    while True:
        rows = snapshot(wallets)
        prices = fetch_prices()
        for r in rows:
            key = f"{r['chain']}:{r['address']}:{r['asset']}"
            prev = state.get(key)
            if prev is not None:
                diff = r["balance"] - prev
                diff_usd = abs(value_of({**r, "balance": diff}, prices, "usd"))
                # Tanpa data harga, alert untuk setiap perubahan
                if diff != 0 and (not prices or diff_usd >= args.min_usd):
                    arah = "MASUK" if diff > 0 else "KELUAR"
                    ts = time.strftime("%Y-%m-%d %H:%M:%S")
                    nilai = f" (~{fmt_money(diff_usd, 'usd')})" if prices else ""
                    msg = (f"{arah} {abs(diff):,.4f} {r['asset']}{nilai} | {r['name']} "
                           f"| saldo {prev:,.4f} -> {r['balance']:,.4f}")
                    print(f"[{ts}] {msg}", flush=True)
                    send_telegram(f"🐋 [{r['group']}] {msg}")
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
    w.add_argument("--min-usd", type=float, default=1_000_000, help="minimal perubahan (USD) untuk alert")
    w.add_argument("--state", default=str(DEFAULT_STATE), help="file penyimpanan saldo terakhir")
    w.add_argument("--once", action="store_true", help="cek sekali lalu keluar (untuk cron)")
    w.set_defaults(func=cmd_watch)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
