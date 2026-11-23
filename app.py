"""Generator gambar berbasis web: Excel (link gambar) + frame -> gambar baru.

Jalankan:  python app.py   lalu buka http://localhost:5000
"""
import io
import ipaddress
import json
import os
import re
import shutil
import socket
import tempfile
import threading
import time
import urllib.parse
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor

import openpyxl
import requests
from flask import Flask, abort, jsonify, request, send_file, send_from_directory
from PIL import Image

import imaging

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024

JOBS = {}
JOBS_DIR = tempfile.mkdtemp(prefix="framegen_")
URL_RE = re.compile(r"https?://[^\s,;\"'<>]+", re.I)
ALLOW_PRIVATE = os.environ.get("ALLOW_PRIVATE_URLS") == "1"
MAX_ROWS = 2000
MAX_IMG_BYTES = 25 * 1024 * 1024


# --------------------------------------------------------------------------
# Excel
# --------------------------------------------------------------------------
def read_rows(file_storage):
    """Return list of dict(sku, desc, url) dari sheet pertama."""
    wb = openpyxl.load_workbook(file_storage, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    if not rows:
        return []
    header = [str(c or "").strip().lower() for c in rows[0]]

    def find(*keys):
        for i, hname in enumerate(header):
            if any(k in hname for k in keys):
                return i
        return None

    url_col = find("link", "url", "gambar", "image", "foto")
    if url_col is None:  # cari kolom pertama yang isinya URL
        for i in range(len(header)):
            if any(isinstance(r[i], str) and URL_RE.search(r[i]) for r in rows[1:6] if len(r) > i):
                url_col = i
                break
    if url_col is None:
        raise ValueError("Kolom link gambar tidak ditemukan di Excel.")
    sku_col = find("sku", "kode", "id")
    desc_col = find("deskripsi", "description", "nama", "name", "produk")
    if sku_col is None:
        sku_col = 0 if url_col != 0 else None

    out = []
    for n, r in enumerate(rows[1:], start=2):
        cell = r[url_col] if len(r) > url_col else None
        m = URL_RE.search(str(cell)) if cell else None
        if not m:
            continue
        get = lambda c: (str(r[c]).strip() if c is not None and len(r) > c and r[c] is not None else "")
        out.append({"row": n, "sku": get(sku_col) or f"row{n}", "desc": get(desc_col), "url": m.group(0)})
    return out[:MAX_ROWS]


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------
def check_url(url):
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("URL tidak valid")
    if ALLOW_PRIVATE:
        return
    for info in socket.getaddrinfo(p.hostname, None):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("Alamat internal tidak diizinkan")


def download(url):
    check_url(url)
    r = requests.get(
        url,
        timeout=(8, 30),
        stream=True,
        headers={"User-Agent": "Mozilla/5.0 (FrameGenerator)"},
        allow_redirects=False,
    )
    hops = 0
    while r.is_redirect and hops < 4:
        nxt = urllib.parse.urljoin(url, r.headers["Location"])
        check_url(nxt)
        r = requests.get(nxt, timeout=(8, 30), stream=True, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=False)
        hops += 1
    r.raise_for_status()
    buf = io.BytesIO()
    for chunk in r.iter_content(65536):
        buf.write(chunk)
        if buf.tell() > MAX_IMG_BYTES:
            raise ValueError("File gambar terlalu besar")
    return buf.getvalue()


# --------------------------------------------------------------------------
# Job
# --------------------------------------------------------------------------
def safe_name(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")[:80] or "img"


def process_item(job, item, frame):
    try:
        im = imaging.load_rgb(download(item["url"]))
        subj = imaging.extract_subject(im, job["remove_bg"])
        out = imaging.compose(frame, job["rect"], subj, job["padding"], job["shadow"])
        ext = job["fmt"]
        fname = f"{safe_name(item['sku'])}_r{item['row']}.{ext}"
        path = os.path.join(job["dir"], "out", fname)
        if ext == "jpg":
            out.save(path, "JPEG", quality=job["quality"], optimize=True)
        else:
            out.save(path, "PNG", optimize=True)
        item.update(status="ok", file=fname, mode=subj[2])
    except Exception as e:  # noqa: BLE001
        item.update(status="error", error=str(e)[:200])
    finally:
        job["done"] += 1


def run_job(job):
    frame = Image.open(os.path.join(job["dir"], "frame.png")).convert("RGB")
    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(lambda it: process_item(job, it, frame), job["items"]))
    # ringkasan Excel
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["SKU Pendek", "Deskripsi", "Link Gambar", "File Hasil", "Status"])
    for it in job["items"]:
        ws.append([it["sku"], it["desc"], it["url"], it.get("file", ""), it.get("status") == "ok" and "OK" or it.get("error", "")])
    wb.save(os.path.join(job["dir"], "hasil.xlsx"))
    job["finished"] = True


@app.post("/api/detect")
def api_detect():
    f = request.files.get("frame")
    if not f:
        return jsonify(error="Frame belum diupload"), 400
    try:
        frame = Image.open(f.stream).convert("RGB")
    except Exception:
        return jsonify(error="File frame bukan gambar yang valid"), 400
    x, y, w, h = imaging.find_content_rect(frame)
    return jsonify(width=frame.width, height=frame.height, rect=dict(x=x, y=y, w=w, h=h))


@app.post("/api/start")
def api_start():
    xf, ff = request.files.get("excel"), request.files.get("frame")
    if not xf or not ff:
        return jsonify(error="Excel dan frame wajib diupload"), 400
    try:
        items = read_rows(xf.stream)
    except Exception as e:  # noqa: BLE001
        return jsonify(error=f"Gagal membaca Excel: {e}"), 400
    if not items:
        return jsonify(error="Tidak ada link gambar di Excel"), 400
    try:
        frame = Image.open(ff.stream).convert("RGB")
        r = json.loads(request.form.get("rect", "{}"))
        rect = tuple(int(r[k]) for k in ("x", "y", "w", "h"))
    except Exception:
        return jsonify(error="Frame atau area tidak valid"), 400
    x, y, w, h = rect
    if w < 20 or h < 20 or x < 0 or y < 0 or x + w > frame.width or y + h > frame.height:
        return jsonify(error="Area penempatan di luar frame"), 400

    jid = uuid.uuid4().hex[:12]
    d = os.path.join(JOBS_DIR, jid)
    os.makedirs(os.path.join(d, "out"))
    frame.save(os.path.join(d, "frame.png"))
    job = dict(
        id=jid,
        dir=d,
        items=items,
        done=0,
        finished=False,
        rect=rect,
        padding=min(0.3, max(0.0, float(request.form.get("padding", 6)) / 100)),
        shadow=request.form.get("shadow", "1") == "1",
        remove_bg=request.form.get("remove_bg", "1") == "1",
        fmt="png" if request.form.get("fmt") == "png" else "jpg",
        quality=int(request.form.get("quality", 92)),
        created=time.time(),
    )
    JOBS[jid] = job
    threading.Thread(target=run_job, args=(job,), daemon=True).start()
    return jsonify(id=jid, total=len(items))


@app.get("/api/job/<jid>")
def api_job(jid):
    job = JOBS.get(jid) or abort(404)
    return jsonify(
        total=len(job["items"]),
        done=job["done"],
        finished=job["finished"],
        items=[
            {k: it.get(k) for k in ("sku", "desc", "status", "file", "error", "mode")}
            for it in job["items"]
        ],
    )


@app.get("/api/job/<jid>/img/<name>")
def api_img(jid, name):
    job = JOBS.get(jid) or abort(404)
    return send_from_directory(os.path.join(job["dir"], "out"), name, as_attachment=request.args.get("dl") == "1")


@app.get("/api/job/<jid>/zip")
def api_zip(jid):
    job = JOBS.get(jid) or abort(404)
    if not job["finished"]:
        abort(409)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for it in job["items"]:
            if it.get("status") == "ok":
                z.write(os.path.join(job["dir"], "out", it["file"]), it["file"])
        z.write(os.path.join(job["dir"], "hasil.xlsx"), "hasil.xlsx")
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True, download_name="hasil_frame.zip")


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


def cleanup_loop():
    while True:
        time.sleep(600)
        for jid, job in list(JOBS.items()):
            if time.time() - job["created"] > 3 * 3600:
                shutil.rmtree(job["dir"], ignore_errors=True)
                JOBS.pop(jid, None)


threading.Thread(target=cleanup_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), threaded=True)
