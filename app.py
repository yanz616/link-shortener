import os
import random
import string
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask, request, jsonify, redirect, abort
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import render_template
from flask_cors import CORS

load_dotenv()

app = Flask(__name__)
CORS(app)
BASE_URL = os.getenv("BASE_URL", "http://localhost:5000")

# --- Database Connection ---
def get_db():
    return psycopg2.connect(os.getenv("DATABASE_URL"), cursor_factory=RealDictCursor)

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS links (
            id        SERIAL PRIMARY KEY,
            code      VARCHAR(10) UNIQUE NOT NULL,
            url       TEXT NOT NULL,
            clicks    INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    cur.close()
    conn.close()

# --- Helper ---
def generate_code(length=6):
    chars = string.ascii_letters + string.digits
    return "".join(random.choices(chars, k=length))

# --- Routes ---

# 1. Shorten URL
@app.route("/shorten", methods=["POST"])
def shorten():
    data     = request.get_json()
    url      = data.get("url")
    custom   = data.get("custom_code", "").strip().lower()

    if not url or not url.startswith(("http://", "https://")):
        return jsonify({"error": "URL tidak valid. Harus diawali http:// atau https://"}), 400

    conn = get_db()
    cur  = conn.cursor()

    # Validasi custom code
    if custom:
        if len(custom) < 3:
            return jsonify({"error": "Custom code minimal 3 karakter."}), 400
        if not custom.replace("-", "").replace("_", "").isalnum():
            return jsonify({"error": "Custom code hanya boleh huruf, angka, - dan _"}), 400

        cur.execute("SELECT id FROM links WHERE code = %s", (custom,))
        if cur.fetchone():
            cur.close()
            conn.close()
            return jsonify({"error": f"Code '/{custom}' sudah dipakai, coba yang lain!"}), 409

        code = custom

    else:
        # Cek apakah URL sudah pernah di-shorten
        cur.execute("SELECT code FROM links WHERE url = %s AND custom = FALSE", (url,))
        existing = cur.fetchone()
        if existing:
            cur.close()
            conn.close()
            return jsonify({
                "short_url": f"{BASE_URL}/{existing['code']}",
                "code": existing["code"],
                "message": "URL ini sudah pernah di-shorten!"
            })

        # Generate code otomatis
        while True:
            code = generate_code()
            cur.execute("SELECT id FROM links WHERE code = %s", (code,))
            if not cur.fetchone():
                break

    is_custom = bool(custom)
    cur.execute(
        "INSERT INTO links (code, url, custom) VALUES (%s, %s, %s) RETURNING *",
        (code, url, is_custom)
    )
    link = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({
        "short_url": f"{BASE_URL}/{link['code']}",
        "code": link["code"],
        "original_url": link["url"],
        "custom": link["custom"],
        "created_at": str(link["created_at"])
    }), 201

# 2. Redirect ke URL asli
@app.route("/<code>")
def redirect_url(code):
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("SELECT url FROM links WHERE code = %s", (code,))
    link = cur.fetchone()

    if not link:
        cur.close()
        conn.close()
        abort(404)

    # Catat klik
    cur.execute("UPDATE links SET clicks = clicks + 1 WHERE code = %s", (code,))
    cur.execute(
        "INSERT INTO click_logs (code, referrer, user_agent) VALUES (%s, %s, %s)",
        (code, request.referrer, request.user_agent.string)
    )
    conn.commit()
    cur.close()
    conn.close()

    return redirect(link["url"])

# 3. Lihat stats sebuah link
@app.route("/stats/<code>")
def stats(code):
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM links WHERE code = %s", (code,))
    link = cur.fetchone()
    cur.close()
    conn.close()

    if not link:
        return jsonify({"error": "Link tidak ditemukan"}), 404

    return jsonify({
        "code": link["code"],
        "original_url": link["url"],
        "short_url": f"{BASE_URL}/{link['code']}",
        "clicks": link["clicks"],
        "created_at": str(link["created_at"])
    })

# 4. Lihat semua link
@app.route("/links")
def all_links():
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM links ORDER BY created_at DESC")
    links = cur.fetchall()
    cur.close()
    conn.close()

    return jsonify([{
        "code": l["code"],
        "short_url": f"{BASE_URL}/{l['code']}",
        "original_url": l["url"],
        "clicks": l["clicks"],
        "created_at": str(l["created_at"])
    } for l in links])

# 5. Delete link
@app.route("/delete/<code>", methods=["DELETE"])
def delete_link(code):
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("DELETE FROM links WHERE code = %s RETURNING code", (code,))
    deleted = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    if not deleted:
        return jsonify({"error": "Link tidak ditemukan"}), 404

    return jsonify({"message": f"Link /{code} berhasil dihapus!"})

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/dashboard/data")
def dashboard_data():
    conn = get_db()
    cur  = conn.cursor()

    # Total links & clicks
    cur.execute("SELECT COUNT(*) AS total_links, COALESCE(SUM(clicks), 0) AS total_clicks FROM links")
    summary = cur.fetchone()

    # Top 5 link terbanyak diklik
    cur.execute("""
        SELECT code, url, clicks FROM links
        ORDER BY clicks DESC LIMIT 5
    """)
    top_links = list(cur.fetchall())

    # Klik per hari (7 hari terakhir)
    cur.execute("""
        SELECT DATE(clicked_at) AS day, COUNT(*) AS total
        FROM click_logs
        WHERE clicked_at >= NOW() - INTERVAL '7 days'
        GROUP BY day ORDER BY day
    """)
    clicks_per_day = list(cur.fetchall())

    # Klik per jam hari ini
    cur.execute("""
        SELECT EXTRACT(HOUR FROM clicked_at) AS hour, COUNT(*) AS total
        FROM click_logs
        WHERE DATE(clicked_at) = CURRENT_DATE
        GROUP BY hour ORDER BY hour
    """)
    clicks_per_hour = list(cur.fetchall())

    cur.close()
    conn.close()

    return jsonify({
        "total_links":     summary["total_links"],
        "total_clicks":    summary["total_clicks"],
        "top_links":       [dict(r) for r in top_links],
        "clicks_per_day":  [{"day": str(r["day"]), "total": r["total"]} for r in clicks_per_day],
        "clicks_per_hour": [{"hour": int(r["hour"]), "total": r["total"]} for r in clicks_per_hour],
    })

@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")

# --- Run ---
if __name__ == "__main__":
    init_db()
    app.run(debug=True)