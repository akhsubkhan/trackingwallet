# trackingwallet
tracking wallet crypto

Pelacak wallet crypto besar (whale) di **dunia** dan **Indonesia**: menampilkan saldo
BTC/ETH beserta nilainya dalam USD dan Rupiah, dan memberi alert saat ada dana besar
masuk/keluar.

Hanya butuh Python 3.8+ (tanpa library tambahan, tanpa API key). Sumber data:
[mempool.space](https://mempool.space) (BTC), RPC publik Ethereum
(`ethereum-rpc.publicnode.com`), dan [CoinGecko](https://www.coingecko.com) (harga).

## Pemakaian

```bash
# Saldo semua wallet, diurutkan dari nilai terbesar
python3 tracker.py balances

# Hanya grup tertentu
python3 tracker.py --group global balances
python3 tracker.py --group indonesia balances

# Output JSON
python3 tracker.py balances --json

# Pantau terus tiap 5 menit, alert jika saldo berubah >= 100 koin
python3 tracker.py watch --interval 300 --min-change 100

# Cek sekali (cocok untuk cron)
python3 tracker.py watch --once --min-change 100
```

Contoh alert:

```
[2026-10-07 10:15:00] KELUAR 1,250.0000 BTC | Binance Cold Wallet (BTC) | saldo 248,597.1200 -> 247,347.1200
```

## Daftar wallet (`wallets.json`)

Wallet dikelompokkan per grup (`global`, `indonesia`, atau grup buatan sendiri).
Setiap entri: `name`, `chain` (`btc` atau `eth`), `address`. Entri dengan `address`
kosong dilewati.

### Grup `global`

Berisi alamat publik yang sudah dikenal luas: cold wallet Binance & Bitfinex,
Robinhood, alamat genesis Satoshi, kontrak deposit Beacon ETH 2.0, kontrak WETH,
Binance 7, bridge Arbitrum, dan wallet Vitalik Buterin.

### Grup `indonesia`

Entri exchange Indonesia (Indodax, Tokocrypto, Pintu, Reku) sengaja **dikosongkan**:
exchange ini tidak mempublikasikan alamatnya secara resmi dan alamat hot wallet
sering berganti, jadi alamat yang salah lebih berbahaya daripada kosong.
Isi sendiri dari label yang sudah diverifikasi, misalnya:

- Etherscan → cari nama exchange, lihat alamat ber-label (mis. "Indodax")
- [Arkham Intelligence](https://intel.arkm.com) → cari entitas "Indodax", "Tokocrypto", dll.
- Laporan Proof of Reserve dari exchange tersebut (jika ada)

Setelah diisi, jalankan `python3 tracker.py --group indonesia balances`.

> Catatan: label wallet bisa berubah dan saldo exchange tersebar di banyak alamat.
> Angka yang tampil adalah saldo alamat yang terdaftar saja, bukan total aset exchange.
