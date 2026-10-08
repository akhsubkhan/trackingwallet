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

Workflow `.github/workflows/watch.yml` menjalankan `bigbuy.py --once` (alert beli token,
TOP 5) tiap 15 menit di server GitHub dan mengirim alert ke Telegram. Alert saldo whale
`tracker.py` **tidak** dijadwalkan (pergerakan hot wallet exchange terlalu ramai & lemah
sinyalnya); jalankan manual bila perlu: `python3 tracker.py watch`.

1. Buka repo → **Settings → Secrets and variables → Actions → New repository secret**,
   tambahkan `TELEGRAM_BOT_TOKEN` dan `TELEGRAM_CHAT_ID`.
2. (Opsional) Di tab **Variables**, buat `BIGBUY_MIN_USD` (default `10000`) dan
   `BIGBUY_ARGS` (mis. `--smart-only`).
3. Buka tab **Actions → Whale watch → Run workflow** untuk tes pertama.

Catatan:
- Jadwal GitHub bisa telat berjam-jam / terlewat. Agar tepat tiap 15 menit, picu workflow
  dari luar (mis. cron-job.org: `POST https://api.github.com/repos/<owner>/<repo>/actions/workflows/watch.yml/dispatches`,
  header `Authorization: Bearer <fine-grained token, izin Actions read/write>`, body `{"ref":"main"}`).
- State disimpan di cache Actions (bukan di-commit ke repo).
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

Satu alert **per token** per cek: semua pembelian token itu dijumlahkan.

```
🟢 AKUMULASI 登月 · BSC · $118.2K
38 wallet · 81x beli · ±12 menit terakhir
Total: 221,999,081 登月 (22.20% supply)
24 jam: $410.5K dari 96 wallet
• 0x6fa4…6220 $7.4K 5x ⭐3/4
• 0x5736…951b $6.2K 4x 🆕
• 0x7f28…b392 $6.1K 4x
• 0x8595…bb47 $5.8K 4x 📉0/2
• 0x1930…cca6 $5.4K 3x (Binance Hot Wallet 10)
… +33 wallet lain ($87.4K)
0xa91d549aeb83dc058c12157449be2a677ee60a4a
```

| Bagian | Artinya |
|---|---|
| `AKUMULASI` / `BELI BESAR` | ≥ 2 wallet / 1 wallet membeli token ini di cek ini |
| `$118.2K` | Total beli token ini sejak cek sebelumnya (±15 menit); alert jika ≥ `--min-usd` |
| `81x beli` · `Total` · `% supply` | Jumlah transaksi, total token dibeli, dan persentasenya dari total supply |
| `24 jam` | Total beli token ini 24 jam terakhir (semua cek), muncul jika lebih besar dari cek ini |
| `• 0x6fa4…6220 $7.4K 5x` | Pembeli (5 terbesar), total belinya, berapa kali beli. Alamat & nominal bisa diklik (explorer / tx terakhir); nama token → DexScreener |
| `⭐3/4` / `📉0/2` | Rekam jejak (lihat bawah) · `🆕` wallet baru (≤ 5 transaksi) · `(…)` label exchange |

Hanya pembelian ≥ `--track-usd` per wallet (default $2.000) yang ikut dihitung.

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

### Hanya token besar (`--top-mcap`)

Di GitHub Actions default-nya **hanya 50 token market cap terbesar** yang punya kontrak
di BSC/ETH (variable `BIGBUY_TOP_MCAP`, `0` = semua token termasuk meme kecil). Daftar
diambil dari CoinGecko sekali sehari; stablecoin, aset yang dipatok (emas, treasury) dan
token mayor (WBNB/WETH/WBTC/…) tidak dihitung. Contoh isi: LINK, UNI, AAVE, PEPE, SHIB,
ONDO, ENA, ARB, CAKE, LDO, PENDLE. Alert menampilkan peringkatnya, mis. `#48 mcap`.
Jika CoinGecko gagal, daftar terakhir tetap dipakai; jika belum pernah ada, tidak ada alert.

### Rekam jejak pembeli

Setiap pembelian ≥ `--track-usd` (default $2.000) dicatat
beserta harga belinya. Tiap cek berikutnya, harga token dibaca langsung dari pool
on-chain tempat ia membeli (V2 `getReserves`, V3 `slot0`, four.meme harga curve)
selama **7 hari**, dan harga puncaknya disimpan. Alert lalu menampilkan:

- `⭐4/5`: dari token lain yang pernah dibeli wallet ini (tercatat oleh tool ini, ≥ 1 jam
  lalu), 4 dari 5 pernah naik ≥ 50% dari harga belinya. ⭐ jika ≥ setengahnya naik, 📉 jika tidak.
- Tanpa tanda: wallet ini belum pernah tercatat membeli token lain.

`--smart-only` hanya mengirim alert token yang dibeli minimal satu pembeli dengan ≥ 2 token
di rekam jejak (`--smart-min`) dan ⭐. Di GitHub Actions: buat variable `BIGBUY_ARGS` = `--smart-only`.

Catatan:
- Rekam jejak **mulai dari nol** saat tool ini mulai jalan; makin lama jalan, makin berguna.
- Yang diukur adalah **kenaikan harga setelah wallet membeli**, bukan profit yang benar-benar
  direalisasikan (kapan wallet menjual tidak dilacak). Token yang di-rug turun ke ~-100%.
- Harga dicek tiap run (15 menit), jadi lonjakan singkat di antaranya bisa terlewat.
- Token four.meme yang sudah pindah ke PancakeSwap berhenti diperbarui harganya.

### Deteksi volume diputar (wash trading)

Setiap cek, untuk semua pembeli dicari wallet yang mengirim USDT/WBNB/dll. kepadanya
sampai 10 menit sebelum ia membeli (pendana). Pool DEX, wallet berlabel, dan **hub**
(pendana yang menerima dana dari ≥ 15 alamat berbeda, ciri hot wallet exchange) tidak
dihitung.

```
⚠️ 11/11 pembeli didanai 1 wallet 0xb406…07ef
🚫 Volume diputar: pendana itu juga menjual ke pool token ini
```

- `⚠️ N/M pembeli didanai 1 wallet`: ≥ 3 pembeli dan ≥ 30% pembeli token itu didanai
  wallet yang sama → kemungkinan satu operator memakai banyak wallet. Di TOP 5: skor −10.
- `🚫 Volume diputar`: pendana itu juga menerima quote dari pool token tersebut (menjual
  token, lalu membagi hasilnya ke wallet baru yang membeli lagi). "Banyak wallet
  akumulasi" di token ini palsu; token **dicoret dari TOP 5 selama 24 jam**.

Pendanaan BNB/ETH native tidak terlihat lewat event log, jadi operator yang mendanai
dengan BNB native belum tertangkap.

### TOP 5 sinyal akumulasi (tiap jam)

Sekali per jam (`--top-every`, menit; `0` = mati) dikirim peringkat 5 token per chain
dari data **24 jam** terakhir:

```
📊 TOP 5 SINYAL AKUMULASI · BSC · 24 jam
Skor dari data on-chain, bukan saran investasi. Cek kontrak & chart sebelum beli.

1. 登月 · skor 78/100
Volume beli $410.5K / jual $180.2K (net +$230.3K)
96 wallet pembeli (⭐4)
Likuiditas $350.0K · harga +35% sejak dibeli
Narasi: tidak ada deskripsi publik
Website · X · Telegram · Mcap $1.25M · pool umur 2 hari
Dibeli oleh: KOL @budi, moonboy.bnb, smart money 0x6fa4…6220 ⭐3/4
⚠️ 62% pembeli wallet baru
0xa91d549aeb83dc058c12157449be2a677ee60a4a
```

| Komponen skor | Poin |
|---|---|
| Wallet pembeli berbeda (≥ `--track-usd`) | s/d 25 (20 wallet = penuh) |
| Arus bersih: volume quote masuk pool − keluar pool | s/d 20 ($250K = penuh) |
| Pembeli dengan rekam jejak ⭐ | s/d 20 (3 wallet = penuh) |
| Likuiditas pool (sisi quote; four.meme: dana di curve) | s/d 20 ($20K = 0, $200K = penuh) |
| Harga sekarang vs harga beli (median pembeli) | s/d 15 (−20% = 0, +50% = penuh) |
| ⚠️ 1 wallet > 50% pembelian / > 50% pembeli wallet baru (bundle/bot) / pembeli didanai 1 wallet | −10 masing-masing |

Token **dicoret** jika: volume diputar (24 jam), skor < 30, < 3 wallet pembeli, tidak net beli (beli harus ≥ 1,1× jual),
likuiditas < $20K, atau harga median turun > 30% dari harga beli (indikasi rug).

> ⚠️ Ini peringkat sinyal, **bukan rekomendasi beli**. Tool ini tidak memeriksa kontrak
> (honeypot, pajak jual, mint, owner), tim, atau berita; volume bisa dipalsukan bot.
> Selalu cek sendiri (mis. DexScreener, honeypot checker, explorer) sebelum membeli.

### Narasi & pembeli yang dikenal

Setiap token di TOP 5 dilengkapi:

- **Narasi**: deskripsi & kategori dari CoinGecko (hanya token yang sudah listing di
  sana; token meme baru umumnya belum). `tidak ada deskripsi publik` = sumber bisa
  diakses tapi token tidak punya deskripsi. (API four.meme privat, tidak bisa dipakai.)
- **Link** website / X / Telegram / Discord (DexScreener, CoinGecko), market cap dan
  umur pool. `⚠️ Tanpa website/sosial media` = tidak ada satu pun link publik.
- **Dibeli oleh**: pembeli yang dikenal, otomatis dari tiga sumber:
  1. **Nama on-chain** `.eth` (ENS) / `.bnb` (Space ID) yang dipasang pemilik wallet
     sendiri, hanya jika cocok dua arah (alamat → nama → alamat yang sama). Banyak KOL &
     trader memakainya. Nama ini juga tampil di daftar pembeli alert, mis. `(grumpyx.eth)`.
  2. **Smart money otomatis**: pembeli dengan rekam jejak ⭐ (≥ 2 token, ≥ setengahnya naik
     ≥ 50%), mis. `smart money 0x6fa4…6220 ⭐3/4`.
  3. **Label manual** di `labels.json` / `wallets.json`. Tambahkan sendiri wallet KOL /
     fund yang Anda tahu (sumber: GMGN, Arkham, DeBank, Dune, bio X), mis.:

  ```json
  "labels": {
    "0x1234...abcd": "KOL @namaakun",
    ...
  }
  ```

  Ini "endorsement" on-chain: siapa yang benar-benar membeli, bukan siapa yang
  ngetwit. Pantauan promosi di X/Twitter tidak termasuk (butuh API berbayar).

**Cek satu token kapan saja**: tab **Actions → Whale watch → Run workflow**, isi
`token` (alamat kontrak) dan pilih `chain`. Narasi, link, aktivitas 24 jam dan pembeli
yang dikenal dikirim ke Telegram. Atau lokal: `python3 bigbuy.py --chains bsc --info 0x...`.

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
