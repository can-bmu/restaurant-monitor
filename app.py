import os
import re
import time
import threading
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from flask import Flask, jsonify, render_template_string

# ================== CONFIG ==================

RESTAURANTS = {
    "Bolt": [
        {"name": "Burgers Militari", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/53203"},
        {"name": "Smash Militari", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/157022-smash-gorilla/info"},
        {"name": "Burgers Olteniței", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/81061-gorilla's-crazy-burgers-berceni"},
        {"name": "Smash Olteniței", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/156512"},
        {"name": "Smash Moșilor", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/157033-smash-gorilla"},
        {"name": "Burgers Moșilor", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/69192-gorilla's-crazy-burgers-mosilor"},
        {"name": "Burgers Pipera", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/122872-gorilla's-crazy-burgers-pipera"},
        {"name": "Smash Pipera", "url": "https://food.bolt.eu/en-US/325-bucharest/p/157013-smash-gorilla/?utm_content=menu_header&utm_medium=product&utm_source=share_provider"},
        {"name": "Tacos Olteniței", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/130672-gorilla's-crazy-tacos"},
    ],
    "Wolt": [
        {"name": "Burgers Militari", "url": "https://wolt.com/en/rou/bucharest/restaurant/gorillas-crazy-burgers-gorjului-67dc3f47b93a5300e8efd705"},
        {"name": "Smash Militari", "url": "https://wolt.com/ro/rou/bucharest/restaurant/smash-gorilla-gorjului-6880a63946c4278a97069f59?srsltid=AfmBOorhoNaf1Q_3cirLld_oYSAo3uQ9JW13C2p6h8fgVASdkaVwbQwx"},
        {"name": "Burgers Olteniței", "url": "https://wolt.com/ro/rou/bucharest/restaurant/gorillas-crazy-burgers-oltenitei-67e189430bd3fc375bb3acc8"},
        {"name": "Smash Olteniței", "url": "https://wolt.com/ro/rou/bucharest/restaurant/smash-gorilla-berceni-6880a32754547abea1869cec?srsltid=AfmBOoqxe8amoCAhqB15o152PGNXULHnM_upiReSTCQyz_URAFREGZGh"},
        {"name": "Smash Moșilor", "url": "https://wolt.com/en/rou/bucharest/restaurant/smash-gorilla-mosilor-6880a63946c4278a97069f5a?srsltid=AfmBOor0fwOZtC1D6-22cz_hdap9fSgC3E4oqdqD7OonR2i6o5nl6jEi"},
        {"name": "Burgers Moșilor", "url": "https://wolt.com/en/rou/bucharest/restaurant/gorillas-crazy-burgers-mosilor-67dc3f47b93a5300e8efd706?srsltid=AfmBOop0XnSmPfKUhYX81w9mNUfK1ZtUVJuyeqe4mNV7LDwJDT9oYzGW"},
        {"name": "Burgers Pipera", "url": "https://wolt.com/ro/rou/bucharest/restaurant/gorillas-crazy-burgers-pipera-67e189430bd3fc375bb3acc9"},
        {"name": "Smash Pipera", "url": "https://wolt.com/en/rou/bucharest/restaurant/smash-gorilla-pipera-6880a32754547abea1869ced?srsltid=AfmBOooNCNAfypM0Ry_jGEj2R4bPId3Ac78LKm282Ae8NdaOPt9_qKOt"},
        {"name": "Tacos Olteniței", "url": "https://wolt.com/en/rou/bucharest/restaurant/gorillas-crazy-tacos-berceni-67db0092e014794baf59070a?srsltid=AfmBOoqFDIClWfds-9WDwfnTe2y7RnwG6KYFsKexRwaZJlbHefkeHBzc"},
    ]
}

CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36",
    "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7",
}

# ================== APP STATE ==================

STATUS = {
    # "platform|url": {
    #   "platform": "...",
    #   "url": "...",
    #   "status": "OPEN/CLOSED/UNKNOWN/ERROR",
    #   "checked_at": "YYYY-mm-dd HH:MM:SS",
    #   "details": "text scurt"
    # }
}
LAST_FULL_CHECK = None

app = Flask(__name__)

# ================== LOGIC ==================

def classify_status(text: str) -> str:
    t = text.lower()
    # simple heuristics
    if re.search(r"\b(închis|inchis|closed|temporarily\s*closed)\b", t):
        return "CLOSED"
    if re.search(r"\b(deschis|open|open\s*until)\b", t):
        return "OPEN"
    return "UNKNOWN"

def fetch_status(url: str) -> tuple[str, str]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        return classify_status(text), "ok"
    except Exception as e:
        return "ERROR", str(e)[:120]

def check_all():
    global LAST_FULL_CHECK
    for platform, urls in RESTAURANTS.items():
        for url in urls:
            st, details = fetch_status(url)
            key = f"{platform}|{url}"
            STATUS[key] = {
                "platform": platform,
                "url": url,
                "status": st,
                "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "details": details,
            }
            time.sleep(0.6)  # fii prietenos cu site-urile
    LAST_FULL_CHECK = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def worker_loop():
    # rulare continuă în fundal
    while True:
        try:
            check_all()
        except Exception:
            pass
        time.sleep(CHECK_INTERVAL_SECONDS)

# pornește workerul când pornește aplicația
threading.Thread(target=worker_loop, daemon=True).start()

# ================== ROUTES ==================

TEMPLATE = """
<!doctype html>
<html lang="ro">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Status restaurante – Monitor</title>
<style>
  :root{color-scheme: light dark;}
  body{font-family: system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; margin:24px;}
  h1{font-size:1.6rem;margin:0 0 8px;}
  .meta{color:gray;margin-bottom:16px;}
  .grid{display:grid;grid-template-columns:1fr;gap:12px;max-width:960px}
  @media(min-width:700px){.grid{grid-template-columns:1fr 1fr}}
  .card{border:1px solid #ddd;border-radius:14px;padding:16px;box-shadow:0 1px 4px rgba(0,0,0,.06);}
  .row{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
  .url{word-break:break-all;font-size:.9rem;margin-top:6px}
  .badge{padding:4px 10px;border-radius:999px;font-weight:600;font-size:.85rem}
  .OPEN{background:#e6f4ea;color:#137333}
  .CLOSED{background:#fde7e9;color:#b3261e}
  .UNKNOWN{background:#eee;color:#444}
  .ERROR{background:#ffe9cc;color:#8a4b00}
  footer{margin-top:20px;color:gray;font-size:.9rem}
</style>
<meta http-equiv="refresh" content="30">
</head>
<body>
  <h1>📊 Status restaurante (Wolt / Glovo / Bolt)</h1>
  <div class="meta">Ultima verificare completă: <b>{{ last }}</b> • Auto-refresh la 30s • Interval verificare: {{ interval }}s</div>
  <div class="grid">
    {% for item in items %}
      <div class="card">
        <div class="row">
          <div><b>{{ item.platform }}</b></div>
          <div class="badge {{ item.status }}">{{ item.status }}</div>
        </div>
        <div class="url">{{ item.url }}</div>
        <div style="margin-top:8px;color:gray;font-size:.85rem">Verificat la {{ item.checked_at }} • {{ item.details }}</div>
      </div>
    {% endfor %}
  </div>
  <footer>
    API: <a href="/api/status">/api/status</a>
  </footer>
</body>
</html>
"""

@app.route("/")
def index():
    items = list(STATUS.values())
    items.sort(key=lambda x: (x["platform"], x["url"]))
    return render_template_string(
        TEMPLATE,
        items=items,
        last=LAST_FULL_CHECK or "în curs…",
        interval=CHECK_INTERVAL_SECONDS
    )

@app.route("/api/status")
def api_status():
    return jsonify({
        "last_full_check": LAST_FULL_CHECK,
        "interval_seconds": CHECK_INTERVAL_SECONDS,
        "items": list(STATUS.values())
    })

# Render/Gunicorn folosește app:app
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
