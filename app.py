import os
import threading
import time
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

# ------------------ CONFIG ------------------
RESTAURANTS = {
    "Bolt": [
        {"name": "Burgers Militari",  "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/53203"},
        {"name": "Smash Militari",    "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/157022-smash-gorilla/info"},
        {"name": "Burgers Olteniței", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/81061-gorilla's-crazy-burgers-berceni"},
        {"name": "Smash Olteniței",   "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/156512"},
        {"name": "Smash Moșilor",     "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/157033-smash-gorilla"},
        {"name": "Burgers Moșilor",   "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/69192-gorilla's-crazy-burgers-mosilor"},
        {"name": "Burgers Pipera",    "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/122872-gorilla's-crazy-burgers-pipera"},
        {"name": "Smash Pipera",      "url": "https://food.bolt.eu/en-US/325-bucharest/p/157013-smash-gorilla/?utm_content=menu_header&utm_medium=product&utm_source=share_provider"},
        {"name": "Tacos Olteniței",   "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/130672-gorilla's-crazy-tacos"},
    ],
    "Wolt": [
        {"name": "Burgers Militari",  "url": "https://wolt.com/en/rou/bucharest/restaurant/gorillas-crazy-burgers-gorjului-67dc3f47b93a5300e8efd705"},
        {"name": "Smash Militari",    "url": "https://wolt.com/ro/rou/bucharest/restaurant/smash-gorilla-gorjului-6880a63946c4278a97069f59?srsltid=AfmBOorhoNaf1Q_3cirLld_oYSAo3uQ9JW13C2p6h8fgVASdkaVwbQwx"},
        {"name": "Burgers Olteniței", "url": "https://wolt.com/ro/rou/bucharest/restaurant/gorillas-crazy-burgers-oltenitei-67e189430bd3fc375bb3acc8"},
        {"name": "Smash Olteniței",   "url": "https://wolt.com/ro/rou/bucharest/restaurant/smash-gorilla-berceni-6880a32754547abea1869cec?srsltid=AfmBOoqxe8amoCAhqB15o152PGNXULHnM_upiReSTCQyz_URAFREGZGh"},
        {"name": "Smash Moșilor",     "url": "https://wolt.com/en/rou/bucharest/restaurant/smash-gorilla-mosilor-6880a63946c4278a97069f5a?srsltid=AfmBOor0fwOZtC1D6-22cz_hdap9fSgC3E4oqdqD7OonR2i6o5nl6jEi"},
        {"name": "Burgers Moșilor",   "url": "https://wolt.com/en/rou/bucharest/restaurant/gorillas-crazy-burgers-mosilor-67dc3f47b93a5300e8efd706?srsltid=AfmBOop0XnSmPfKUhYX81w9mNUfK1ZtUVJuyeqe4mNV7LDwJDT9oYzGW"},
        {"name": "Burgers Pipera",    "url": "https://wolt.com/ro/rou/bucharest/restaurant/gorillas-crazy-burgers-pipera-67e189430bd3fc375bb3acc9"},
        {"name": "Smash Pipera",      "url": "https://wolt.com/en/rou/bucharest/restaurant/smash-gorilla-pipera-6880a32754547abea1869ced?srsltid=AfmBOooNCNAfypM0Ry_jGEj2R4bPId3Ac78LKm282Ae8NdaOPt9_qKOt"},
        {"name": "Tacos Olteniței",   "url": "https://wolt.com/en/rou/bucharest/restaurant/gorillas-crazy-tacos-berceni-67db0092e014794baf59070a?srsltid=AfmBOoqFDIClWfds-9WDwfnTe2y7RnwG6KYFsKexRwaZJlbHefkeHBzc"},
    ]
}

CHECK_INTERVAL = 60
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7",
}

STATUS = {}
LAST_CHECK = None

# ------------------ DETECTION ------------------
OPEN_PATTERNS = [
    r'"is_open"\s*:\s*true',
    r'"open"\s*:\s*true',
    r'"availabilityStatus"\s*:\s*"open"',
    r'\bdeschis\b', r'\bopen\b',
]
CLOSED_PATTERNS = [
    r'"is_open"\s*:\s*false',
    r'"closed"\s*:\s*true',
    r'"open"\s*:\s*false',
    r'"availabilityStatus"\s*:\s*"closed"',
    r'\bînchis\b', r'\binchis\b', r'\bclosed\b', r'temporarily\s*closed',
]

def classify_from_html(url: str, html: str) -> str:
    """Heuristici combinate pentru a decide OPEN/CLOSED/NONE."""
    t = html.lower()

    # platform-specific quick hints
    if "bolt.eu" in url:
        # uneori apare in json "availabilityStatus"
        if re.search(r'"availabilitystatus"\s*:\s*"closed"', t):
            return "🔴 Închis"
        if re.search(r'"availabilitystatus"\s*:\s*"open"', t):
            return "🟢 Deschis"

    if "wolt.com" in url:
        if re.search(r'"is_open"\s*:\s*false', t):
            return "🔴 Închis"
        if re.search(r'"is_open"\s*:\s*true', t):
            return "🟢 Deschis"

    # generic patterns
    for pat in CLOSED_PATTERNS:
        if re.search(pat, t):
            return "🔴 Închis"
    for pat in OPEN_PATTERNS:
        if re.search(pat, t):
            return "🟢 Deschis"

    return "🟡 Nedetectabil"

def fetch_status(url: str) -> str:
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code >= 400:
            return f"🔴 Închis ({r.status_code})"
        return classify_from_html(url, r.text)
    except Exception as e:
        return f"❌ Eroare: {str(e)[:90]}"

def check_once():
    """Verifică toate restaurantele, actualizează STATUS & LAST_CHECK."""
    global STATUS, LAST_CHECK
    print("[monitor] start check...", flush=True)
    new = {}
    for platform, items in RESTAURANTS.items():
        lst = []
        for it in items:
            st = fetch_status(it["url"])
            lst.append({
                "name": it["name"],
                "url": it["url"],
                "status": st,
                "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
            time.sleep(0.5)  # politicos cu site-urile
        new[platform] = lst
    STATUS = new
    LAST_CHECK = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("[monitor] done at", LAST_CHECK, flush=True)

def background_loop():
    while True:
        check_once()
        time.sleep(CHECK_INTERVAL)

# rulează o verificare la import + loop periodic în background
threading.Thread(target=check_once, daemon=True).start()
threading.Thread(target=background_loop, daemon=True).start()

# ------------------ UI ------------------
TEMPLATE = """
<!doctype html>
<html lang="ro">
<head>
<meta charset="utf-8">
<title>📊 Status restaurante (Wolt / Bolt)</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30">
<style>
  :root{color-scheme:dark light;}
  body{background:#101214;color:#e6e6e6;font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:28px;text-align:center}
  h1{margin:0 0 6px}
  .meta{color:#9aa0a6;margin-bottom:14px}
  .wrap{max-width:1100px;margin:0 auto}
  table{width:100%;border-collapse:collapse;margin:16px 0}
  th,td{border:1px solid #2b2f36;padding:10px}
  th{background:#171a1f}
  a{color:#8ab4f8;text-decoration:none}
  .ok{color:#34a853;font-weight:700}
  .bad{color:#ea4335;font-weight:700}
  .unk{color:#9aa0a6;font-weight:700}
  .btn{display:inline-block;padding:8px 14px;border-radius:10px;border:1px solid #2b2f36;margin-top:6px;cursor:pointer}
</style>
<script>
async function refreshNow(btn){
  btn.disabled = true; btn.innerText = 'Se verifică...';
  try{
    await fetch('/refresh', {method:'POST'});
  }catch(e){}
  btn.innerText = 'Reverifică acum';
  btn.disabled = false;
  location.reload();
}
</script>
</head>
<body>
<div class="wrap">
  <h1>📊 Status restaurante (Wolt / Bolt)</h1>
  <div class="meta">Ultima verificare completă: <b>{{ last or "în curs…" }}</b> • Auto-refresh 30s • Interval verificare: {{ interval }}s</div>
  <button class="btn" onclick="refreshNow(this)">Reverifică acum</button>

  {% for platform, rows in status.items() %}
    <h2 style="margin-top:22px">{{ platform }}</h2>
    <table>
      <tr><th>Locație</th><th>Status</th><th>Verificat la</th></tr>
      {% for r in rows %}
        {% set cls = 'ok' if '🟢' in r.status else ('bad' if '🔴' in r.status else 'unk') %}
        <tr>
          <td style="text-align:left"><a href="{{ r.url }}" target="_blank">{{ r.name }}</a></td>
          <td class="{{ cls }}">{{ r.status }}</td>
          <td>{{ r.checked_at }}</td>
        </tr>
      {% endfor %}
    </table>
  {% endfor %}
</div>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(TEMPLATE, status=STATUS, last=LAST_CHECK, interval=CHECK_INTERVAL)

@app.route("/api/status")
def api_status():
    return jsonify({"last_check": LAST_CHECK, "interval": CHECK_INTERVAL, "status": STATUS})

@app.route("/refresh", methods=["POST"])
def refresh():
    threading.Thread(target=check_once, daemon=True).start()
    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
