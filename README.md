# Frame Generator

    pip install -r requirements.txt
    python app.py          # buka http://localhost:5000

1. Upload Excel (kolom link gambar terdeteksi otomatis; SKU & Deskripsi ikut dibaca).
2. Upload frame -> area kosong putih terdeteksi otomatis (kotak oranye, bisa digeser/diubah ukurannya).
3. Generate -> unduh satuan atau ZIP (berisi gambar + hasil.xlsx ringkasan status).

Cara kerja: foto diunduh, foto utama dipisah dari background (background seragam -> deteksi tepi;
background rumit -> GrabCut, atau rembg bila terpasang), lalu diskalakan dan dipusatkan di area frame
dengan bayangan lembut. Server tidak mengizinkan link ke alamat internal/localhost
(set ALLOW_PRIVATE_URLS=1 hanya untuk testing).
