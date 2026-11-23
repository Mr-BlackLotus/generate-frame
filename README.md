# Frame Generator

Upload Excel berisi link gambar + frame -> foto utama produk otomatis ditempel di frame baru.

**Versi web (GitHub Pages):** https://mr-blacklotus.github.io/generate-frame/

Semua diproses di browser (tanpa server): Excel dibaca, gambar diunduh, foto utama dipisah dari
background, ditempel di area kosong frame (terdeteksi otomatis, bisa digeser), lalu diunduh sebagai ZIP.

## Catatan
- Jika server gambar tidak mengizinkan akses dari browser (CORS), unduhan otomatis dicoba lewat proxy gambar publik (wsrv.nl).
- Foto dengan background polos (putih/berwarna) dipotong rapi. Foto dengan background rumit ditempel utuh.

## Versi Python (opsional)
Folder `python-server/` berisi versi Flask yang juga memakai GrabCut untuk background rumit:

    cd python-server && pip install -r requirements.txt && python app.py
