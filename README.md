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
2. Kirim pesan apa saja ke bot, lalu buka
   `https://api.telegram.org/bot<TOKEN>/getUpdates` untuk melihat `chat.id`.
3. Set environment variable lalu jalankan `watch`:

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC..."
export TELEGRAM_CHAT_ID="123456789"
python3 tracker.py watch
```

Alert akan dikirim ke Telegram sekaligus tampil di terminal.

## Daftar wallet (`wallets.json`)

Wallet dikelompokkan per grup (`global`, `indonesia`, atau grup buatan sendiri).
Setiap entri: `name`, `chain` (lihat tabel di atas), `address`, dan opsional `tokens`
(mis. `["USDT", "USDC"]`). Entri dengan `address` kosong dilewati.

```json
{"name": "Contoh wallet Tron", "chain": "tron", "address": "T...", "tokens": ["USDT"]}
```

### Grup `global`

Berisi alamat publik yang sudah dikenal luas: cold wallet Binance & Bitfinex,
Robinhood, alamat genesis Satoshi, kontrak deposit Beacon ETH 2.0, kontrak WETH,
Binance 7 (beserta saldo USDT/USDC), bridge Arbitrum, dan wallet Vitalik Buterin.

### Grup `indonesia`

Entri exchange Indonesia (Indodax, Tokocrypto, Pintu, Reku — termasuk slot Tron dan
BSC untuk USDT, yang banyak dipakai di Indonesia) sengaja **dikosongkan**:
exchange ini tidak mempublikasikan alamatnya secara resmi dan alamat hot wallet
sering berganti, jadi alamat yang salah lebih berbahaya daripada kosong.
Isi sendiri dari label yang sudah diverifikasi, misalnya:

- Etherscan / BscScan / Tronscan / Solscan → cari nama exchange, lihat alamat ber-label (mis. "Indodax")
- [Arkham Intelligence](https://intel.arkm.com) → cari entitas "Indodax", "Tokocrypto", dll.
- Laporan Proof of Reserve dari exchange tersebut (jika ada)

Setelah diisi, jalankan `python3 tracker.py --group indonesia balances`.

> Catatan: label wallet bisa berubah dan saldo exchange tersebar di banyak alamat.
> Angka yang tampil adalah saldo alamat yang terdaftar saja, bukan total aset exchange.
