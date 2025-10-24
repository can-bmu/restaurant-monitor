import os
import threading
import time
import re
import html as html_lib
import unicodedata
from datetime import datetime

import requests
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

# ================== META / VERSION ==================
# Schimbă aici când faci un „release”.
# Ex.: "v0.1 beta", apoi "v0.1.1", "v0.1.2" etc.
VERSION = os.getenv("APP_VERSION", "v0.1.1")

# ================== CONFIG ==================
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

# ================== SORTING (burgers -> smash -> tacos) ==================
def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()

def _category_index(name: str) -> int:
    n = _norm(name)
    if "burger" in n:
        return 0
    if "smash" in n:
        return 1
    if "taco" in n:
        return 2
    return 3

def _sorted_items(items):
    return sorted(items, key=lambda it: (_category_index(it["name"]), _norm(it["name"])))


# ================== DETECTION (with reasons) ==================
def _normalize_html_text(s: str) -> tuple[str, str]:
    s = html_lib.unescape(s).lower().replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s)
    s_ascii = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return s, s_ascii

def classify_with_reason(url: str, html: str) -> tuple[str, str]:
    """
    Returnează (status_emoji+text, motiv).
    Status poate fi: '🔴 Închis', '🟢 Deschis', '🟡 Nedetectabil', '❌ Eroare: ...'
    """
    t, t_ascii = _normalize_html_text(html)

    # semnale generice de "închis"
    closed_signals = [
        (r'"availabilitystatus"\s*:\s*"closed"', "Bolt JSON availabilityStatus=closed"),
        (r"\binchis temporar\b", "Bolt UI: „Închis temporar”"),
        (r"\binchis\b", "Text „Închis”"),
        (r"\btemporar(?:[^a-z]|$)", "Are cuvântul „temporar” lângă disponibilitate"),
        (r"\btemporarily closed\b", "Text „temporarily closed”"),
        (r"\bclosed\b", "Text „closed”"),
    ]
    opens_at_signals = [
        (r"deschide la \d{1,2}[:.]\d{2}", "Bolt UI: „Deschide la HH:MM”"),
        (r"opens at \d{1,2}[:.]\d{2}", "Text „Opens at HH:MM”"),
    ]

    # ---- BOLT: closed-first ----
    if "bolt.eu" in url:
        for pat, why in closed_signals:
            if re.search(pat, t) or re.search(pat, t_ascii):
                return "🔴 Închis", why
        for pat, why in opens_at_signals:
            if re.search(pat, t) or re.search(pat, t_ascii):
                return "🔴 Închis", why
        return "🟡 Nedetectabil", "Bolt: niciun semnal clar (nici closed, nici opens-at)"

    # ---- WOLT ----
    if "wolt.com" in url:
        if re.search(r'"is_open"\s*:\s*false', t):
            return "🔴 Închis", "Wolt JSON is_open=false"
        if re.search(r'"is_open"\s*:\s*true', t):
            return "🟢 Deschis", "Wolt JSON is_open=true"
        if re.search(r"\binchis\b", t) or re.search(r"\bclosed\b", t):
            return "🔴 Închis", "Wolt text vizibil conține „închis/closed”"
        return "🟡 Nedetectabil", "Wolt: niciun semnal clar (is_open absent)"

    # ---- fallback pentru alte site-uri (nu ar trebui să ajungem aici) ----
    if re.search(r"\bclosed\b", t) or re.search(r"\binchis\b", t):
        return "🔴 Închis", "Text generic conține „closed/închis”"
    if re.search(r'\bopen now\b', t) or re.search(r'\bdeschis acum\b', t):
        return "🟢 Deschis", "Text generic conține „open now/deschis acum”"
    return "🟡 Nedetectabil", "Fără semnale în HTML"

def fetch_status_and_reason(url: str) -> tuple[str, str]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code >= 400:
            return f"🔴 Închis ({r.status_code})", f"HTTP status {r.status_code}"
        return classify_with_reason(url, r.text)
    except Exception as e:
        msg = str(e)[:140]
        return f"❌ Eroare", f"Eroare rețea: {msg}"

def check_once():
    global STATUS, LAST_CHECK
    print("[monitor] start check...", flush=True)
    new = {}
    for platform, items in RESTAURANTS.items():
        lst = []
        for it in _sorted_items(items):
            st, why = fetch_status_and_reason(it["url"])
            lst.append({
                "name": it["name"],
                "url": it["url"],
                "status": st,
                "reason": why,
                "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
            time.sleep(0.5)
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

# ================== UI ==================
TEMPLATE = """
<!doctype html>
<html lang="ro">
<head>
<meta charset="utf-8">
<title>📊 Status restaurante</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30">
<style>
  :root{color-scheme:dark light;}
  body{background:#101214;color:#e6e6e6;font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:28px;}
  h1{margin:0 0 6px}
  .meta{color:#9aa0a6;margin-bottom:14px}
  .wrap{max-width:1200px;margin:0 auto}
  .topbar{display:flex;gap:12px;align-items:center;justify-content:space-between}
  .version{font-size:12px;color:#9aa0a6;border:1px solid #2b2f36;padding:4px 8px;border-radius:8px}
  table{width:100%;border-collapse:collapse;margin:16px 0}
  th,td{border:1px solid #2b2f36;padding:10px;vertical-align:top}
  th{background:#171a1f}
  a{color:#8ab4f8;text-decoration:none}
  .ok{color:#34a853;font-weight:700}
  .bad{color:#ea4335;font-weight:700}
  .unk{color:#9aa0a6;font-weight:700}
  .btn{display:inline-block;padding:8px 14px;border-radius:10px;border:1px solid #2b2f36;margin-top:6px;cursor:pointer;background:#15181d;color:#e6e6e6}
  .reason{color:#cbd5e1;font-size:12px}
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
  <div class="topbar">
    <div>
      <h1>📊 Status restaurante (Wolt / Bolt)</h1>
      <div class="meta">Ultima verificare completă: <b>{{ last or "în curs…" }}</b> • Auto-refresh 30s • Interval verificare: {{ interval }}s</div>
      <button class="btn" onclick="refreshNow(this)">Reverifică acum</button>
    </div>
    <div class="version">Versiune: {{ version }}</div>
  </div>

  {% for platform, rows in status.items() %}
    <h2 style="margin-top:22px">{{ platform }}</h2>
    <table>
      <tr><th style="width:28%">Locație</th><th style="width:15%">Status</th><th style="width:37%">Motiv</th><th style="width:20%">Verificat la</th></tr>
      {% for r in rows %}
        {% set cls = 'ok' if '🟢' in r.status else ('bad' if '🔴' in r.status else 'unk') %}
        <tr>
          <td style="text-align:left"><a href="{{ r.url }}" target="_blank">{{ r.name }}</a></td>
          <td class="{{ cls }}">{{ r.status }}</td>
          <td class="reason">{{ r.reason }}</td>
          <td>{{ r.checked_at }}</td>
        </tr>
      {% endfor %}
    </table>
  {% endfor %}
</div>
</body>
</html>
"""

# ================== ROUTES ==================
@app.route("/")
def index():
    return render_template_string(
        TEMPLATE,
        status=STATUS,
        last=LAST_CHECK,
        interval=CHECK_INTERVAL,
        version=VERSION,
    )

@app.route("/api/status")
def api_status():
    return jsonify({"last_check": LAST_CHECK, "interval": CHECK_INTERVAL, "status": STATUS, "version": VERSION})

@app.route("/refresh", methods=["POST"])
def refresh():
    threading.Thread(target=check_once, daemon=True).start()
    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
