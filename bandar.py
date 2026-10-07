#!/usr/bin/env python3
"""Deteksi akumulasi "bandar" pada token EVM (BSC / ETH).

Pipeline:
  TOKEN -> holder aktif -> filter exchange / LP / kontrak -> wallet yang akumulasi
  -> cluster wallet -> profit -> sumber dana -> transaksi DEX
  -> wallet lain ikut akumulasi? -> BANDAR SCORE -> alert

Tanpa API key: semua data dari event log on-chain (RPC publik). Karena itu
analisis dibatasi pada jendela waktu (--hours): holder yang tidak bergerak di
jendela itu tidak terlihat, dan profit dihitung untuk token ini saja.

Contoh:
  python3 bandar.py scan --chain bsc --token 0x... --hours 24
  python3 bandar.py watch --chain bsc --token 0x... --min-score 60
"""

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from tracker import EVM_RPC, PRICE_API, http_json, rpc, send_telegram

BASE_DIR = Path(__file__).resolve().parent
LABELS_FILE = BASE_DIR / "labels.json"
WALLETS_FILE = BASE_DIR / "wallets.json"
DEFAULT_STATE = BASE_DIR / "bandar_state.json"
CACHE_DIR = BASE_DIR / "cache"

# RPC bisa diganti, mis. BSC_RPC=https://... (RPC publik default hanya menyimpan
# log ~10.000 blok terakhir: BSC ~75 menit, ETH ~33 jam)
RPC = {c: os.environ.get(f"{c.upper()}_RPC", url) for c, url in EVM_RPC.items()}

TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
SWAP_V2 = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
SWAP_V3 = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
SWAP_PCS_V3 = "0x19b47279256b2a23a1665c810c8d55a1758940ee09377d4f8d26497a3577dc83"

# four.meme (BSC): token diperdagangkan di bonding curve TokenManager sebelum listing di PancakeSwap
FOURMEME_HELPER = "0xf251f83e40a78868fcfa3fa4599dad6494e46034"  # TokenManagerHelper3.getTokenInfo
FOURMEME_MANAGER = "0x5c952063c7fc8610ffdb798152d69f0b9550762b"  # TokenManager V2
FOURMEME_V1 = "0xec4549cadce5da21df6e6422d448034b5233bfbc"
FM_BUY = "0x7db52723a3b2cdd6164364b3b766e65e540d7be48ffa89582956d8eaebe62942"  # TokenPurchase(...)
FM_SELL = "0x0a5575b3648bae2210cee56bf33254cc1ddfbc7bf637c0af2ac18b14fb1bae19"  # TokenSale(...)
CG_PLATFORM = {"bsc": "binance-smart-chain", "eth": "ethereum"}

EXPLORER = {"bsc": "https://bscscan.com/address/", "eth": "https://etherscan.io/address/"}
BURN = {"0x0000000000000000000000000000000000000000", "0x000000000000000000000000000000000000dead"}

# Token pasangan (quote) di pool DEX: alamat -> (simbol, desimal, id CoinGecko; None = stablecoin $1)
QUOTES = {
    "bsc": {
        "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c": ("WBNB", 18, "binancecoin"),
        "0x55d398326f99059ff775485246999027b3197955": ("USDT", 18, None),
        "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": ("USDC", 18, None),
        "0xe9e7cea3dedca5984780bafc599bd69add087d56": ("BUSD", 18, None),
        "0xc5f0f7b66764f6ec8c8dff7ba683102295e16409": ("FDUSD", 18, None),
        "0x8d0d000ee44948fc98c9b98a4fa4921476f08b0d": ("USD1", 18, None),
    },
    "eth": {
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": ("WETH", 18, "ethereum"),
        "0xdac17f958d2ee523a2206206994597c13d831ec7": ("USDT", 6, None),
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6, None),
        "0x6b175474e89094c44da98b954eedeac495271d0f": ("DAI", 18, None),
    },
}


def log(msg):
    print(f"  .. {msg}", file=sys.stderr, flush=True)


def short(addr):
    return addr[:6] + "…" + addr[-4:]


def topic_addr(topic):
    return "0x" + topic[-40:]


def pad(addr):
    return "0x" + addr.lower().removeprefix("0x").rjust(64, "0")


def word_addr(x):
    return "0x" + hex(x)[2:].rjust(40, "0")


def words(data):
    data = data.removeprefix("0x")
    return [int(data[i:i + 64], 16) for i in range(0, len(data), 64)]


def signed(x):
    return x - (1 << 256) if x >= 1 << 255 else x


# --- RPC ------------------------------------------------------------------

def batch(chain, calls, size=50):
    """Banyak panggilan sekaligus; hasil None jika panggilan itu gagal."""
    out = []
    for i in range(0, len(calls), size):
        chunk = calls[i:i + size]
        payload = [{"jsonrpc": "2.0", "id": j, "method": m, "params": p} for j, (m, p) in enumerate(chunk)]
        for attempt in range(5):
            try:
                resp = http_json(RPC[chain], payload, timeout=40)
                if isinstance(resp, list):
                    break
            except Exception:
                pass
            if attempt == 4:
                raise RuntimeError("batch RPC gagal terus")
            time.sleep(2 ** attempt)
        by_id = {r.get("id"): r.get("result") for r in resp}
        out += [by_id.get(j) for j in range(len(chunk))]
    return out


def eth_calls(chain, pairs):
    return batch(chain, [("eth_call", [{"to": to, "data": data}, "latest"]) for to, data in pairs])


def get_logs(chain, address, topics, start, end, span=5000, limit=400_000):
    """eth_getLogs per potongan blok; potongan dikecilkan otomatis bila RPC menolak."""
    logs, cur, fails = [], start, 0
    while cur <= end:
        hi = min(end, cur + span - 1)
        try:
            logs += rpc(RPC[chain], "eth_getLogs",
                        [{"address": address, "topics": topics, "fromBlock": hex(cur), "toBlock": hex(hi)}])
            cur, fails = hi + 1, 0
        except Exception as exc:
            m = re.search(r"range (\d+)-(\d+)", str(exc))
            if m:
                span = max(1, int(m.group(2)) - int(m.group(1)) + 1)
            elif span > 1:
                span = max(1, span // 2)
            else:
                fails += 1
                if fails > 5:
                    raise
                time.sleep(2 ** fails)
        if len(logs) > limit:
            sys.exit(f"Terlalu banyak event (>{limit:,}). Perkecil --hours.")
    return logs


def block_range(chain, hours):
    head = int(rpc(RPC[chain], "eth_blockNumber", []), 16)
    b1, b0 = batch(chain, [("eth_getBlockByNumber", [hex(head), False]),
                           ("eth_getBlockByNumber", [hex(head - 1000), False])])
    block_time = (int(b1["timestamp"], 16) - int(b0["timestamp"], 16)) / 1000
    return max(0, head - int(hours * 3600 / block_time)), head, block_time


def logs_available(chain, address, block):
    try:
        rpc(RPC[chain], "eth_getLogs", [{"address": address, "fromBlock": hex(block), "toBlock": hex(block)}])
        return True
    except Exception as exc:
        if "rchive" in str(exc) or "403" in str(exc):
            return False
        raise


def earliest_block(chain, address, start, end):
    """Blok tertua yang log-nya masih dilayani RPC (RPC non-archive membatasi riwayat)."""
    if logs_available(chain, address, start):
        return start
    lo, hi = start, end
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if logs_available(chain, address, mid):
            hi = mid
        else:
            lo = mid
    return hi + 100  # sedikit margin: batas RPC ikut bergeser selama pemindaian


def load_cache(chain, token):
    path = CACHE_DIR / f"{chain}_{token}.json"
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"start": -1, "end": -1, "transfers": [], "swaps": [], "swap_end": -1}


def save_cache(chain, token, cache):
    CACHE_DIR.mkdir(exist_ok=True)
    (CACHE_DIR / f"{chain}_{token}.json").write_text(json.dumps(cache))


def decode_str(raw):
    if not raw or raw == "0x":
        return "?"
    data = bytes.fromhex(raw[2:])
    if len(data) >= 96:  # string ABI: offset, panjang, isi
        n = int.from_bytes(data[32:64], "big")
        return data[64:64 + n].decode(errors="replace")
    return data.rstrip(b"\0").decode(errors="replace")  # bytes32


def token_info(chain, token):
    dec, sym, supply = eth_calls(chain, [(token, "0x313ce567"), (token, "0x95d89b41"), (token, "0x18160ddd")])
    if not dec or dec == "0x":
        sys.exit(f"{token} bukan token ERC-20 di {chain}.")
    decimals = int(dec, 16)
    return {"address": token.lower(), "decimals": decimals, "symbol": decode_str(sym),
            "supply": int(supply, 16) / 10 ** decimals if supply and supply != "0x" else 0}


def usd_price(cg_id):
    if cg_id is None:
        return 1.0
    try:
        return http_json(PRICE_API.format(cg_id))[cg_id]["usd"]
    except Exception as exc:
        log(f"gagal ambil harga {cg_id}: {exc}")
        return None


# --- Label & klasifikasi alamat ---------------------------------------------

def load_labels():
    labels = {}
    if LABELS_FILE.exists():
        labels.update({a.lower(): n for a, n in json.loads(LABELS_FILE.read_text())["labels"].items()})
    if WALLETS_FILE.exists():  # wallet yang dipantau tracker.py (exchange, dll.)
        for group, items in json.loads(WALLETS_FILE.read_text()).items():
            if not group.startswith("_"):
                for w in items:
                    if w.get("address", "").startswith("0x") and w["chain"] in EVM_RPC:
                        labels.setdefault(w["address"].lower(), w["name"])
    return labels


def token_usd(chain, addr):
    """Harga USD token sembarang via CoinGecko (untuk quote four.meme di luar QUOTES)."""
    try:
        url = (f"https://api.coingecko.com/api/v3/simple/token_price/{CG_PLATFORM[chain]}"
               f"?contract_addresses={addr}&vs_currencies=usd")
        return http_json(url)[addr]["usd"]
    except Exception as exc:
        log(f"gagal ambil harga {addr}: {exc}")
        return None


def fourmeme_info(chain, token):
    """Info bonding curve four.meme, atau None jika bukan token four.meme."""
    if chain != "bsc":
        return None
    (raw,) = eth_calls(chain, [(FOURMEME_HELPER, "0x1f69565f" + pad(token)[2:])])
    if not raw or len(raw) < 2 + 64 * 12:
        return None
    w = words(raw)
    if w[1] == 0:
        return None
    manager = word_addr(w[1])
    quote = None if w[2] == 0 else word_addr(w[2])
    if quote is None:
        q_sym, q_dec, q_usd = "BNB", 18, usd_price("binancecoin")
    elif quote in QUOTES[chain]:
        q_sym, q_dec, cg = QUOTES[chain][quote]
        q_usd = usd_price(cg)
    else:
        q = token_info(chain, quote)
        q_sym, q_dec, q_usd = q["symbol"], q["decimals"], token_usd(chain, quote)
    return {"manager": manager, "quote": q_sym, "quote_decimals": q_dec, "quote_usd": q_usd,
            "progress_pct": w[9] / w[10] * 100 if w[10] else None, "listed": bool(w[11])}


def parse_fourmeme(logs, token, fm, swaps):
    """TokenPurchase/TokenSale(token, account, price, amount, cost, fee, offers, funds) -> swaps."""
    for lg in logs:
        w = words(lg["data"])
        if len(w) < 8 or word_addr(w[0]) != token:
            continue
        buy = lg["topics"][0] == FM_BUY
        quote_amt = (w[4] + w[5] if buy else w[4] - w[5]) / 10 ** fm["quote_decimals"]  # termasuk fee
        swaps[lg["transactionHash"]].append({
            "side": "buy" if buy else "sell", "raw": w[3],
            "usd": quote_amt * fm["quote_usd"] if fm["quote_usd"] else None,
            "block": int(lg["blockNumber"], 16), "pool": lg["address"].lower(),
        })


def classify(chain, addrs, labels):
    """alamat -> {'kind': wallet|exchange|pool|contract|burn, ...}"""
    addrs = list(dict.fromkeys(addrs))
    codes = batch(chain, [("eth_getCode", [a, "latest"]) for a in addrs])
    result, contracts = {}, []
    for a, code in zip(addrs, codes):
        if a in BURN:
            result[a] = {"kind": "burn"}
        elif a in (FOURMEME_MANAGER, FOURMEME_V1):
            result[a] = {"kind": "curve", "label": "four.meme bonding curve"}
        elif a in labels:
            result[a] = {"kind": "exchange", "label": labels[a]}
        elif code in (None, "0x") or code.startswith("0xef0100"):  # EOA (termasuk delegasi EIP-7702)
            result[a] = {"kind": "wallet"}
        else:
            result[a] = {"kind": "contract"}
            contracts.append(a)
    # Pool DEX (Uniswap/PancakeSwap V2 & V3) punya token0() dan token1()
    res = eth_calls(chain, [(a, sel) for a in contracts for sel in ("0x0dfe1681", "0xd21220a7")])
    for i, a in enumerate(contracts):
        t0, t1 = res[2 * i], res[2 * i + 1]
        if t0 and t1 and len(t0) == 66 and len(t1) == 66:
            result[a] = {"kind": "pool", "token0": topic_addr(t0), "token1": topic_addr(t1)}
    return result


# --- Analisis ---------------------------------------------------------------

def parse_swaps(chain, swap_logs, pools, token, quote_usd):
    """Swap per transaksi dari sudut pandang trader: beli/jual token, nilai USD."""
    swaps = defaultdict(list)
    for lg in swap_logs:
        p = pools.get(lg["address"].lower())
        if not p:
            continue
        is0 = p["token0"] == token
        quote = p["token1"] if is0 else p["token0"]
        w = words(lg["data"])
        if lg["topics"][0] == SWAP_V2:
            a0, a1 = w[0] - w[2], w[1] - w[3]  # masuk ke pool (in - out)
        else:
            a0, a1 = signed(w[0]), signed(w[1])
        tok_in, q_in = (a0, a1) if is0 else (a1, a0)
        if tok_in == 0:
            continue
        info = QUOTES[chain].get(quote)
        usd = None
        if info and quote_usd.get(quote):
            usd = abs(q_in) / 10 ** info[1] * quote_usd[quote]
        swaps[lg["transactionHash"]].append({
            "side": "buy" if tok_in < 0 else "sell", "raw": abs(tok_in), "usd": usd,
            "block": int(lg["blockNumber"], 16), "pool": lg["address"].lower(),
        })
    return swaps


class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)


def scan(chain, token, hours, top=20, max_funding=40):
    token = token.lower()
    labels = load_labels()
    info = token_info(chain, token)
    dec = info["decimals"]
    burned = sum(int(b, 16) for b in eth_calls(chain, [(token, "0x70a08231" + pad(a)[2:]) for a in BURN])
                 if b and b != "0x") / 10 ** dec
    fm = fourmeme_info(chain, token)
    in_curve = 0.0
    if fm:  # token yang belum terjual di bonding curve belum beredar
        (b,) = eth_calls(chain, [(token, "0x70a08231" + pad(fm["manager"])[2:])])
        in_curve = int(b, 16) / 10 ** dec if b and b != "0x" else 0.0
        if fm["manager"] != FOURMEME_MANAGER:
            log("token four.meme versi lama (V1): transaksi bonding curve tidak dibaca")
    supply = max(info["supply"] - burned - in_curve, 1)  # supply beredar: semua % dihitung dari sini
    info["circulating"] = supply
    info["fourmeme"] = fm
    start, end, block_time = block_range(chain, hours)

    # 1. Transfer token -> arus per alamat (log lama diambil dari cache lokal)
    cache = load_cache(chain, token)
    use_cache = cache["end"] >= start and cache["start"] >= 0
    cached = [t for t in cache["transfers"] if t[1] >= start] if use_cache else []
    fetch_from = max(start, cache["end"] + 1) if use_cache else start
    avail = earliest_block(chain, token, fetch_from, end)
    if avail > fetch_from:
        log(f"RPC hanya menyimpan log sejak blok {avail:,} (~{(end - avail) * block_time / 60:.0f} menit); "
            "data sebelumnya tidak ada. Pakai RPC archive (BSC_RPC/ETH_RPC) atau biarkan 'watch' "
            "berjalan agar cache terisi.")
    raw = get_logs(chain, token, [TRANSFER], avail, end)
    transfers = cached + [(lg["transactionHash"], int(lg["blockNumber"], 16), topic_addr(lg["topics"][1]),
                           topic_addr(lg["topics"][2]), int(lg["data"], 16) / 10 ** dec)
                          for lg in raw if len(lg["topics"]) == 3 and lg["data"] not in ("0x", "")]
    start = max(start, cache["start"]) if use_cache else avail
    covered = (end - start) * block_time / 3600
    log(f"{info['symbol']}: {len(transfers):,} transfer di blok {start:,}–{end:,} (~{covered:.1f} jam)")
    if not transfers:
        sys.exit("Tidak ada transfer di jendela ini. Perbesar --hours.")
    dust = supply * 0.0001  # < 0,01% supply beredar dianggap debu (airdrop spam)
    net, count = Counter(), Counter()
    fanout = defaultdict(set)
    by_tx = defaultdict(list)
    for tx, blk, frm, to, amt in transfers:
        net[to] += amt
        net[frm] -= amt
        count[frm] += 1
        count[to] += 1
        fanout[frm].add(to)
        by_tx[tx].append((frm, to, amt))

    # 2-3. Holder aktif + filter exchange / LP / kontrak
    cand = [a for a, _ in net.most_common(300)] + [a for a, _ in count.most_common(60)]
    kinds = classify(chain, cand, labels)
    pools = {a: k for a, k in kinds.items() if k["kind"] == "pool" and token in (k["token0"], k["token1"])}
    log(f"{len(pools)} pool DEX, {sum(k['kind'] == 'exchange' for k in kinds.values())} exchange, "
        f"{sum(k['kind'] == 'contract' for k in kinds.values())} kontrak lain")

    # 8. Transaksi DEX (event Swap di pool token ini)
    quote_usd = {}
    for p in pools.values():
        q = p["token1"] if p["token0"] == token else p["token0"]
        if q in QUOTES[chain] and q not in quote_usd:
            quote_usd[q] = usd_price(QUOTES[chain][q][2])
    swap_pools = sorted(pools, key=lambda a: -count[a])[:20]
    known = set(cache.get("pools", [])) if use_cache else set()
    swap_logs = [lg for lg in cache["swaps"] if int(lg["blockNumber"], 16) >= start] if use_cache else []
    for group, frm in (([p for p in swap_pools if p in known], max(avail, cache["swap_end"] + 1)),
                       ([p for p in swap_pools if p not in known], avail)):
        if group and frm <= end:
            swap_logs += [{k: lg[k] for k in ("address", "topics", "data", "blockNumber", "transactionHash")}
                          for lg in get_logs(chain, group, [[SWAP_V2, SWAP_V3, SWAP_PCS_V3]], frm, end)]
    # Transaksi bonding curve four.meme (event tidak ter-index: ambil semua lalu saring per token)
    curve_logs = [lg for lg in cache.get("curve", []) if int(lg["blockNumber"], 16) >= start] if use_cache else []
    if fm and fm["manager"] == FOURMEME_MANAGER:
        frm = max(avail, cache.get("curve_end", -1) + 1) if use_cache else avail
        if frm <= end:
            curve_logs += [{k: lg[k] for k in ("address", "topics", "data", "blockNumber", "transactionHash")}
                           for lg in get_logs(chain, FOURMEME_MANAGER, [[FM_BUY, FM_SELL]], frm, end)
                           if lg["data"][26:66] == token[2:]]
    save_cache(chain, token, {"start": start, "end": end, "transfers": transfers, "swaps": swap_logs, "swap_end": end,
                              "pools": sorted(known | set(swap_pools)), "curve": curve_logs, "curve_end": end})
    swaps = parse_swaps(chain, swap_logs, pools, token, quote_usd)
    if fm:
        parse_fourmeme(curve_logs, token, fm, swaps)
    log(f"{sum(map(len, swaps.values())):,} swap")

    priced = [s for ss in swaps.values() for s in ss if s["usd"]]
    price = None
    if priced:
        last = max(priced, key=lambda s: s["block"])
        price = last["usd"] / (last["raw"] / 10 ** dec)

    # Atribusi swap ke wallet: wallet EOA yang saldo tokennya naik/turun di tx tsb
    st = defaultdict(lambda: {"buy_n": 0, "sell_n": 0, "buy_tok": 0.0, "sell_tok": 0.0,
                              "cost": 0.0, "proceeds": 0.0, "buy_blocks": Counter()})
    direct = defaultdict(Counter)  # penerima -> pengirim -> jumlah (transfer non-DEX)
    for tx, moves in by_tx.items():
        delta = Counter()
        for frm, to, amt in moves:
            delta[to] += amt
            delta[frm] -= amt
        wallets = {a: d for a, d in delta.items() if kinds.get(a, {"kind": "wallet"})["kind"] == "wallet" and d}
        if tx in swaps:
            for side, sign in (("buy", 1), ("sell", -1)):
                ss = [s for s in swaps[tx] if s["side"] == side]
                who = {a: d * sign for a, d in wallets.items() if d * sign > 0}
                if not ss or not who:
                    continue
                usd = sum(s["usd"] or 0 for s in ss)
                tot = sum(who.values())
                for a, d in who.items():
                    s = st[a]
                    s[f"{side}_n"] += 1
                    s[f"{side}_tok"] += d
                    s["cost" if side == "buy" else "proceeds"] += usd * d / tot
                    if side == "buy":
                        s["buy_blocks"][ss[0]["block"]] += d
        else:
            for frm, to, amt in moves:
                if kinds.get(frm, {}).get("kind") not in ("pool", "burn") and to in wallets and amt >= dust:
                    direct[to][frm] += amt

    # 4. Wallet yang akumulasi: saldo sekarang + net masuk di jendela
    accum = [a for a, n in net.most_common(max(200, top * 4)) if n >= dust and kinds.get(a, {}).get("kind") == "wallet"]
    bals = eth_calls(chain, [(token, "0x70a08231" + pad(a)[2:]) for a in accum])
    balance = {a: int(b, 16) / 10 ** dec for a, b in zip(accum, bals) if b and b != "0x"}
    accum = [a for a in accum if balance.get(a, 0) > 0]
    log(f"{len(accum)} wallet akumulasi")

    # 6. Sumber dana: stablecoin/WBNB yang masuk ke wallet + token yang dikirim langsung
    funders = defaultdict(Counter)
    targets = accum[:max_funding]
    for i in range(0, len(targets), 40):
        logs = get_logs(chain, list(QUOTES[chain]), [TRANSFER, None, [pad(a) for a in targets[i:i + 40]]],
                        max(start, avail), end)
        for lg in logs:
            q = QUOTES[chain][lg["address"].lower()]
            amt = int(lg["data"], 16) / 10 ** q[1] * (quote_usd.get(lg["address"].lower()) or (1.0 if q[2] is None else 0))
            if amt >= 20:  # abaikan debu < $20
                funders[topic_addr(lg["topics"][2])][topic_addr(lg["topics"][1])] += amt
    senders = {s for c in funders.values() for s in c} | {s for c in direct.values() for s in c}
    kinds.update(classify(chain, [s for s in senders if s not in kinds], labels))
    # Dana/token dari kontrak (router, pool, bonding curve, airdrop) adalah hasil trading,
    # bukan pendanaan antar wallet
    for src_map in (direct, funders):
        for a in list(src_map):
            src_map[a] = Counter({s: v for s, v in src_map[a].items() if kinds[s]["kind"] in ("wallet", "exchange")})

    def source_ok(addr):
        # Sumber yang bermakna untuk cluster: wallet biasa, bukan pool/router/exchange,
        # dan bukan hub yang mengirim ke ratusan alamat (bot airdrop, payment, market maker)
        return kinds.get(addr, {}).get("kind") == "wallet" and len(fanout[addr]) <= 100

    # 5 & 9. Cluster: transfer langsung, sumber dana sama, beli di blok yang sama berulang
    uf = UnionFind()
    why = defaultdict(set)
    acc_set = set(accum)
    for a in accum:
        uf.find(a)
        for src in list(direct[a]) + list(funders[a]):
            if src in acc_set and src != a:
                uf.union(a, src)
                why[a].add(f"terima token dari {short(src)}")
                why[src].add(f"kirim token ke {short(a)}")
    shared = defaultdict(set)
    for a in accum:
        for src in set(direct[a]) | set(funders[a]):
            if source_ok(src):
                shared[src].add(a)
    for src, members in shared.items():
        if len(members) > 1:
            members = sorted(members)
            for m in members[1:]:
                uf.union(members[0], m)
            for m in members:
                why[m].add(f"dana dari sumber sama {short(src)}")
    block_buyers = defaultdict(set)
    for a in accum:
        for b in st[a]["buy_blocks"]:
            block_buyers[b].add(a)
    pair = Counter()
    for buyers in block_buyers.values():
        if 1 < len(buyers) <= 10:
            bl = sorted(buyers)
            for i, x in enumerate(bl):
                for y in bl[i + 1:]:
                    pair[(x, y)] += 1
    coord = Counter()
    for (x, y), n in pair.items():
        # Kebetulan beli di blok yang sama wajar di token ramai; anggap terkoordinasi hanya jika
        # berulang (>= 3x) dan mencakup sebagian besar pembelian kedua wallet
        if n >= 3 and n >= 0.3 * max(len(st[x]["buy_blocks"]), len(st[y]["buy_blocks"])):
            uf.union(x, y)
            coord[x] += n
            coord[y] += n
            why[x].add("beli serentak di blok yang sama")
            why[y].add("beli serentak di blok yang sama")
    # Bundle: >= 3 wallet beli di blok yang sama dengan jumlah hampir identik (selisih <= 5%),
    # pola khas peluncuran four.meme / bundler
    bundled = set()
    for blk, buyers in block_buyers.items():
        amts = sorted((st[a]["buy_blocks"][blk], a) for a in buyers)
        group = [amts[0]] if amts else []
        for amt, a in amts[1:] + [(None, None)]:
            if amt is not None and amt <= group[0][0] * 1.05:
                group.append((amt, a))
                continue
            if len(group) >= 3:
                for _, m in group:
                    uf.union(group[0][1], m)
                    bundled.add(m)
                    why[m].add(f"bundle: {len(group)} wallet beli jumlah sama di blok {blk:,}")
            group = [(amt, a)]

    clusters = defaultdict(list)
    for a in accum:
        clusters[uf.find(a)].append(a)
    cl_id = {}
    for i, members in enumerate(sorted(clusters.values(), key=lambda m: -sum(balance[a] for a in m)), 1):
        for a in members:
            cl_id[a] = (i, members)

    # 10. BANDAR SCORE
    rows = []
    for a in accum:
        s = st[a]
        cid, members = cl_id[a]
        cl_bal = sum(balance[m] for m in members) / supply * 100
        cl_net = sum(net[m] for m in members) / supply * 100
        bought = s["buy_tok"] - s["sell_tok"]
        pnl = roi = None
        if s["cost"] > 0 and price:
            pnl = s["proceeds"] + max(bought, 0) * price - s["cost"]
            roi = pnl / s["cost"] * 100
        traded = s["buy_tok"] + s["sell_tok"]
        fund = [src for src in list(funders[a]) + list(direct[a]) if src != a]
        fund_desc = []
        for src in sorted(set(fund), key=lambda x: -(funders[a][x] + direct[a][x]))[:2]:
            k = kinds.get(src, {})
            fund_desc.append(k.get("label") or f"{k.get('kind', '?')} {short(src)}")
        parts = {
            "pegangan cluster": min(25, cl_bal * 5),
            "akumulasi": min(20, cl_net * 8),
            "dominasi beli": 10 * s["buy_tok"] / traded if traded else (5 if direct[a] else 0),
            "cluster": min(15, (len(members) - 1) * 3),
            "beli serentak": 10 if a in bundled else min(10, coord[a] * 2),
            "sumber dana": (10 if any(source_ok(f) and len(shared[f]) > 1 for f in fund)
                            else 6 if direct[a] else 3 if fund else 0),
            "profit": min(10, max(0, roi / 10)) if roi is not None else 0,
            "tanpa jual": 5 if s["sell_n"] == 0 and s["buy_n"] >= 3 else 0,
        }
        score = min(100, sum(parts.values()))
        reasons = [f"{k} +{v:.0f}" for k, v in sorted(parts.items(), key=lambda kv: -kv[1]) if v >= 1][:4]
        rows.append({
            "wallet": a, "score": round(score, 1), "cluster": cid, "cluster_size": len(members),
            "balance": balance[a], "balance_pct": balance[a] / supply * 100, "net_pct": net[a] / supply * 100,
            "buys": s["buy_n"], "sells": s["sell_n"], "cost_usd": s["cost"], "pnl_usd": pnl, "roi_pct": roi,
            "funding": fund_desc, "links": sorted(why[a]), "reasons": reasons,
        })
    rows.sort(key=lambda r: -r["score"])

    # Top holder aktif (semua jenis) untuk konteks
    top_addrs = [a for a, _ in net.most_common(40) if a not in BURN] + list(pools)
    hb = eth_calls(chain, [(token, "0x70a08231" + pad(a)[2:]) for a in top_addrs])
    holders = sorted({a: int(b, 16) / 10 ** dec for a, b in zip(top_addrs, hb) if b and b != "0x"}.items(),
                     key=lambda kv: -kv[1])[:10]

    # 9. Seberapa banyak wallet lain ikut akumulasi (momentum)
    q3 = start + (end - start) * 3 // 4
    late = {to for tx, blk, frm, to, amt in transfers if blk >= q3 and to in acc_set}
    return {
        "chain": chain, "token": info, "blocks": [start, end], "hours": round(covered, 1), "price_usd": price,
        "transfers": len(transfers), "swaps": sum(map(len, swaps.values())),
        "accumulators": len(accum), "accumulators_last_quarter": len(late),
        "clusters": sum(1 for m in clusters.values() if len(m) > 1),
        "holders": [{"address": a, "balance_pct": b / (supply if kinds.get(a, {}).get("kind") == "wallet"
                                                       else info["supply"]) * 100,
                     "kind": kinds.get(a, {}).get("kind", "?"), "label": kinds.get(a, {}).get("label")}
                    for a, b in holders],
        "ranking": rows[:top],
    }


# --- Output -----------------------------------------------------------------

def fmt_usd(v):
    return "-" if v is None else f"{'-' if v < 0 else ''}${abs(v):,.0f}"


def print_report(rep):
    t = rep["token"]
    price = f"${rep['price_usd']:.8g}" if rep["price_usd"] else "-"
    print(f"\n{t['symbol']} ({rep['chain']}) {t['address']}  harga {price}  "
          f"supply beredar {t['circulating']:,.0f} (total {t['supply']:,.0f})")
    fm = t.get("fourmeme")
    if fm:
        status = "sudah listing di PancakeSwap" if fm["listed"] else f"bonding {fm['progress_pct'] or 0:.1f}%"
        print(f"four.meme: {status}, quote {fm['quote']}")
    print(f"Jendela {rep['hours']} jam | {rep['transfers']:,} transfer, {rep['swaps']:,} swap | "
          f"{rep['accumulators']} wallet akumulasi ({rep['accumulators_last_quarter']} aktif di 1/4 akhir) | "
          f"{rep['clusters']} cluster")

    print("\nTOP HOLDER AKTIF")
    for h in rep["holders"]:
        tag = h["label"] or h["kind"]
        print(f"  {h['address']}  {h['balance_pct']:6.2f}%  {tag}")

    print("\nRANKING BANDAR SCORE")
    hdr = f"{'#':>2} {'SKOR':>5} {'WALLET':<13} {'CL':>6} {'SALDO%':>7} {'NET%':>7} {'B/S':>7} {'PNL':>10}  ALASAN"
    print(hdr)
    print("-" * len(hdr))
    for i, r in enumerate(rep["ranking"], 1):
        cl = f"C{r['cluster']}" + (f"x{r['cluster_size']}" if r["cluster_size"] > 1 else "")
        print(f"{i:>2} {r['score']:>5.1f} {short(r['wallet']):<13} {cl:>6} {r['balance_pct']:>6.2f}% "
              f"{r['net_pct']:>6.2f}% {r['buys']:>3}/{r['sells']:<3} {fmt_usd(r['pnl_usd']):>10}  "
              f"{', '.join(r['reasons'])}")
        extra = r["links"] + ([f"dana: {', '.join(r['funding'])}"] if r["funding"] else [])
        if extra:
            print(f"{'':>30}↳ {'; '.join(extra)}")


def alert_text(rep, rows):
    """Satu pesan per cluster: ringkasan + wallet dengan skor tertinggi."""
    t, top = rep["token"], rows[0]
    lines = [f"🚨 BANDAR {t['symbol']} ({rep['chain']}) skor {top['score']:.0f}",
             f"Cluster C{top['cluster']}: {top['cluster_size']} wallet, {len(rows)} melewati ambang "
             f"(jendela {rep['hours']} jam)",
             ", ".join(top["reasons"])]
    for r in rows[:5]:
        lines.append(f"• {r['wallet']} skor {r['score']:.0f} | saldo {r['balance_pct']:.2f}% "
                     f"net {r['net_pct']:+.2f}% | B/S {r['buys']}/{r['sells']} | PnL {fmt_usd(r['pnl_usd'])}")
    if len(rows) > 5:
        lines.append(f"… +{len(rows) - 5} wallet lain")
    lines.append(f"{EXPLORER[rep['chain']]}{top['wallet']}")
    return "\n".join(lines)


# --- Perintah ---------------------------------------------------------------

def cmd_scan(args):
    rep = scan(args.chain, args.token, args.hours, args.top)
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        print_report(rep)


def cmd_watch(args):
    state_path = Path(args.state)
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    print(f"Memantau {args.token} tiap {args.interval}s, alert jika skor >= {args.min_score}. Ctrl+C untuk berhenti.")
    while True:
        try:
            rep = scan(args.chain, args.token, args.hours, args.top)
        except Exception as exc:
            print(f"[error] {exc}", file=sys.stderr)
            rep = None
        # Alert untuk wallet baru di atas ambang, atau yang skornya naik >= 10
        hits = defaultdict(list)
        for r in (rep or {}).get("ranking", []):
            prev = state.get(f"{args.chain}:{args.token.lower()}:{r['wallet']}")
            if r["score"] >= args.min_score and (prev is None or r["score"] >= prev + 10):
                hits[r["cluster"]].append(r)
        for rows in hits.values():
            msg = alert_text(rep, rows)
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n", flush=True)
            send_telegram(msg)
            for r in rows:
                state[f"{args.chain}:{args.token.lower()}:{r['wallet']}"] = r["score"]
            state_path.write_text(json.dumps(state, indent=2))
        if args.once:
            break
        time.sleep(args.interval)


def main():
    p = argparse.ArgumentParser(description="Deteksi akumulasi bandar pada token EVM (BSC/ETH)")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, func, hlp in (("scan", cmd_scan, "analisis sekali + ranking"),
                            ("watch", cmd_watch, "pantau berkala + alert")):
        s = sub.add_parser(name, help=hlp)
        s.add_argument("--chain", choices=sorted(QUOTES), default="bsc")
        s.add_argument("--token", required=True, help="alamat kontrak token")
        s.add_argument("--hours", type=float, default=24, help="jendela analisis (default 24 jam)")
        s.add_argument("--top", type=int, default=20, help="jumlah wallet di ranking")
        s.set_defaults(func=func)
    sub.choices["scan"].add_argument("--json", action="store_true", help="output JSON")
    w = sub.choices["watch"]
    w.add_argument("--interval", type=int, default=600, help="detik antar analisis (default 600)")
    w.add_argument("--min-score", type=float, default=60, help="skor minimal untuk alert (default 60)")
    w.add_argument("--state", default=str(DEFAULT_STATE), help="file skor yang sudah di-alert")
    w.add_argument("--once", action="store_true", help="analisis sekali lalu keluar (untuk cron)")
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
