import os
import threading
import time
import re
import unicodedata
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
        {"name": "Smash Militari",    "url": "https://wolt.com/ro/rou/bucharest/restaurant/smash-gorilla-gorjului-6880a63946c4278a97069f59"},
        {"name": "Burgers Olteniței", "url": "https://wolt.com/ro/rou/bucharest/restaurant/gorillas-crazy-burgers-oltenitei-67e189430bd3fc375bb3acc8"},
        {"name": "Smash Olteniței",   "url": "https://wolt.com/ro/rou/bucharest/restaurant/smash-gorilla-berceni-6880a32754547abea1869cec"},
        {"name": "Smash Moșilor",     "url": "https://wolt.com/en/rou/bucharest/restaurant/smash-gorilla-mosilor-6880a63946c4278a97069f5a"},
        {"name": "Burgers Moșilor",   "url": "https://wolt.com/en/rou/bucharest/restaurant/gorillas-crazy-burgers-mosilor-67dc3f47b93a5300e8efd706"},
        {"name": "Burgers Pipera",    "url": "https://wolt.com/ro/rou/bucharest/restaurant/gorillas-crazy-burgers-pipera-67e189430bd3fc375bb3acc9"},
        {"name": "Smash Pipera",      "url": "https://wolt.com/en/rou/bucharest/restaurant/smash-gorilla-pipera-6880a32754547abea1869ced"},
        {"name": "Tacos Olteniței",   "url": "https://wolt.com/en/rou/bucharest/restaurant/gorillas-crazy-tacos-berceni-67db0092e014794baf59070a"},
    ]
}

CHECK_INTERVAL = 60
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7",
}

STATUS = {}
LAST_CHECK = None

# ------------------ SORTING (burgers -> smash -> tacos) ------------------
def _norm(s: str) -> str:
    # fără diacritice, lower
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()

def _category_index(name: str) -> int:
    n = _norm(name)
    if "burger" in n:
        return 0   # Gorilla's Crazy Burgers
    if "smash" in n:
        return 1   # Smash
    if "taco" in n:
        return 2   # Tacos
    return 3       # orice altceva (la final)

def _sorted_items(items):
    return sorted(items, key=lambda it: (_category_index(it["name"]), _norm(it["name"])))


# ------------------ DETECTION ------------------
OPEN_PATTERNS = [
    r'"is_open"\s*:\s*true',
    r'"open"\s*:\s*true',
    r'"availabilityStatus"\s*:\s*"open"',
    r'\bdeschis\b', r'\bopen\b'
]

CLOSED_PATTERNS = [
    r'"is_open"\s*:\s*false',
    r'"closed"\s*:\s*true',
    r'"open"\s*:\s*false',
    r'"availabilityStatus"\s*:\s*"closed"',
    r'\bînchis\b', r'\binchis\b',
    r'\bînchis\s+temporar\b', r'\binchis\s+temporar\b',
    r'\btemporar\b',
    r'\bdeschide\s+la\b',
    r'\bopens\s+at\b', r'\bopening\s+at\b',
    r'\bclosed\b', r'temporarily\s*closed'
]

def classify_from_html(url: str, html: str) -> str:
    """Heuristici combinate pentru a decide OPEN/CLOSED/NONE pe Bolt/Wolt."""
    t = html.lower()

    # ---- Bolt ----
    if "bolt.eu" in url:
        if re.search(r'"availabilitystatus"\s*:\s*"closed"', t):
            return "🔴 Închis"
        if re.search(r'"availabilitystatus"\s*:\s*"open"', t):
            return "🟢 Deschis"
        if "închis temporar" in t or "inchis temporar" in t:
            return "🔴 Închis"
        if re.search(r'deschide\s+la\s+\d{1,2}[:.]\d{2}', t) or re.search(r'opens\s+at\s+\d{1,2}[:.]\d{2}', t):
            return "🔴 Închis"

    # ---- Wolt ----
    if "wolt.com" in url:
        if re.search(r'"is_open"\s*:\s*false', t):
            return "🔴 Închis"
        if re.search(r'"is_open"\s*:\s*true', t):
            return "🟢 Deschis"
        if "închis" in t or "inchis" in t or "closed" in t:
            return "🔴 Închis"

    # ---- fallback generic ----
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
        for it in _sorted_items(items):   # <-- sortăm aici
            st = fetch_status(it["url"])
            lst.append({
                "name": it["name"],
                "url": it["url"],
                "status": st,
                "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
            time.sleep(0.5)  # politicos
        new[platform] = lst
    STATUS = new
    LAST_CHECK = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("[monitor] done at", LAST_CHECK, flush=True)

def background_loop():
    while True:
        check_once()
        time.sleep(CHECK_INTERVAL)

# rulează o verificare la import + loop periodic
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
  try{ await fetch('/refresh', {method:'POST'}); }catch(e){}
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
