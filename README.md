# trackingwallet
tracking wallet crypto

Pelacak wallet crypto besar (whale) di **dunia** dan **Indonesia**: menampilkan saldo
koin dan token (USDT/USDC) beserta nilainya dalam USD dan Rupiah, serta memberi alert
(terminal + Telegram) saat ada dana besar masuk/keluar.

Hanya butuh Python 3.9+ (tanpa library tambahan, tanpa API key). Sumber data:

| Chain (`chain`) | Koin | Token (`tokens`) | Sumber |
|---|---|---|---|
| `btc` | BTC | - | [mempool.space](https://mempool.space) |
| `eth` | ETH | USDT, USDC | `ethereum-rpc.publicnode.com` |
| `bsc` | BNB | USDT | `bsc-rpc.publicnode.com` |
| `sol` | SOL | USDT, USDC | `api.mainnet-beta.solana.com` |
| `tron` | TRX | USDT | [TronGrid](https://www.trongrid.io) |

Harga USD/IDR dari [CoinGecko](https://www.coingecko.com).

## Pemakaian

```bash
# Saldo semua wallet, diurutkan dari nilai terbesar
python3 tracker.py balances

# Hanya grup tertentu
python3 tracker.py --group global balances
python3 tracker.py --group indonesia balances

# Output JSON
python3 tracker.py balances --json

# Pantau terus tiap 5 menit, alert jika perubahan >= $1 juta (default)
python3 tracker.py watch --interval 300 --min-usd 1000000

# Cek sekali (cocok untuk cron)
python3 tracker.py watch --once --min-usd 5000000
```

Contoh alert:

```
[2026-10-07 10:15:00] KELUAR 1,250.0000 BTC (~$125,000,000) | Binance Cold Wallet (BTC) | saldo 248,597.1200 -> 247,347.1200
```

Jika harga gagal diambil, setiap perubahan saldo akan di-alert.

## Notifikasi Telegram

1. Buat bot lewat [@BotFather](https://t.me/BotFather), salin token-nya.
2. Buka chat dengan bot Anda di Telegram, tekan **Start** / kirim pesan apa saja, lalu buka
   `https://api.telegram.org/bot<TOKEN>/getUpdates` dan salin `message.chat.id`.
   **Jangan** pakai angka di depan token (itu ID bot sendiri — bot tidak bisa
   mengirim pesan ke dirinya sendiri). Untuk grup, tambahkan bot ke grup; ID grup
   diawali `-`.
3. Set environment variable lalu jalankan `watch`:

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC..."
export TELEGRAM_CHAT_ID="123456789"
python3 tracker.py watch
```

Alert akan dikirim ke Telegram sekaligus tampil di terminal.

## Jalan otomatis di GitHub Actions (tanpa laptop menyala)

Workflow `.github/workflows/watch.yml` menjalankan `watch --once` tiap 15 menit
di server GitHub dan mengirim alert ke Telegram.

1. Buka repo → **Settings → Secrets and variables → Actions → New repository secret**,
   tambahkan `TELEGRAM_BOT_TOKEN` dan `TELEGRAM_CHAT_ID`.
2. (Opsional) Di tab **Variables**, buat `MIN_USD` untuk mengubah batas alert
   (default `1000000`).
3. Buka tab **Actions → Whale watch → Run workflow** untuk tes pertama. Run pertama
   hanya mencatat saldo awal; alert muncul mulai run berikutnya.

Catatan:
- Jadwal GitHub bisa telat 5–30 menit saat sibuk, kadang satu jadwal terlewat.
- Saldo terakhir disimpan di cache Actions (bukan di-commit ke repo).
- Repo privat punya kuota 2.000 menit/bulan; tiap 15 menit ≈ 2.900 run/bulan, jadi
  bisa melebihi kuota. Ubah `cron` di workflow (mis. `7,37 * * * *` = tiap 30 menit)
  atau jadikan repo publik.
- Di repo publik, GitHub menonaktifkan jadwal jika repo tidak ada aktivitas 60 hari;
  aktifkan lagi dari tab Actions.

## Daftar wallet (`wallets.json`)

Wallet dikelompokkan per grup (`global`, `indonesia`, atau grup buatan sendiri).
Setiap entri: `name`, `chain` (lihat tabel di atas), `address`, dan opsional `tokens`
(mis. `["USDT", "USDC"]`). Entri dengan `address` kosong dilewati.

```json
{"name": "Contoh wallet Tron", "chain": "tron", "address": "T...", "tokens": ["USDT"]}
```

### Grup `global`

Berisi alamat publik yang sudah dikenal luas: cold wallet Binance & Bitfinex,
Robinhood, alamat genesis Satoshi, kontrak WETH,
Binance 7, bridge Arbitrum, dan wallet Vitalik Buterin, ditambah wallet Binance
di chain lain:

| Nama | Chain | Label explorer |
|---|---|---|
| Binance Hot Wallet 6 | `bsc` | BscScan "Binance: Hot Wallet 6" |
| Binance Hot Wallet 20 | `bsc` | BscScan "Binance: Hot Wallet 20" |
| Binance 2 | `sol` | Solscan "Binance 2" |
| Binance-Hot 7 | `tron` | Tronscan "Binance-Hot 7" |

### Grup `indonesia`

Yang sudah terisi hanya alamat yang berlabel publik di Etherscan:

- **Indodax 1** (`0x5183…d521`) dan **Indodax 2** (`0x9cba…2719`): hot wallet
  Indodax yang aktif (ratusan ribu transaksi), tapi saldonya kecil karena dana
  langsung diteruskan. Alert akan jarang muncul dengan `--min-usd` default; pakai
  nilai lebih kecil, mis. `--min-usd 10000`.

Entri lain (Indodax BTC/Tron, Tokocrypto, Pintu, Reku) masih **dikosongkan**:
exchange ini tidak mempublikasikan alamatnya secara resmi dan alamat hot wallet
sering berganti, jadi alamat yang salah lebih berbahaya daripada kosong.
(Alamat berlabel "Tokocrypto" di Etherscan sudah tidak dipakai/saldo 0; sejak
diakuisisi Binance, dana Tokocrypto kemungkinan besar ada di wallet Binance.)
Isi sendiri dari label yang sudah diverifikasi, misalnya:

- Etherscan / BscScan / Tronscan / Solscan → cari nama exchange, lihat alamat ber-label (mis. "Indodax")
- [Arkham Intelligence](https://intel.arkm.com) → cari entitas "Indodax", "Tokocrypto", dll.
- Laporan Proof of Reserve dari exchange tersebut (jika ada)

Setelah diisi, jalankan `python3 tracker.py --group indonesia balances`.

> Catatan: label wallet bisa berubah dan saldo exchange tersebar di banyak alamat.
> Angka yang tampil adalah saldo alamat yang terdaftar saja, bukan total aset exchange.

## Beli token besar (`bigbuy.py`)

Alert setiap kali sebuah wallet **membeli token apa pun** dalam jumlah besar di DEX
BSC / Ethereum (PancakeSwap, Uniswap V2/V3, dan fork-nya) serta bonding curve four.meme.
Tidak perlu tahu tokennya dulu: semua pasar dipindai.

```bash
# Terus-menerus, cek tiap 60 detik, alert beli >= $10.000 (BSC + ETH)
python3 bigbuy.py

# Atur sendiri
python3 bigbuy.py --chains bsc --min-usd 25000 --interval 30
python3 bigbuy.py --once            # sekali jalan (cron / GitHub Actions)
```

Contoh alert:

```
🟢 BELI BESAR quq · BSC DEX · $65.5K · 3x beli
Pembeli: 0xe269470dce1f6676494f5114c80a249055ab26e5
⭐ Rekam jejak: 4/5 token naik ≥50% (puncak rata-rata +180%, sekarang +60%)
Dapat: 40,717,212 quq · bayar 65,546 USDT
Token: 0x4fa7c69a7b69f8bc48233024d546bc299d6b03bf
https://dexscreener.com/bsc/0x4fa7…
https://bscscan.com/tx/0x4b95…
```

Cara kerja:

1. Semua `Transfer` token quote (WBNB/USDT/USDC/BUSD/FDUSD/USD1 di BSC,
   WETH/USDT/USDC/DAI di ETH) di blok baru diambil lewat RPC publik.
2. Transfer quote bernilai besar yang **masuk ke pool DEX** (alamat yang punya
   `token0/token1`) berarti seseorang membayar untuk token pasangan di pool itu.
3. Receipt transaksi dicek: harus ada event `Swap` dari pool tersebut dan
   **pengirim transaksi menerima token itu**. Add liquidity, arbitrase, dan bot
   yang menyimpan token di kontraknya otomatis tersaring.
4. Pembeli yang menjual lagi token yang sama dalam 2 blok (sandwich/MEV) dibuang.
5. Pembeli harus menerima ≥ 50% token yang keluar dari pool (sisanya routing/arbitrase).
6. Beberapa beli wallet yang sama untuk token yang sama dalam satu cek digabung
   (`3x beli`). Label exchange dari `labels.json` dan tanda **wallet baru**
   (≤ 5 transaksi) ikut ditampilkan.

### Rekam jejak pembeli

Setiap pembelian ≥ `--track-usd` (default $5.000, walau di bawah ambang alert) dicatat
beserta harga belinya. Tiap cek berikutnya, harga token dibaca langsung dari pool
on-chain tempat ia membeli (V2 `getReserves`, V3 `slot0`, four.meme harga curve)
selama **7 hari**, dan harga puncaknya disimpan. Alert lalu menampilkan:

- `⭐ Rekam jejak: 4/5 token naik ≥50%`: dari token lain yang pernah dibeli wallet ini
  (tercatat oleh tool ini, ≥ 1 jam lalu), 4 dari 5 pernah naik ≥ 50% dari harga belinya.
  ⭐ jika ≥ setengahnya naik, 📉 jika tidak.
- `puncak rata-rata`: rata-rata kenaikan tertinggi; `sekarang`: rata-rata posisi saat ini.
- `Rekam jejak: belum ada`: wallet ini belum pernah tercatat membeli token lain.

`--smart-only` hanya mengirim alert untuk pembeli dengan ≥ 2 token di rekam jejak
(`--smart-min`) dan ⭐. Di GitHub Actions: buat variable `BIGBUY_ARGS` = `--smart-only`.

Catatan:
- Rekam jejak **mulai dari nol** saat tool ini mulai jalan; makin lama jalan, makin berguna.
- Yang diukur adalah **kenaikan harga setelah wallet membeli**, bukan profit yang benar-benar
  direalisasikan (kapan wallet menjual tidak dilacak). Token yang di-rug turun ke ~-100%.
- Harga dicek tiap run (15 menit), jadi lonjakan singkat di antaranya bisa terlewat.
- Token four.meme yang sudah pindah ke PancakeSwap berhenti diperbarui harganya.

Di GitHub Actions, `bigbuy.py --once` ikut jalan di workflow `watch.yml` tiap 15 menit
dan memindai semua blok sejak run sebelumnya. Ubah ambang lewat variable
`BIGBUY_MIN_USD` (default `10000`).

Batasan:
- Hanya BSC dan Ethereum. Solana dan Tron tidak bisa dipindai seperti ini dengan API
  publik gratis (perlu indexer berbayar seperti Helius / Birdeye / TronGrid Pro).
- Pembelian yang dibayar BNB/ETH **native langsung ke pool** (Uniswap V4,
  PancakeSwap Infinity) belum terdeteksi. Lewat router V2/V3 tetap terdeteksi karena
  router mengubahnya ke WBNB/WETH dulu.
- Beli token mayor (WBNB, BTCB, stablecoin, stETH, dll.) tidak dianggap beli token.
- RPC publik BSC hanya menyimpan log ±75 menit; jika jeda antar cek lebih dari
  60 menit (`--max-minutes`), blok yang lebih lama dilewati.
- Token Binance Alpha sering dibeli berulang oleh wallet farming volume; alert-nya
  asli (memang beli), tapi belum tentu "smart money".

## Deteksi bandar (`bandar.py`)

Analisis satu token EVM (BSC / ETH) untuk mencari wallet "bandar" yang sedang
akumulasi, lalu memberi peringkat **BANDAR SCORE** (0–100) dan alert.

```
TOKEN → holder aktif → filter exchange / LP / kontrak → wallet yang akumulasi
      → cluster wallet → profit → sumber dana → transaksi DEX
      → wallet lain ikut akumulasi? → BANDAR SCORE → ALERT
```

```bash
# Analisis sekali (default jendela 24 jam, 20 wallet teratas)
python3 bandar.py scan --chain bsc --token 0xALAMAT_TOKEN
python3 bandar.py scan --chain eth --token 0xALAMAT_TOKEN --hours 6 --top 30 --json

# Token four.meme: sama saja, dikenali otomatis
python3 bandar.py scan --chain bsc --token 0x....4444

# Pantau terus: analisis skor tiap 60 detik + alert instan tiap 3 detik
python3 bandar.py watch --chain bsc --token 0xALAMAT_TOKEN --min-score 60

# Atur sendiri: ambang beli/jual besar 0,5% supply, cek instan tiap 2 detik
python3 bandar.py watch --chain bsc --token 0xALAMAT_TOKEN --big-pct 0.5 --poll 2
```

`watch` punya dua lapis alert (terminal + Telegram):

| Alert | Kapan | Delay |
|---|---|---|
| 🟢/🔴 **BELI/JUAL BESAR** | Satu transaksi DEX / bonding curve ≥ `--big-pct` % supply beredar (default 1%) | ±3–5 detik (`--poll`) |
| 🚨 **BUNDLE** | ≥ 3 wallet beli jumlah hampir sama di satu blok | ±3–5 detik |
| 🚨 **BANDAR** (skor) | Wallet/cluster mencapai `--min-score`, atau skornya naik ≥ 10 | ≤ 60 detik (`--interval`) |

Wallet yang sudah masuk alert bundle tidak dikirim ulang sebagai beli besar.
Matikan alert instan dengan `--no-instant`.

Contoh alert (satu pesan per cluster):

```
🚨 BANDAR CATE (bsc) skor 87
Cluster C1: 24 wallet, 12 melewati ambang (jendela 1.3 jam)
pegangan cluster +25, akumulasi +20, cluster +15, profit +10
• 0x0097…9b11 skor 87 | saldo 0.74% net +0.74% | B/S 13/13 | PnL $61,145
…
```

### Cara kerja

| Tahap | Yang dilakukan |
|---|---|
| Holder aktif | Semua event `Transfer` token di jendela waktu → arus masuk/keluar per alamat, saldo via `balanceOf` |
| Filter | Exchange (label di `labels.json` + `wallets.json`), pool DEX (punya `token0/token1`), kontrak lain, alamat burn. Persentase dihitung dari **supply beredar** (total − burn) |
| Transaksi DEX | Event `Swap` Uniswap/PancakeSwap V2 & V3 di pool token → beli/jual per wallet + nilai USD |
| four.meme | Token four.meme (BSC) otomatis dikenali: beli/jual di bonding curve (`TokenPurchase`/`TokenSale` TokenManager V2) ikut dihitung, quote BNB/USDT/token lain, progres bonding & status listing ditampilkan, token yang masih di curve tidak dihitung beredar |
| Profit | PnL token ini di jendela: hasil jual + nilai token yang masih dipegang − modal beli |
| Sumber dana | USDT/USDC/WBNB/dll. yang masuk ke wallet (≥ $20) dan token yang dikirim langsung antar wallet (≥ 0,01% supply) |
| Cluster | Wallet disatukan jika: saling kirim token, didanai wallet yang sama, berulang kali beli di blok yang sama, atau **bundle** (≥ 3 wallet beli jumlah hampir sama, selisih ≤ 5%, di satu blok — pola bundler peluncuran four.meme). Hub yang mengirim ke >100 alamat (bot airdrop) diabaikan |
| Ikut akumulasi | Jumlah wallet akumulasi dan berapa yang aktif di ¼ akhir jendela |

**BANDAR SCORE** (maks. 100):

| Komponen | Poin |
|---|---|
| Pegangan cluster (% supply beredar) | s/d 25 |
| Akumulasi bersih cluster di jendela | s/d 20 |
| Cluster (jumlah wallet terhubung) | s/d 15 |
| Dominasi beli (beli vs jual) | s/d 10 |
| Beli serentak di blok yang sama / bundle | s/d 10 |
| Sumber dana (didanai wallet yang sama / terima token langsung) | s/d 10 |
| Profit (ROI) | s/d 10 |
| Beli ≥ 3x tanpa pernah jual | 5 |

### Batasan

- **Riwayat RPC.** RPC publik default (publicnode) hanya melayani log ~10.000 blok
  terakhir: **BSC ±75 menit**, ETH ±33 jam. Jendela otomatis dipotong (ada
  peringatan). Solusi:
  - Jalankan `watch`: log disimpan di `cache/`, jadi riwayat terkumpul sendiri
    selama program berjalan.
  - Atau pakai RPC sendiri yang menyimpan riwayat (NodeReal, Ankr, Alchemy,
    QuickNode, token pribadi publicnode, dll.):
    `export BSC_RPC="https://..."` / `export ETH_RPC="https://..."`.
- **Holder yang diam** (tidak bergerak di jendela) tidak terlihat; top holder yang
  ditampilkan adalah holder yang aktif.
- **Profit** hanya untuk token ini di jendela waktu; riwayat profit di token lain
  butuh indexer berbayar. Harga WBNB/WETH memakai harga saat ini.
- **Sumber dana** hanya dari token ERC-20 (stablecoin/WBNB/WETH). Pendanaan BNB/ETH
  native tidak terlihat lewat event log.
- Belum mendukung Uniswap V4 dan token four.meme versi lama (TokenManager V1, 2024).
  Pembeli four.meme umumnya membayar BNB native, jadi sumber dananya sering tidak
  terlihat; deteksi bundle tetap jalan karena berbasis pola pembelian.
- Skor adalah **heuristik**, bukan bukti. Bot volume/MEV dan market maker bisa
  terlihat mirip bandar. Selalu cek manual di explorer sebelum mengambil keputusan.
