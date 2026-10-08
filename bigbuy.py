#!/usr/bin/env python3
"""Deteksi wallet yang membeli token apa pun dalam jumlah besar di DEX (BSC / ETH).

Cara kerja (tanpa API key, RPC publik):
  1. Ambil semua Transfer token quote (WBNB/WETH/USDT/USDC/...) di rentang blok baru.
  2. Transfer quote bernilai besar yang MASUK ke pool DEX (Uniswap/PancakeSwap V2/V3)
     = seseorang membayar quote untuk membeli token pasangan di pool itu.
  3. Receipt transaksi dicek: harus ada event Swap dari pool tersebut, dan pengirim
     transaksi harus menerima token itu (menyaring add liquidity, arbitrase, bot MEV).
  4. four.meme (BSC): event TokenPurchase dari bonding curve.
  5. Rekam jejak: setiap pembelian dicatat; harga token dipantau dari pool on-chain
     selama 7 hari, lalu alert menampilkan berapa token pilihan wallet itu yang naik.

Contoh:
  python3 bigbuy.py --min-usd 10000                 # BSC + ETH, terus-menerus
  python3 bigbuy.py --chains bsc --min-usd 25000 --once   # sekali jalan (cron)
"""

import argparse
import html
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

from bandar import (CG_PLATFORM, EXPLORER, EXPLORER_TX, FM_BUY, FOURMEME_HELPER, FOURMEME_MANAGER, FOURMEME_V1,
                    QUOTES, RPC, SWAP_PCS_V3, SWAP_V2, SWAP_V3, TRANSFER, batch, decode_str, eth_calls,
                    load_labels, pad, token_usd, topic_addr, word_addr, words)
from keccak import keccak256
from tracker import http_json, send_telegram

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_STATE = BASE_DIR / "bigbuy_state.json"

SWAPS = {SWAP_V2, SWAP_V3, SWAP_PCS_V3}
CONFIRMATIONS = {"bsc": 3, "eth": 1}  # blok paling baru dilewati dulu (reorg)
BLOCK_TIME = {"bsc": 0.45, "eth": 12.0}
DEXSCREENER = {"bsc": "https://dexscreener.com/bsc/", "eth": "https://dexscreener.com/ethereum/"}

# Token "mayor": membelinya tidak dianggap beli token (mis. USDT -> WBNB, USDC -> WETH)
MAJORS = {
    "bsc": set(QUOTES["bsc"]) | {
        "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c",  # BTCB
        "0x2170ed0880ac9a755fd29b2688956bd959f933f8",  # ETH (Binance-Peg)
        "0x1af3f329e8be154074d8769d1ffa4ee058b1dbc3",  # DAI
        "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34",  # USDe
        "0xa2e3356610840701bdf5611a53974510ae27e2e1",  # wBETH
        "0xb0b84d294e0c75a6abe60171b70edeb2efd14a1b",  # slisBNB
    },
    "eth": set(QUOTES["eth"]) | {
        "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599",  # WBTC
        "0xae7ab96520de3a18e5e111b5eaa95a5e3f5ee6b2",  # stETH
        "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0",  # wstETH
        "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf",  # cbBTC
        "0x4c9edd5852cd905f086c759e8383e09bff1e68b3",  # USDe
        "0x9d39a5de30e57443bff2a8307a4256c8797a3497",  # sUSDe
        "0x1abaea1f7c830bd89acc67ec4af516284b1bc33c",  # EURC
        "0x6c3ea9036406852006290770bedfcaba0e23a0e8",  # PYUSD
        "0xdc035d45d973e3ec169d2276ddab16f1e407384f",  # USDS
        "0x8d0d000ee44948fc98c9b98a4fa4921476f08b0d",  # USD1
        "0x8292bb45bf1ee4d140127049757c2e0ff06317ed",  # RLUSD
        "0x40d16fc0246ad3160ccc09b8d0d3a2cd28ae6c2f",  # GHO
        "0xf939e0a03fb07f59a73314e73794be0e57ac1b4e",  # crvUSD
        "0x853d955acef822db058eb8505911ed77f175b99e",  # FRAX
        "0xae78736cd615f374d3085123a210448e74fc6393",  # rETH
        "0xbe9895146f7af43049ca1c1ae358b0541ea49704",  # cbETH
        "0xcd5fe23c85820f7b72d0926fc9b05b43e359b7ee",  # weETH
    },
}
POOL_CACHE_MAX = 100_000

# Rekam jejak pembeli
TRACK_DAYS = 7          # harga token dipantau selama ini setelah dibeli
KEEP_DAYS = 30          # catatan pembelian disimpan selama ini
MIN_AGE = 3600          # pembelian < 1 jam belum dinilai
WIN_GAIN = 0.5          # "naik" = harga puncak >= +50% dari harga beli
MAX_POSITIONS = 60_000


def log(msg):
    print(f"  .. {msg}", file=sys.stderr, flush=True)


def fmt_usd(v):
    if v >= 1e6:
        return f"${v / 1e6:,.2f}M"
    if v >= 1e3:
        return f"${v / 1e3:,.1f}K"
    return f"${v:,.0f}"


def fmt_amt(v):
    return f"{v:,.0f}" if v >= 100 else f"{v:,.4f}"


def head_block(chain):
    return int(http_json(RPC[chain], {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []})["result"], 16)


def iter_logs(chain, address, topics, start, end, span):
    """eth_getLogs per potongan blok, satu potongan di memori sekaligus.
    Potongan mengikuti saran RPC ("retry with the range a-b") saat hasil terlalu banyak."""
    cur, fails, max_span = start, 0, span
    while cur <= end:
        hi = min(end, cur + span - 1)
        payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_getLogs",
                   "params": [{"address": address, "topics": topics, "fromBlock": hex(cur), "toBlock": hex(hi)}]}
        try:
            resp = http_json(RPC[chain], payload, timeout=90)
            if "error" in resp:
                raise RuntimeError(resp["error"].get("message", resp["error"]))
        except Exception as exc:
            m = re.search(r"range (\d+)-(\d+)", str(exc))
            if m:
                span = max(1, int(m.group(2)) - int(m.group(1)) + 1)
            elif span > 1 and "max results" in str(exc):
                span = max(1, span // 2)
            else:
                fails += 1
                if fails > 5:
                    raise
                time.sleep(2 ** fails)
            continue
        fails = 0
        yield from resp["result"]
        cur = hi + 1
        span = min(max_span, span + span // 4 + 1)


# --- Info pool & token (di-cache di state antar run) -------------------------

def pool_tokens(chain, addrs, cache):
    """alamat -> [token0, token1] untuk pool V2/V3, 0 jika bukan pool."""
    new = [a for a in dict.fromkeys(addrs) if a not in cache]
    if new:
        res = eth_calls(chain, [(a, sel) for a in new for sel in ("0x0dfe1681", "0xd21220a7")])
        for i, a in enumerate(new):
            t0, t1 = res[2 * i], res[2 * i + 1]
            ok = t0 and t1 and len(t0) == 66 and len(t1) == 66
            cache[a] = [topic_addr(t0), topic_addr(t1)] if ok else 0
    return {a: cache[a] for a in addrs}


def token_meta(chain, addrs, cache):
    """alamat -> [simbol, desimal]"""
    new = [a for a in dict.fromkeys(addrs) if a not in cache]
    if new:
        res = eth_calls(chain, [(a, sel) for a in new for sel in ("0x95d89b41", "0x313ce567")])
        for i, a in enumerate(new):
            sym, dec = res[2 * i], res[2 * i + 1]
            cache[a] = [decode_str(sym)[:20] or "?", int(dec, 16) if dec and dec != "0x" else 18]
    return cache


# Pool V2 besar stablecoin/native untuk harga WBNB & WETH on-chain (tanpa CoinGecko, yang sering 429)
NATIVE_POOL = {
    "bsc": "0x16b9a82891338f9ba80e2d6970fdda79d1eb0dae",  # PancakeSwap V2 USDT-WBNB
    "eth": "0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc",  # Uniswap V2 USDC-WETH
}


def quote_prices(chain):
    """alamat quote -> harga USD (stablecoin = 1, WBNB/WETH dari pool on-chain)."""
    out = {addr: 1.0 for addr, (_, _, cg) in QUOTES[chain].items() if cg is None}
    try:
        (t0, t1, res) = eth_calls(chain, [(NATIVE_POOL[chain], sel) for sel in ("0x0dfe1681", "0xd21220a7", "0x0902f1ac")])
        r0, r1 = words(res)[:2]
        t0, t1 = topic_addr(t0), topic_addr(t1)
        stable, native, r_s, r_n = (t0, t1, r0, r1) if t0 in out else (t1, t0, r1, r0)
        out[native] = (r_s / 10 ** QUOTES[chain][stable][1]) / (r_n / 10 ** QUOTES[chain][native][1])
    except Exception as exc:
        log(f"gagal ambil harga native {chain}: {exc} (hanya stablecoin yang dicek)")
    return out


# --- Deteksi -----------------------------------------------------------------

def dex_buys(chain, start, end, min_usd, prices, state):
    """Beli token via pool V2/V3 yang dibayar dengan quote bernilai >= min_usd.
    Juga mengembalikan volume per token {token: [quote masuk pool, quote keluar pool]} untuk arus bersih."""
    pools_cache = state["pools"].setdefault(chain, {})
    # Ambang per transfer lebih rendah: satu pembelian bisa dipecah ke beberapa pool
    floor = min_usd / 4
    cands = []  # (tx, block, pool, quote, raw, usd)
    outs = []   # (pool, quote, usd): quote keluar dari pool = token dijual
    n = 0
    for lg in iter_logs(chain, list(prices), [TRANSFER], start, end, span=40 if chain == "bsc" else 20):
        n += 1
        if len(lg["topics"]) != 3 or len(lg["data"]) < 66:
            continue
        q = lg["address"].lower()
        raw = int(lg["data"][:66], 16)
        usd = raw / 10 ** QUOTES[chain][q][1] * prices[q]
        if usd >= floor:
            cands.append((lg["transactionHash"], int(lg["blockNumber"], 16), topic_addr(lg["topics"][2]), q, raw, usd))
            outs.append((topic_addr(lg["topics"][1]), q, usd))
    log(f"{chain}: {n:,} transfer quote, {len(cands):,} >= {fmt_usd(floor)}")
    if not cands:
        return [], {}

    pools = pool_tokens(chain, [c[2] for c in cands] + [o[0] for o in outs], pools_cache)
    volume = defaultdict(lambda: [0.0, 0.0])  # sama-sama transfer >= floor: beli vs jual sebanding
    for side, rows in ((0, [(c[2], c[3], c[5]) for c in cands]), (1, outs)):
        for pool, q, usd in rows:
            p = pools.get(pool)
            if p and q in p:
                token = p[1] if p[0] == q else p[0]
                if token not in MAJORS[chain]:
                    volume[token][side] += usd
    by_tx = defaultdict(list)
    for tx, blk, pool, q, raw, usd in cands:
        p = pools.get(pool)
        if not p or q not in p:
            continue
        token = p[1] if p[0] == q else p[0]
        if token in MAJORS[chain]:
            continue
        by_tx[tx].append((blk, pool, q, raw, usd, token))
    # Hanya tx yang total quote masuk ke pool token non-mayor bisa mencapai ambang
    by_tx = {tx: v for tx, v in by_tx.items() if sum(x[4] for x in v) >= min_usd}
    log(f"{chain}: {len(by_tx):,} transaksi kandidat beli")
    if not by_tx:
        return [], volume

    # eth_getTransactionReceipt ditolak RPC publik BSC ("archive"); eth_getBlockReceipts tidak
    blocks = sorted({v[0][0] for v in by_tx.values()})
    receipts = {}
    for blk, rcs in zip(blocks, batch(chain, [("eth_getBlockReceipts", [hex(b)]) for b in blocks], size=5)):
        receipts.update({rc["transactionHash"]: rc for rc in rcs or []})
    buys = []
    for tx in by_tx:
        rc = receipts.get(tx)
        if not rc or rc.get("status") != "0x1":
            continue
        buyer = rc["from"].lower()
        swapped = {lg["address"].lower() for lg in rc["logs"] if lg["topics"] and lg["topics"][0] in SWAPS}
        per_token = defaultdict(lambda: {"usd": 0.0, "paid": defaultdict(int), "pools": defaultdict(float)})
        for blk, pool, q, raw, usd, token in by_tx[tx]:
            if pool in swapped:
                per_token[token]["usd"] += usd
                per_token[token]["paid"][q] += raw
                per_token[token]["pools"][(pool, q)] += usd
        for token, agg in per_token.items():
            if agg["usd"] < min_usd:
                continue
            # Token harus benar-benar diterima pengirim tx (bukan kontrak bot / LP)
            got = out = 0
            swap_pools = {pool for pool, _ in agg["pools"]}
            for lg in rc["logs"]:
                if (lg["address"].lower() == token and len(lg["topics"]) == 3 and lg["topics"][0] == TRANSFER
                        and len(lg["data"]) >= 66):
                    amt = int(lg["data"][:66], 16)
                    got += amt if topic_addr(lg["topics"][2]) == buyer else 0
                    got -= amt if topic_addr(lg["topics"][1]) == buyer else 0
                    out += amt if topic_addr(lg["topics"][1]) in swap_pools else 0
            # Pembeli harus menerima sebagian besar token dari pool; sisanya = routing / arbitrase
            if got <= 0 or got < out / 2:
                continue
            pool, q = max(agg["pools"], key=agg["pools"].get)  # pool utama, untuk memantau harga
            buys.append({"chain": chain, "tx": tx, "block": int(rc["blockNumber"], 16), "buyer": buyer,
                         "token": token, "raw": got, "usd": agg["usd"], "pool": pool, "quote": q,
                         "paid": {QUOTES[chain][q][0]: r / 10 ** QUOTES[chain][q][1] for q, r in agg["paid"].items()}})
    return buys, volume


def fourmeme_buys(start, end, min_usd, bnb_usd, state):
    """TokenPurchase(token, account, price, amount, cost, fee, offers, funds) di bonding curve four.meme."""
    if not bnb_usd:
        return []
    quotes = state.setdefault("fm_quote", {})
    hits = []
    for lg in iter_logs("bsc", [FOURMEME_MANAGER, FOURMEME_V1], [FM_BUY], start, end, span=2000):
        w = words(lg["data"])
        if len(w) < 8:
            continue
        # Ambang awal dengan asumsi quote BNB (quote termahal); dicek ulang setelah quote diketahui
        if (w[4] + w[5]) / 1e18 * bnb_usd >= min_usd:
            hits.append((lg, w))
    if not hits:
        return []
    tokens = list(dict.fromkeys(word_addr(w[0]) for _, w in hits if word_addr(w[0]) not in quotes))
    res = eth_calls("bsc", [(FOURMEME_HELPER, "0x1f69565f" + pad(t)[2:]) for t in tokens])
    for t, raw in zip(tokens, res):
        q = word_addr(words(raw)[2]) if raw and len(raw) >= 2 + 64 * 3 else word_addr(0)
        quotes[t] = q
    price_cache = {}
    buys = []
    for lg, w in hits:
        token, q = word_addr(w[0]), quotes.get(word_addr(w[0]), word_addr(0))
        if int(q, 16) == 0:
            sym, dec, price = "BNB", 18, bnb_usd
        elif q in QUOTES["bsc"]:
            sym, dec, cg = QUOTES["bsc"][q]
            price = bnb_usd if cg == "binancecoin" else 1.0 if cg is None else None
        else:
            if q not in price_cache:
                price_cache[q] = token_usd("bsc", q)
            sym, dec, price = q[:8], 18, price_cache[q]
        if not price:
            continue
        paid = (w[4] + w[5]) / 10 ** dec
        if paid * price < min_usd:
            continue
        buys.append({"chain": "bsc", "tx": lg["transactionHash"], "block": int(lg["blockNumber"], 16),
                     "buyer": word_addr(w[1]), "token": token, "raw": w[3], "usd": paid * price,
                     "paid": {sym: paid}, "fourmeme": True, "pool": "fourmeme", "quote": q})
    return buys


def drop_mev(chain, buys, end):
    """Buang pembeli yang menjual lagi token itu dalam 2 blok (sandwich / arbitrase)."""
    keep = []
    for b in buys:
        hi = min(end, b["block"] + 2)
        try:
            sold = list(iter_logs(chain, b["token"], [TRANSFER, pad(b["buyer"])], b["block"], hi, span=10))
        except Exception:
            sold = []
        if any(lg["transactionHash"] != b["tx"] for lg in sold):
            continue
        keep.append(b)
    return keep


# --- Rekam jejak pembeli ------------------------------------------------------

def current_prices(chain, keys, state, prices):
    """{(pool, quote, token)} -> harga USD token sekarang, dari pool on-chain."""
    tokens, pools = state["tokens"].get(chain, {}), state["pools"].get(chain, {})
    wbnb = next((a for a, v in QUOTES[chain].items() if v[0] == "WBNB"), None)
    keys = list(keys)
    calls = []
    for pool, q, token in keys:
        if pool == "fourmeme":
            calls += [(FOURMEME_HELPER, "0x1f69565f" + pad(token)[2:])] * 2
        else:
            calls += [(pool, "0x0902f1ac"), (pool, "0x3850c7bd")]  # getReserves() (V2), slot0() (V3)
    res = eth_calls(chain, calls)
    out = {}
    for i, (pool, q, token) in enumerate(keys):
        v2, v3 = res[2 * i], res[2 * i + 1]
        dt = tokens.get(token, ["?", 18])[1]
        try:
            if pool == "fourmeme":
                w = words(v2) if v2 else []
                q_usd = prices.get(wbnb) if int(q, 16) == 0 else prices.get(q)
                if len(w) < 12 or w[11] or not q_usd:
                    continue  # sudah pindah ke PancakeSwap: harga curve tidak berlaku lagi
                out[(pool, q, token)] = w[3] / 1e18 * q_usd
                continue
            q_usd, dq = prices.get(q), QUOTES[chain][q][1]
            t0 = (pools.get(pool) or [None])[0]
            if not q_usd or t0 is None:
                continue
            if v2 and len(v2) >= 2 + 64 * 2:
                r0, r1 = words(v2)[:2]
                r_tok, r_q = (r0, r1) if t0 == token else (r1, r0)
                if not r_tok or not r_q:
                    continue
                price_q = (r_q / 10 ** dq) / (r_tok / 10 ** dt)
            elif v3 and len(v3) >= 2 + 64:
                ratio = words(v3)[0] ** 2 / 2 ** 192  # harga token0 dalam token1 (unit mentah)
                if not ratio:
                    continue
                price_q = ratio * 10 ** dt / 10 ** dq if t0 == token else 10 ** dt / 10 ** dq / ratio
            else:
                continue
            out[(pool, q, token)] = price_q * q_usd
        except (ValueError, ZeroDivisionError, OverflowError):
            continue
    return out


def update_positions(chain, state, prices, now):
    """Perbarui harga sekarang & harga puncak semua pembelian < TRACK_DAYS hari."""
    pos = state["positions"]
    live = {k: p for k, p in pos.items() if k.startswith(chain + ":") and now - p["t"] < TRACK_DAYS * 86400}
    keys = {(p["pool"], p["q"], k.split(":")[2]) for k, p in live.items()}
    # token0/token1 pool bisa hilang jika cache pool di-reset (POOL_CACHE_MAX)
    pool_tokens(chain, [pool for pool, _, _ in keys if pool != "fourmeme"], state["pools"].setdefault(chain, {}))
    cur = current_prices(chain, keys, state, prices) if keys else {}
    for k, p in live.items():
        v = cur.get((p["pool"], p["q"], k.split(":")[2]))
        if v and v < p["p"] * 1000:  # > 1000x hampir pasti harga pool rusak / dimanipulasi
            p["last"], p["peak"], p["u"] = v, max(p["peak"], v), now
    return len(live), len(cur)


def record_buys(chain, buys, meta, state, now):
    pos = state["positions"]
    for b in buys:
        amt = b["raw"] / 10 ** meta.get(b["token"], ["?", 18])[1]
        if amt <= 0:
            continue
        k = f"{chain}:{b['buyer']}:{b['token']}"
        if k in pos:  # beli lagi: harga beli rata-rata
            p = pos[k]
            p["usd"] += b["usd"]
            p["amt"] += amt
            p["p"] = p["usd"] / p["amt"]
        else:
            price = b["usd"] / amt
            pos[k] = {"p": price, "usd": b["usd"], "amt": amt, "t": now, "pool": b["pool"], "q": b["quote"],
                      "peak": price, "last": price}
    for k in [k for k, p in pos.items() if now - p["t"] > KEEP_DAYS * 86400]:
        del pos[k]
    if len(pos) > MAX_POSITIONS:
        for k in sorted(pos, key=lambda k: pos[k]["t"])[:len(pos) - MAX_POSITIONS]:
            del pos[k]


def track_records(chain, state, now):
    """pembeli -> [(token, kenaikan puncak, kenaikan sekarang)] untuk pembelian yang sudah bisa dinilai."""
    recs = defaultdict(list)
    for k, p in state["positions"].items():
        c, buyer, token = k.split(":")
        if c == chain and p.get("u") and now - p["t"] >= MIN_AGE and p["p"] > 0:
            recs[buyer].append((token, p["peak"] / p["p"] - 1, p["last"] / p["p"] - 1))
    return recs


def record_stats(recs, buyer, token):
    rows = [r for r in recs.get(buyer, []) if r[0] != token]
    if not rows:
        return 0, 0, 0.0, 0.0
    n = len(rows)
    return n, sum(r[1] >= WIN_GAIN for r in rows), sum(r[1] for r in rows) / n, sum(r[2] for r in rows) / n


def record_text(stats):
    n, wins, peak, cur = stats
    if not n:
        return "Rekam jejak: belum ada"
    icon = "⭐" if wins / n >= 0.5 else "📉"
    pct = lambda x: f"{round(x * 100):+d}%".replace("+0%", "0%").replace("-0%", "0%")
    return (f"{icon} Rekam jejak: {wins}/{n} token naik ≥{WIN_GAIN:.0%} "
            f"(puncak rata-rata {pct(peak)}, sekarang {pct(cur)})")


# --- Deteksi volume diputar (wash) --------------------------------------------

FUND_LOOKBACK_MIN = 10   # dana masuk ke pembeli sampai 10 menit sebelum cek ini
HUB_SENDERS = 15         # pendana yang menerima dari >= sekian alamat = hub/exchange, bukan satu operator
WASH_HOURS = 24          # token yang ketahuan diputar dicoret dari TOP 5 selama ini


def find_funders(chain, buys, start, end, prices, state, labels):
    """pembeli -> wallet yang paling banyak mengirim quote (USDT/WBNB/...) sebelum ia membeli.
    Pool DEX dan wallet berlabel (exchange, dll.) tidak dihitung sebagai pendana."""
    first = {}
    for b in buys:
        first[b["buyer"]] = min(first.get(b["buyer"], b["block"]), b["block"])
    if not first or not prices:
        return {}
    lo = max(0, start - int(FUND_LOOKBACK_MIN * 60 / BLOCK_TIME[chain]))
    pools = state["pools"].get(chain, {})
    got = defaultdict(lambda: defaultdict(float))
    buyers = list(first)
    for i in range(0, len(buyers), 100):
        topics = [TRANSFER, None, [pad(a) for a in buyers[i:i + 100]]]
        for lg in iter_logs(chain, list(prices), topics, lo, end, span=1000):
            if len(lg["topics"]) != 3 or len(lg["data"]) < 66:
                continue
            to, frm = topic_addr(lg["topics"][2]), topic_addr(lg["topics"][1])
            if int(lg["blockNumber"], 16) > first.get(to, -1) or pools.get(frm) or frm in labels:
                continue
            q = lg["address"].lower()
            got[to][frm] += int(lg["data"][:66], 16) / 10 ** QUOTES[chain][q][1] * prices[q]
    return {b: max(src, key=src.get) for b, src in got.items() if src}


def flag_wash(chain, groups, funders, prices, start, end, state, now):
    """Tandai token yang pembelinya didanai satu wallet (g['funder']) dan, jika pendana itu juga
    menerima quote dari pool token tersebut (= menjual lalu mendanai pembeli baru), g['wash']."""
    top = {}
    for g in groups:
        buyers = [b["buyer"] for b in g["buys"]]
        cnt = Counter(funders[b] for b in buyers if b in funders)
        if cnt:
            f, n = cnt.most_common(1)[0]
            if n >= 3 and n >= 0.3 * len(buyers):
                top[g["token"]] = (f, n, len(buyers))
    if not top:
        return
    lo = max(0, start - int(FUND_LOOKBACK_MIN * 60 / BLOCK_TIME[chain]))
    # Hot wallet exchange juga mendanai banyak pembeli, tapi menerima dari banyak alamat berbeda
    pools_cache = state["pools"].get(chain, {})
    senders = defaultdict(set)
    cand = sorted({f for f, _, _ in top.values()})
    for lg in iter_logs(chain, list(prices), [TRANSFER, None, [pad(f) for f in cand]], lo, end, span=1000):
        if len(lg["topics"]) == 3 and not pools_cache.get(topic_addr(lg["topics"][1])):
            senders[topic_addr(lg["topics"][2])].add(topic_addr(lg["topics"][1]))
    hubs = {f for f in cand if len(senders[f]) >= HUB_SENDERS}
    suspects = {}
    for g in groups:
        if g["token"] in top and top[g["token"]][0] not in hubs:
            f, n, total = top[g["token"]]
            g["funder"] = (f, n, total)
            pools = {b["pool"] for b in g["buys"] if b.get("pool", "fourmeme") != "fourmeme"}
            if pools:
                suspects[g["token"]] = (f, pools)
    if suspects:
        all_pools = sorted({p for _, ps in suspects.values() for p in ps})
        all_funders = sorted({f for f, _ in suspects.values()})
        sold = set()
        topics = [TRANSFER, [pad(p) for p in all_pools], [pad(f) for f in all_funders]]
        for lg in iter_logs(chain, list(prices), topics, lo, end, span=1000):
            if len(lg["topics"]) == 3:
                sold.add((topic_addr(lg["topics"][1]), topic_addr(lg["topics"][2])))
        for g in groups:
            f, pools = suspects.get(g["token"], (None, ()))
            g["wash"] = any((p, f) in sold for p in pools)
    # token -> [waktu, pendana, jumlah pembeli didanai, total pembeli, diputar?]; yang diputar tidak ditimpa
    funded = state.setdefault("funded", {}).setdefault(chain, {})
    for g in groups:
        if g.get("funder") and not (funded.get(g["token"]) or [0] * 5)[4]:
            f, n, total = g["funder"]
            funded[g["token"]] = [now, f, n, total, bool(g.get("wash"))]
    for t in [t for t, v in funded.items() if now - v[0] > WASH_HOURS * 3600]:
        del funded[t]


def funder_lines(chain, g):
    if not g.get("funder"):
        return []
    f, n, total = g["funder"]
    who = link(short(f), EXPLORER[chain] + f)
    lines = [f"⚠️ {n}/{total} pembeli didanai 1 wallet {who}"]
    if g.get("wash"):
        lines.append("🚫 Volume diputar: pendana itu juga menjual ke pool token ini")
    return lines


# --- Alert -------------------------------------------------------------------

def merge(buys):
    """Gabungkan beberapa beli wallet yang sama untuk token yang sama dalam satu run."""
    out = {}
    for b in sorted(buys, key=lambda b: b["block"]):
        k = (b["chain"], b["buyer"], b["token"])
        if k not in out:
            out[k] = {**b, "paid": dict(b["paid"]), "n": 1}
            continue
        m = out[k]
        m["usd"] += b["usd"]
        m["raw"] += b["raw"]
        m["n"] += 1
        m["tx"] = b["tx"]
        for s, v in b["paid"].items():
            m["paid"][s] = m["paid"].get(s, 0) + v
    return sorted(out.values(), key=lambda b: -b["usd"])


FLOW_HOURS = 24        # ringkasan "24 jam" per token
MAX_FLOWS = 100_000
TOP_BUYERS = 5


def link(text, url):
    return f'<a href="{url}">{html.escape(text)}</a>'


def plain(text):
    return html.unescape(re.sub("<[^>]+>", "", text))


def short(addr):
    return addr[:6] + "…" + addr[-4:]


def record_badge(stats):
    n, wins = stats[:2]
    return "" if not n else f" {'⭐' if wins / n >= 0.5 else '📉'}{wins}/{n}"


def group_by_token(buys):
    """Pembelian satu run -> satu grup per token, urut total USD terbesar."""
    groups = defaultdict(list)
    for b in buys:
        groups[b["token"]].append(b)
    out = []
    for token, rows in groups.items():
        rows.sort(key=lambda b: -b["usd"])
        out.append({"token": token, "buys": rows, "usd": sum(b["usd"] for b in rows),
                    "raw": sum(b["raw"] for b in rows), "n_tx": sum(b["n"] for b in rows),
                    "fourmeme": any(b.get("fourmeme") for b in rows)})
    return sorted(out, key=lambda g: -g["usd"])


def record_flows(chain, buys, state, now):
    flows = state.setdefault("flows", {}).setdefault(chain, [])
    flows += [[now, b["token"], b["buyer"], b["usd"], b["pool"], b["quote"]] for b in buys]
    cutoff = now - FLOW_HOURS * 3600
    flows[:] = [f for f in flows if f[0] >= cutoff][-MAX_FLOWS:]


def record_volume(chain, volume, state, now):
    rows = state.setdefault("volume", {}).setdefault(chain, [])
    rows += [[now, token, v[0], v[1]] for token, v in volume.items()]
    cutoff = now - FLOW_HOURS * 3600
    rows[:] = [r for r in rows if r[0] >= cutoff][-MAX_FLOWS:]


def flow_24h(chain, state, token):
    rows = [f for f in state.get("flows", {}).get(chain, []) if f[1] == token]
    return sum(f[3] for f in rows), len({f[2] for f in rows})


def group_text(chain, g, meta, labels, nonces, supply, minutes, day, names=None):
    names = names or {}
    sym, dec = meta.get(g["token"], ["?", 18])
    amt = g["raw"] / 10 ** dec
    n_wallet = len(g["buys"])
    title = "AKUMULASI" if n_wallet > 1 else "BELI BESAR"
    where = " four.meme" if g["fourmeme"] else ""
    share = amt / supply * 100 if supply else None
    pct = ("" if share is None or share > 100 else  # > 100%: totalSupply token tidak wajar
           " (&lt;0.01% supply)" if share < 0.01 else f" ({share:.2f}% supply)")
    lines = [f"🟢 <b>{title} {link(sym, DEXSCREENER[chain] + g['token'])}</b> · {chain.upper()}{where} · "
             f"<b>{fmt_usd(g['usd'])}</b>",
             f"{n_wallet} wallet · {g['n_tx']}x beli · ±{minutes} menit terakhir",
             f"Total: {fmt_amt(amt)} {html.escape(sym)}{pct}"]
    day_usd, day_wallets = day
    if day_usd > g["usd"] * 1.01:
        lines.append(f"24 jam: {fmt_usd(day_usd)} dari {day_wallets} wallet")
    for b in g["buys"][:TOP_BUYERS]:
        nonce = nonces.get(b["buyer"])
        who = labels.get(b["buyer"]) or names.get(b["buyer"])
        extra = ("" if b["n"] == 1 else f" {b['n']}x") + record_badge(b["record"])
        extra += " 🆕" if nonce is not None and nonce <= 5 else ""
        extra += f" ({html.escape(who)})" if who else ""
        lines.append(f"• {link(short(b['buyer']), EXPLORER[chain] + b['buyer'])} "
                     f"{link(fmt_usd(b['usd']), EXPLORER_TX[chain] + b['tx'])}{extra}")
    rest = g["buys"][TOP_BUYERS:]
    if rest:
        lines.append(f"… +{len(rest)} wallet lain ({fmt_usd(sum(b['usd'] for b in rest))})")
    lines += funder_lines(chain, g)
    lines.append(html.escape(g["token"]))
    return "\n".join(lines)


def send_alerts(chain, groups, meta, state, max_alerts, minutes):
    if not groups:
        return
    labels = load_labels()
    shown = groups[:max_alerts]
    names = wallet_names(chain, [b["buyer"] for g in shown for b in g["buys"][:TOP_BUYERS]], state)
    buyers = list(dict.fromkeys(b["buyer"] for g in shown for b in g["buys"][:TOP_BUYERS]))
    nonces = {a: int(n, 16) for a, n in zip(buyers, batch(chain, [("eth_getTransactionCount", [a, "latest"])
                                                                  for a in buyers])) if n}
    supplies = eth_calls(chain, [(g["token"], "0x18160ddd") for g in shown])  # totalSupply()
    msgs = []
    for g, sup in zip(shown, supplies):
        dec = meta.get(g["token"], ["?", 18])[1]
        supply = int(sup, 16) / 10 ** dec if sup and sup != "0x" else 0
        msgs.append(group_text(chain, g, meta, labels, nonces, supply, minutes, flow_24h(chain, state, g["token"]),
                               names))
    if len(groups) > max_alerts:
        msgs.append(f"… +{len(groups) - max_alerts} token lain di {chain.upper()} (naikkan --min-usd)")
    for m in msgs:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {plain(m)}\n", flush=True)
    # Satu pesan Telegram berisi beberapa token (batas Telegram 4096 karakter)
    chunks = [""]
    for m in msgs:
        if chunks[-1] and len(chunks[-1]) + len(m) + 2 > 3800:
            chunks.append("")
        chunks[-1] = f"{chunks[-1]}\n\n{m}" if chunks[-1] else m
    for c in chunks:
        if not send_telegram(c, preview=False, html=True) and os.environ.get("TELEGRAM_BOT_TOKEN"):
            send_telegram(plain(c), preview=False)  # HTML ditolak: kirim ulang sebagai teks biasa


# --- Narasi & pembeli yang dikenal --------------------------------------------

DS_CHAIN = {"bsc": "bsc", "eth": "ethereum"}


def get_json(url):
    """GET JSON sumber publik (DexScreener / CoinGecko); None jika gagal."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (trackingwallet)", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}  # sumber terjangkau, token tidak terdaftar di sana
        log(f"gagal ambil {url.split('?')[0]}: {exc}")
        return None
    except Exception as exc:
        log(f"gagal ambil {url.split('?')[0]}: {exc}")
        return None


def clean_text(text, limit=200):
    text = " ".join(html.unescape(re.sub("<[^>]+>", " ", text or "")).split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def token_story(chain, token):
    """Narasi & link token dari sumber publik: CoinGecko (deskripsi, kategori) dan DexScreener (link).
    API four.meme tidak dipakai: privat (403 dari GitHub Actions)."""
    out = {"desc": "", "cats": [], "links": {}, "mcap": None, "age_days": None, "reached": 0}

    def add_link(name, url):
        if url and isinstance(url, str) and url.startswith("http") and name not in out["links"]:
            out["links"][name] = url

    ds = get_json(f"https://api.dexscreener.com/tokens/v1/{DS_CHAIN[chain]}/{token}")
    out["reached"] += ds is not None
    if isinstance(ds, list) and ds:
        best = max(ds, key=lambda x: ((x.get("liquidity") or {}).get("usd") or 0))
        info = best.get("info") or {}
        for w in info.get("websites") or []:
            add_link("Website" if (w.get("label") or "website").lower() == "website" else w["label"], w.get("url"))
        for so in info.get("socials") or []:
            kind = (so.get("type") or "").lower()
            add_link({"twitter": "X", "telegram": "Telegram", "discord": "Discord"}.get(kind, kind.title()),
                     so.get("url"))
        out["mcap"] = best.get("marketCap") or best.get("fdv")
        if best.get("pairCreatedAt"):
            out["age_days"] = (time.time() - best["pairCreatedAt"] / 1000) / 86400
    cg = get_json(f"https://api.coingecko.com/api/v3/coins/{CG_PLATFORM[chain]}/contract/{token}")
    out["reached"] += cg is not None
    if isinstance(cg, dict) and cg.get("id"):
        out["desc"] = out["desc"] or clean_text((cg.get("description") or {}).get("en"))
        out["cats"] += [c for c in cg.get("categories") or [] if c and c not in out["cats"]][:3]
        links = cg.get("links") or {}
        add_link("Website", next((u for u in links.get("homepage") or [] if u), None))
        if links.get("twitter_screen_name"):
            add_link("X", f"https://x.com/{links['twitter_screen_name']}")
        add_link("Telegram", f"https://t.me/{links['telegram_channel_identifier']}"
                 if links.get("telegram_channel_identifier") else None)
    return out


# Nama wallet on-chain yang dipasang pemiliknya sendiri: ENS (.eth) dan Space ID (.bnb)
ENS_REVERSE_RECORDS = "0x3671ae578e63fdf66ad4f3e12cc0c0d71ac7510c"  # getNames(address[]), sudah cek dua arah
SID_REGISTRY = "0x08ced32a7f3eec915ba84415e9c07a7286977956"         # Space ID registry (BSC)
NAME_TTL = 7 * 86400


def namehash(name):
    node = b"\0" * 32
    for label in reversed(name.split(".")):
        node = keccak256(node + keccak256(label.encode()))
    return node


def abi_strings(raw):
    """Decode string[] hasil eth_call."""
    data = bytes.fromhex(raw[2:])
    u = lambda o: int.from_bytes(data[o:o + 32], "big")
    base = u(0)
    arr = base + 32
    out = []
    for i in range(u(base)):
        off = arr + u(arr + 32 * i)
        out.append(data[off + 32:off + 32 + u(off)].decode(errors="replace"))
    return out


def _ens_names(addrs):
    out = {}
    for i in range(0, len(addrs), 50):
        chunk = addrs[i:i + 50]
        data = ("0xcbf8b66c" + hex(32)[2:].rjust(64, "0") + hex(len(chunk))[2:].rjust(64, "0")
                + "".join(a[2:].rjust(64, "0") for a in chunk))
        (raw,) = eth_calls("eth", [(ENS_REVERSE_RECORDS, data)])
        if raw and len(raw) > 2:
            out.update(zip(chunk, abi_strings(raw)))
    return out


def _sid_names(addrs):
    """Reverse record .bnb, lalu dicek balik: nama harus menunjuk ke alamat yang sama."""
    nodes = [namehash(a[2:] + ".addr.reverse") for a in addrs]
    resolvers = eth_calls("bsc", [(SID_REGISTRY, "0x0178b8bf" + n.hex()) for n in nodes])
    hits = [(a, n, topic_addr(r)) for a, n, r in zip(addrs, nodes, resolvers) if r and len(r) == 66 and int(r, 16)]
    names = eth_calls("bsc", [(rv, "0x691f3431" + n.hex()) for _, n, rv in hits])  # name(bytes32)
    cands = [(a, decode_str(nm)) for (a, _, _), nm in zip(hits, names) if nm and len(nm) > 2]
    cands = [(a, nm) for a, nm in cands if nm.endswith(".bnb")]
    fwd_nodes = [namehash(nm) for _, nm in cands]
    fwd_res = eth_calls("bsc", [(SID_REGISTRY, "0x0178b8bf" + n.hex()) for n in fwd_nodes])
    pairs = [(c, n, topic_addr(r)) for c, n, r in zip(cands, fwd_nodes, fwd_res) if r and len(r) == 66 and int(r, 16)]
    addrs_back = eth_calls("bsc", [(rv, "0x3b3b57de" + n.hex()) for _, n, rv in pairs])  # addr(bytes32)
    return {a: nm for ((a, nm), _, _), back in zip(pairs, addrs_back)
            if back and len(back) == 66 and topic_addr(back) == a}


def wallet_names(chain, addrs, state):
    """alamat -> nama .eth / .bnb terverifikasi (cache 7 hari di state)."""
    cache = state.setdefault("names", {}).setdefault(chain, {})
    now = time.time()
    todo = [a for a in dict.fromkeys(addrs) if a not in cache or now - cache[a][1] > NAME_TTL]
    if todo:
        try:
            found = _ens_names(todo) if chain == "eth" else _sid_names(todo)
        except Exception as exc:
            log(f"{chain}: gagal cek nama wallet: {exc}")
            found = None
        if found is not None:
            for a in todo:
                cache[a] = [found.get(a, ""), now]
    if len(cache) > 50_000:
        for a in sorted(cache, key=lambda a: cache[a][1])[:len(cache) - 50_000]:
            del cache[a]
    return {a: cache[a][0] for a in addrs if cache.get(a, ["", 0])[0]}


def known_buyers(buyers, labels, names=None, smart=None):
    """Pembeli yang dikenal: label (labels.json / wallets.json), nama .eth/.bnb, smart money otomatis."""
    names, smart = names or {}, smart or {}
    out = []
    for b in buyers:
        if b in labels:
            out.append(labels[b])
        elif b in names:
            out.append(names[b])
        elif b in smart:
            out.append(f"smart money {short(b)} ⭐{smart[b]}")
    return list(dict.fromkeys(out))


def story_lines(chain, token, story, buyers, labels, names=None, smart=None):
    lines = []
    narrative = story["desc"] or ""
    cats = ", ".join(story["cats"][:3])
    if narrative or cats:
        lines.append(f"Narasi: {html.escape(narrative)}{' ' if narrative and cats else ''}"
                     f"{f'[{html.escape(cats)}]' if cats else ''}")
    elif story.get("reached"):
        lines.append("Narasi: tidak ada deskripsi publik")
    else:
        lines.append("Narasi: sumber data (DexScreener/CoinGecko) tidak bisa diakses")
    facts = []
    if story["mcap"]:
        facts.append(f"Mcap {fmt_usd(story['mcap'])}")
    if story["age_days"] is not None:
        age = story["age_days"]
        facts.append(f"pool umur {f'{age * 24:.0f} jam' if age < 1 else f'{age:.0f} hari'}")
    links = " · ".join(link(name, url) for name, url in story["links"].items())
    if links or facts:
        lines.append(" · ".join(x for x in (links, " · ".join(facts)) if x))
    elif story.get("reached"):
        lines.append("⚠️ Tanpa website/sosial media")
    known = known_buyers(buyers, labels, names, smart)
    if known:
        lines.append("Dibeli oleh: " + html.escape(", ".join(known[:5]))
                     + (f" +{len(known) - 5} lainnya" if len(known) > 5 else ""))
    return lines


# --- Top 5 (ringkasan per jam) ------------------------------------------------

MIN_LIQUIDITY = 20_000   # USD sisi quote; di bawah ini terlalu tipis untuk dibeli/dijual
MIN_SCORE = 30           # skor di bawah ini tidak masuk TOP 5


def pool_liquidity(chain, items, prices):
    """{(token, pool, quote)} -> USD quote di pool (four.meme: dana di bonding curve)."""
    wbnb = next((a for a, v in QUOTES[chain].items() if v[0] == "WBNB"), None)
    items = list(items)
    calls = [(FOURMEME_HELPER, "0x1f69565f" + pad(t)[2:]) if pool == "fourmeme"
             else (q, "0x70a08231" + pad(pool)[2:]) for t, pool, q in items]  # balanceOf(pool)
    out = {}
    for (t, pool, q), r in zip(items, eth_calls(chain, calls)):
        if not r or r == "0x":
            continue
        if pool == "fourmeme":
            w = words(r)
            q_usd = prices.get(wbnb) if int(q, 16) == 0 else prices.get(q)
            if len(w) >= 12 and not w[11] and q_usd:
                out[t] = w[9] / 1e18 * q_usd
        elif q in QUOTES[chain] and prices.get(q):
            out[t] = int(r[:66], 16) / 10 ** QUOTES[chain][q][1] * prices[q]
    return out


def is_smart(stats, min_tokens=2):
    return stats[0] >= max(1, min_tokens) and stats[1] / stats[0] >= 0.5


def top_tokens(chain, state, prices, now, n=5):
    """Peringkat token dari data 24 jam: luas pembeli, arus bersih, pembeli ⭐, likuiditas, harga."""
    by = defaultdict(lambda: {"usd": 0.0, "buyers": defaultdict(float), "pool": None, "q": None, "ts": 0})
    for f in state.get("flows", {}).get(chain, []):
        d = by[f[1]]
        d["usd"] += f[3]
        d["buyers"][f[2]] += f[3]
        if len(f) >= 6 and f[0] >= d["ts"]:
            d["pool"], d["q"], d["ts"] = f[4], f[5], f[0]
    vol = defaultdict(lambda: [0.0, 0.0])
    for _, token, v_in, v_out in state.get("volume", {}).get(chain, []):
        vol[token][0] += v_in
        vol[token][1] += v_out
    funded = state.get("funded", {}).get(chain, {})
    cands = {t: d for t, d in by.items()
             if len(d["buyers"]) >= 3 and d["pool"] and not (funded.get(t) or [0] * 5)[4]}  # diputar: dicoret
    if not cands:
        return []
    liq = pool_liquidity(chain, [(t, d["pool"], d["q"]) for t, d in cands.items()], prices)
    gains = defaultdict(list)
    for k, p in state["positions"].items():
        c, _, token = k.split(":")
        if c == chain and token in cands and p.get("u"):
            gains[token].append(p["last"] / p["p"] - 1)
    recs = track_records(chain, state, now)
    rows = []
    for t, d in cands.items():
        net, liquidity = vol[t][0] - vol[t][1], liq.get(t)
        g = sorted(gains[t])
        mom = g[len(g) // 2] if g else None
        if (net < 0.1 * vol[t][0] or not liquidity or liquidity < MIN_LIQUIDITY
                or (mom is not None and mom < -0.3)):
            continue  # tidak net beli (>= 10% volume beli), likuiditas tipis, atau harga anjlok (indikasi rug)
        smart = {}
        for b in d["buyers"]:
            st = record_stats(recs, b, t)
            if is_smart(st):
                smart[b] = f"{st[1]}/{st[0]}"
        score = (25 * min(len(d["buyers"]) / 20, 1) + 20 * min(net / 250_000, 1) + 20 * min(len(smart) / 3, 1)
                 + 20 * min(math.log10(liquidity / MIN_LIQUIDITY), 1)
                 + (15 * min(max((mom + 0.2) / 0.7, 0), 1) if mom is not None else 7.5))
        warn = []
        if t in funded:
            f, n, total = funded[t][1:4]
            score -= 10
            warn.append(f"{n}/{total} pembeli didanai 1 wallet {link(short(f), EXPLORER[chain] + f)}")
        top_share = max(d["buyers"].values()) / d["usd"]
        if top_share > 0.5:
            score -= 10
            warn.append(f"1 wallet = {top_share:.0%} pembelian")
        rows.append({"token": t, "score": score, "buy": vol[t][0], "sell": vol[t][1], "net": net,
                     "buyers": sorted(d["buyers"], key=lambda b: -d["buyers"][b]), "smart": smart, "liq": liquidity,
                     "mom": mom, "warn": warn})
    rows.sort(key=lambda r: -r["score"])
    rows = rows[:n * 2]
    # Banyak pembeli wallet baru = sering bundle / bot peluncuran
    buyers = [b for r in rows for b in r["buyers"][:20]]
    nonces = dict(zip(buyers, batch(chain, [("eth_getTransactionCount", [b, "latest"]) for b in buyers])))
    for r in rows:
        sample = [b for b in r["buyers"][:20] if nonces.get(b)]
        fresh = sum(1 for b in sample if int(nonces[b], 16) <= 5) / len(sample) if sample else 0
        if fresh > 0.5:
            r["score"] -= 10
            r["warn"].append(f"{fresh:.0%} pembeli wallet baru")
    return [r for r in sorted(rows, key=lambda r: -r["score"]) if r["score"] >= MIN_SCORE][:n]


def top_text(chain, rows, meta, stories=None, labels=None, names=None):
    stories, labels = stories or {}, labels or {}
    lines = [f"📊 <b>TOP {len(rows)} SINYAL AKUMULASI · {chain.upper()} · 24 jam</b>",
             "<i>Skor dari data on-chain, bukan saran investasi. Cek kontrak &amp; chart sebelum beli.</i>"]
    for i, r in enumerate(rows, 1):
        sym = meta.get(r["token"], ["?", 18])[0]
        mom = "" if r["mom"] is None else f" · harga {round(r['mom'] * 100):+d}% sejak dibeli"
        smart = f" (⭐{len(r['smart'])})" if r["smart"] else ""
        lines += ["",
                  f"{i}. <b>{link(sym, DEXSCREENER[chain] + r['token'])}</b> · skor {max(0, round(r['score']))}/100",
                  f"Volume beli {fmt_usd(r['buy'])} / jual {fmt_usd(r['sell'])} (net +{fmt_usd(r['net'])})",
                  f"{len(r['buyers'])} wallet pembeli{smart}",
                  f"Likuiditas {fmt_usd(r['liq'])}{mom}"]
        if r["token"] in stories:
            lines += story_lines(chain, r["token"], stories[r["token"]], r["buyers"], labels, names, r["smart"])
        lines += [f"⚠️ {w}" for w in r["warn"]]
        lines.append(html.escape(r["token"]))
    return "\n".join(lines)


def send_top(chain, state, prices, now, every_min):
    last = state.setdefault("top_sent", {}).get(chain, 0)
    if every_min <= 0 or now - last < every_min * 60:
        return
    rows = top_tokens(chain, state, prices, now)
    state["top_sent"][chain] = now
    if not rows:
        log(f"{chain}: top 5: belum ada token yang lolos syarat")
        return
    meta = token_meta(chain, [r["token"] for r in rows], state["tokens"].setdefault(chain, {}))
    stories = {r["token"]: token_story(chain, r["token"]) for r in rows}
    names = wallet_names(chain, [b for r in rows for b in r["buyers"][:100]], state)
    msg = top_text(chain, rows, meta, stories, load_labels(), names)
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {plain(msg)}\n", flush=True)
    if not send_telegram(msg, preview=False, html=True) and os.environ.get("TELEGRAM_BOT_TOKEN"):
        send_telegram(plain(msg), preview=False)


def cmd_info(chain, token, state):
    """Kirim narasi, link, aktivitas 24 jam & pembeli yang dikenal untuk satu token."""
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", token or ""):
        sys.exit(f"Alamat token tidak valid: {token!r}")
    token = token.lower()
    meta = token_meta(chain, [token], state["tokens"].setdefault(chain, {}))
    flows = [f for f in state.get("flows", {}).get(chain, []) if f[1] == token]
    buyers = list(dict.fromkeys(f[2] for f in sorted(flows, key=lambda f: -f[3])))
    vol = [0.0, 0.0]
    for _, t, v_in, v_out in state.get("volume", {}).get(chain, []):
        if t == token:
            vol[0] += v_in
            vol[1] += v_out
    lines = [f"🔎 <b>INFO {link(meta[token][0], DEXSCREENER[chain] + token)}</b> · {chain.upper()}"]
    recs = track_records(chain, state, time.time())
    smart = {b: f"{st[1]}/{st[0]}" for b in buyers for st in [record_stats(recs, b, token)] if is_smart(st)}
    names = wallet_names(chain, buyers[:200], state)
    lines += story_lines(chain, token, token_story(chain, token), buyers, load_labels(), names, smart)
    if flows:
        lines.append(f"24 jam (tercatat tool ini): beli {fmt_usd(sum(f[3] for f in flows))} dari "
                     f"{len(buyers)} wallet · volume beli {fmt_usd(vol[0])} / jual {fmt_usd(vol[1])}")
    else:
        lines.append("24 jam: belum ada pembelian ≥ --track-usd yang tercatat")
    lines.append(html.escape(token))
    msg = "\n".join(lines)
    print(plain(msg), flush=True)
    if not send_telegram(msg, preview=False, html=True) and os.environ.get("TELEGRAM_BOT_TOKEN"):
        send_telegram(plain(msg), preview=False)


# --- Main --------------------------------------------------------------------

def run_chain(chain, args, state):
    head = head_block(chain) - CONFIRMATIONS[chain]
    last = state["last"].get(chain)
    oldest = head - int(args.max_minutes * 60 / BLOCK_TIME[chain])
    if last is None:
        start = head - int(args.lookback * 60 / BLOCK_TIME[chain]) + 1
    else:
        start = last + 1
        if start < oldest:
            log(f"{chain}: {oldest - start:,} blok dilewati (lebih lama dari {args.max_minutes} menit)")
            start = oldest
    if start > head:
        return
    log(f"{chain}: blok {start:,}-{head:,} ({head - start + 1:,} blok)")
    prices = quote_prices(chain)
    # Pembelian di atas --track-usd dicatat untuk rekam jejak; alert hanya >= --min-usd
    track_usd = min(args.track_usd, args.min_usd)
    buys, volume = dex_buys(chain, start, head, track_usd, prices, state)
    if chain == "bsc":
        wbnb = next(a for a, v in QUOTES["bsc"].items() if v[0] == "WBNB")
        buys += fourmeme_buys(start, head, track_usd, prices.get(wbnb), state)
    buys = merge(drop_mev(chain, buys, head))
    now = time.time()
    meta = token_meta(chain, [b["token"] for b in buys], state["tokens"].setdefault(chain, {}))
    n_live, n_priced = update_positions(chain, state, prices, now)
    recs = track_records(chain, state, now)
    for b in buys:
        b["record"] = record_stats(recs, b["buyer"], b["token"])
    record_buys(chain, buys, meta, state, now)
    record_flows(chain, buys, state, now)
    record_volume(chain, volume, state, now)
    groups = group_by_token(buys)
    try:
        funders = find_funders(chain, buys, start, head, prices, state, load_labels())
        flag_wash(chain, groups, funders, prices, start, head, state, now)
    except Exception as exc:
        log(f"{chain}: cek pendana gagal: {exc}")
    # Satu alert per token: total semua pembelian token itu di run ini >= --min-usd
    alerts = [g for g in groups
              if g["usd"] >= args.min_usd and (not args.smart_only or any(is_smart(b["record"], args.smart_min) for b in g["buys"]))]
    minutes = max(1, round((head - start + 1) * BLOCK_TIME[chain] / 60))
    send_alerts(chain, alerts, meta, state, args.max_alerts, minutes)
    send_top(chain, state, prices, now, args.top_every)
    state["last"][chain] = head
    log(f"{chain}: {len(alerts)} token di-alert, {len(buys)} beli tercatat; "
        f"{n_live:,} pembelian dipantau ({n_priced:,} pool berhasil dicek harganya)")


def load_state(path):
    try:
        state = json.loads(path.read_text())
    except Exception:
        state = {}
    for k in ("last", "pools", "tokens", "positions"):
        state.setdefault(k, {})
    for chain, cache in state["pools"].items():
        if len(cache) > POOL_CACHE_MAX:
            state["pools"][chain] = {}
    return state


def main():
    p = argparse.ArgumentParser(description="Alert wallet yang membeli token apa pun dalam jumlah besar (BSC/ETH)")
    p.add_argument("--chains", default="bsc,eth", help="chain dipisah koma: bsc,eth (default keduanya)")
    p.add_argument("--min-usd", type=float, default=10_000,
                   help="alert jika total beli satu token dalam satu cek >= ini (USD, default 10000)")
    p.add_argument("--interval", type=int, default=60, help="detik antar cek (default 60)")
    p.add_argument("--once", action="store_true", help="cek sekali lalu keluar (untuk cron / GitHub Actions)")
    p.add_argument("--lookback", type=float, default=15, help="menit ke belakang saat pertama jalan (default 15)")
    p.add_argument("--max-minutes", type=float, default=60,
                   help="rentang maksimal per cek; RPC publik BSC hanya simpan log ~75 menit (default 60)")
    p.add_argument("--max-alerts", type=int, default=25, help="token maksimal di-alert per chain per cek (default 25)")
    p.add_argument("--track-usd", type=float, default=2_000,
                   help="pembelian per wallet >= ini ikut dihitung di total token & rekam jejak (default 2000)")
    p.add_argument("--smart-only", action="store_true",
                   help="hanya alert pembeli yang rekam jejaknya bagus (>= setengah token naik >= 50%%)")
    p.add_argument("--smart-min", type=int, default=2,
                   help="jumlah token minimal di rekam jejak untuk --smart-only (default 2)")
    p.add_argument("--top-every", type=float, default=60,
                   help="kirim TOP 5 token tiap N menit (default 60; 0 = mati)")
    p.add_argument("--state", default=str(DEFAULT_STATE), help="file state (blok terakhir & cache pool)")
    p.add_argument("--info", metavar="TOKEN", help="kirim narasi & info satu token (chain pertama di --chains) lalu keluar")
    args = p.parse_args()
    chains = [c.strip() for c in args.chains.split(",") if c.strip()]
    for c in chains:
        if c not in QUOTES:
            sys.exit(f"chain tidak didukung: {c} (pilihan: {', '.join(QUOTES)})")
    state_path = Path(args.state)
    state = load_state(state_path)
    if args.info:
        cmd_info(chains[0], args.info, state)
        return
    print(f"Memantau beli >= {fmt_usd(args.min_usd)} di {', '.join(c.upper() for c in chains)}"
          f"{'' if args.once else f' tiap {args.interval}s'}. Ctrl+C untuk berhenti.", flush=True)
    while True:
        for chain in chains:
            try:
                run_chain(chain, args, state)
            except Exception as exc:
                print(f"[error] {chain}: {exc}", file=sys.stderr, flush=True)
            state_path.write_text(json.dumps(state))
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
