import os
import re
import uuid
import time
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, jsonify, Response

# ────────────────────────────────────────────────────────────────────────────────
# Config
# ────────────────────────────────────────────────────────────────────────────────
VERSION = "v0.4.1"
TZ = ZoneInfo("Europe/Bucharest")

CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL_SEC", "60"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "6"))
REQ_TIMEOUT = int(os.getenv("REQ_TIMEOUT", "10"))

WOLT_LAT = float(os.getenv("WOLT_LAT", "44.4268"))   # București
WOLT_LON = float(os.getenv("WOLT_LON", "26.1025"))

ASSUME_CLOSED_WHEN_UNCERTAIN_BOLT = os.getenv(
    "ASSUME_CLOSED_WHEN_UNCERTAIN_BOLT", "false"
).lower() in ("1", "true", "yes")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8",
}

# ────────────────────────────────────────────────────────────────────────────────
# Date restaurante
# ────────────────────────────────────────────────────────────────────────────────
RESTAURANTS = [
    # BOLT
    {"platform": "Bolt", "location": "Burgers Militari", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/53203"},
    {"platform": "Bolt", "location": "Smash Militari", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/157022-smash-gorilla/info"},
    {"platform": "Bolt", "location": "Burgers Olteniței", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/81061-gorilla's-crazy-burgers-berceni"},
    {"platform": "Bolt", "location": "Smash Olteniței", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/156512"},
    {"platform": "Bolt", "location": "Smash Moșilor", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/157033-smash-gorilla"},
    {"platform": "Bolt", "location": "Burgers Moșilor", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/69192-gorilla's-crazy-burgers-mosilor"},
    {"platform": "Bolt", "location": "Burgers Pipera", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/122872-gorilla's-crazy-burgers-pipera"},
    {"platform": "Bolt", "location": "Smash Pipera", "url": "https://food.bolt.eu/en-US/325-bucharest/p/157013-smash-gorilla/?utm_content=menu_header&utm_medium=product&utm_source=share_provider"},
    {"platform": "Bolt", "location": "Tacos Olteniței", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/130672-gorilla's-crazy-tacos"},

    # WOLT
    {"platform": "Wolt", "location": "Burgers Militari", "url": "https://wolt.com/ro/rou/bucharest/restaurant/gorillas-crazy-burgers-gorjului-67dc3f47b93a5300e8efd705"},
    {"platform": "Wolt", "location": "Smash Militari", "url": "https://wolt.com/ro/rou/bucharest/restaurant/smash-gorilla-gorjului-6880a63946c4278a97069f59"},
    {"platform": "Wolt", "location": "Burgers Olteniței", "url": "https://wolt.com/ro/rou/bucharest/restaurant/gorillas-crazy-burgers-oltenitei-67e189430bd3fc375bb3acc8"},
    {"platform": "Wolt", "location": "Smash Olteniței", "url": "https://wolt.com/ro/rou/bucharest/restaurant/smash-gorilla-berceni-6880a32754547abea1869cec"},
    {"platform": "Wolt", "location": "Smash Moșilor", "url": "https://wolt.com/ro/rou/bucharest/restaurant/smash-gorilla-mosilor-6880a63946c4278a97069f5a"},
    {"platform": "Wolt", "location": "Burgers Moșilor", "url": "https://wolt.com/ro/rou/bucharest/restaurant/gorillas-crazy-burgers-mosilor-67dc3f47b93a5300e8efd706"},
    {"platform": "Wolt", "location": "Burgers Pipera", "url": "https://wolt.com/ro/rou/bucharest/restaurant/gorillas-crazy-burgers-pipera-67e189430bd3fc375bb3acc9"},
    {"platform": "Wolt", "location": "Smash Pipera", "url": "https://wolt.com/ro/rou/bucharest/restaurant/smash-gorilla-pipera-6880a32754547abea1869ced"},
    {"platform": "Wolt", "location": "Tacos Olteniței", "url": "https://wolt.com/ro/rou/bucharest/restaurant/gorillas-crazy-tacos-berceni-67db0092e014794baf59070a"},
]

# ────────────────────────────────────────────────────────────────────────────────
# Utilitare
# ────────────────────────────────────────────────────────────────────────────────
def now_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

def brand_of(location: str) -> str:
    l = location.lower()
    if "taco" in l:  return "Tacos"
    if "smash" in l: return "Smash"
    return "Burgers"

def sort_key(item: dict):
    BRAND_ORDER = {"Burgers": 1, "Smash": 2, "Tacos": 3}
    LOC_ORDER = {"militari": 1, "olteni": 2, "olteniț": 2, "mosilor": 3, "moșilor": 3, "pipera": 4}
    b = BRAND_ORDER.get(brand_of(item["location"]), 99)
    loc_score = 99
    for k, v in LOC_ORDER.items():
        if k in item["location"].lower():
            loc_score = v
            break
    return (item["platform"] != "Bolt", b, loc_score, item["location"])

# ────────────────────────────────────────────────────────────────────────────────
# Bolt API
# ────────────────────────────────────────────────────────────────────────────────
def bolt_provider_id_from_url(url: str):
    m = re.search(r"/p/(\d+)", url)
    return m.group(1) if m else None

def bolt_check_via_api(url: str):
    pid = bolt_provider_id_from_url(url)
    if not pid:
        return None
    api = "https://deliveryuser.live.boltsvc.net/deliveryClient/public/getProviderAvailabilityStatus"
    params = {
        "provider_id": pid,
        "version": "FW.1.98",
        "language": "ro-RO",
        "device_name": "web",
        "deviceType": "web",
        "deviceId": str(uuid.uuid5(uuid.NAMESPACE_URL, f"bolt:{pid}")),
    }
    try:
        r = requests.get(api, headers=HEADERS, params=params, timeout=REQ_TIMEOUT)
        if r.status_code >= 400:
            return None
        data = (r.json() or {}).get("data", {})
        flags = any([
            data.get("is_available_for_delivery"),
            data.get("is_available_for_takeaway"),
            data.get("is_available_for_schedule_delivery"),
            data.get("is_available_for_schedule_takeaway"),
        ])
        if flags:
            return "🟢 Deschis", "Bolt API: disponibil"
        overlay = (data.get("provider_overlay_text") or {}).get("value") or ""
        if "deschide la" in overlay.lower():
            return "🔴 Închis", f'Bolt API: "{overlay}"'
        return "🔴 Închis", "Bolt API: indisponibil"
    except Exception:
        return None

# ────────────────────────────────────────────────────────────────────────────────
# Wolt API (fără autentificare)
# ────────────────────────────────────────────────────────────────────────────────
def wolt_slug_from_url(url: str):
    m = re.search(r"/restaurant/([^/?#]+)", url)
    return m.group(1) if m else None

def wolt_check_via_api(url: str):
    slug = wolt_slug_from_url(url)
    if not slug:
        return None
    api = f"https://restaurant-api.wolt.com/v1/pages/venue/{slug}"
    params = {"lat": f"{WOLT_LAT:.6f}", "lon": f"{WOLT_LON:.6f}"}
    try:
        r = requests.get(api, headers=HEADERS, params=params, timeout=REQ_TIMEOUT)
        if r.status_code >= 400:
            return None
        venue = (r.json() or {}).get("page_props", {}).get("venue", {})
        state = (venue.get("venue_state") or "").upper()
        online = bool(venue.get("online"))
        # interpretare conservatoare
        if "CLOSED" in state:
            return "🔴 Închis", f"Wolt API: {state}"
        if online or "OPEN" in state:
            return "🟢 Deschis", f"Wolt API: {state or 'ONLINE'}"
        return "🟡 Nedetectabil", f"Wolt API: fără semnal clar ({state or '—'})"
    except Exception:
        return None

# ────────────────────────────────────────────────────────────────────────────────
# Motor de verificare
# ────────────────────────────────────────────────────────────────────────────────
def fetch_status_and_reason(url: str):
    if "bolt.eu" in url:
        r = bolt_check_via_api(url)
        if r:
            return r
        # fallback Bolt dacă vrei să forțezi "închis" când nu ai semnal
        if ASSUME_CLOSED_WHEN_UNCERTAIN_BOLT:
            return "🔴 Închis", "Bolt: fallback ‘assume closed’"

    if "wolt.com" in url:
        r = wolt_check_via_api(url)
        if r:
            return r

    return "🟡 Nedetectabil", "Fallback: fără semnal clar"

def check_all():
    out = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_map = {pool.submit(fetch_status_and_reason, it["url"]): it for it in RESTAURANTS}
        for fut in as_completed(future_map):
            it = future_map[fut]
            status, reason = fut.result()
            out[it["url"]] = {
                "platform": it["platform"],
                "location": it["location"],
                "brand": brand_of(it["location"]),
                "url": it["url"],
                "status": status,
                "reason": reason,
                "checked_at": now_str(),
            }
    return out

# ────────────────────────────────────────────────────────────────────────────────
# Web + UI
# ────────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)
last_results = {}
last_full_check_time = "—"

HTML = """<!doctype html>
<html lang="ro">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Status restaurante (Wolt / Bolt)</title>
  <style>
    :root {
      --bg:#0c0f10; --card:#141a1e; --muted:#a7b0b5; --txt:#eef3f5;
      --ok:#2ecc71; --bad:#e74c3c; --warn:#f1c40f; --chip:#1f2b33;
    }
    html,body { background:var(--bg); color:var(--txt); font-family: Inter, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; }
    .wrap { max-width:1100px; margin:48px auto; padding:0 16px; }
    h1 { font-size:28px; margin:0 0 6px; }
    .sub { color:var(--muted); font-size:14px; display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
    .btn { background:#1e8e5a; color:#fff; border:0; padding:10px 14px; border-radius:10px; cursor:pointer; font-weight:700; }
    .btn:disabled { opacity:.7; cursor:wait; }
    .grid { display:grid; gap:28px; margin-top:22px; }
    .card { background:var(--card); border-radius:16px; padding:0 0 8px; box-shadow: 0 8px 24px rgba(0,0,0,.25); }
    .card h2 { margin:0; padding:16px 18px; font-size:20px; border-bottom:1px solid #22313a; display:flex; align-items:center; gap:10px;}
    table { width:100%; border-collapse:collapse; }
    th, td { padding:12px 16px; border-bottom:1px solid #22313a; text-align:left; font-size:14px; vertical-align:top; }
    th { color:#bbd0da; font-weight:700; }
    .status { font-weight:700; }
    .ok { color:var(--ok); }
    .bad { color:var(--bad); }
    .warn { color:var(--warn); }
    .muted { color:var(--muted); }
    .chip { display:inline-block; background:var(--chip); color:#a7d6c2; padding:2px 8px; border-radius:999px; font-size:12px; }
    .footer { margin-top:18px; color:var(--muted); font-size:12px; }
    .version { margin-left:auto; background:#23313a; color:#9cc5b3; padding:2px 8px; border-radius:999px; font-size:12px; }
    .headerline { display:flex; gap:12px; align-items:center; }
    .note { color:var(--muted); padding: 0 18px 8px; }
    .hidden { display:none; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="headerline">
      <h1>📊 Status restaurante (Wolt / Bolt)</h1>
      <span class="version">__VERSION__</span>
    </div>
    <div class="sub">
      <span>Ultima verificare completă: <b id="last-check">în curs…</b></span>
      <span>•</span>
      <span>Auto-refresh la <b>30s</b></span>
      <span>•</span>
      <span>Interval verificare: <b>__INTERVAL__s</b></span>
      <button id="refresh" class="btn" style="margin-left:12px">Reverifică acum</button>
      <button id="toggle" class="btn" style="background:#2c3e50">Comută detalii</button>
    </div>

    <div class="grid">
      <div class="card">
        <h2>Bolt <span class="muted" style="font-weight:400;">(ordonare: Burgers → Smash → Tacos)</span></h2>
        <div class="note">Mod simplu: doar Locație + Status. Apasă „Comută detalii” pentru a arăta/ascunde „Motiv” și „Verificat la”.</div>
        <table id="bolt">
          <thead>
            <tr>
              <th>Locație</th>
              <th>Status</th>
              <th class="col-detail">Motiv</th>
              <th class="col-detail">Verificat la</th>
            </tr>
          </thead>
          <tbody></tbody>
        </table>
      </div>

      <div class="card">
        <h2>Wolt</h2>
        <table id="wolt">
          <thead>
            <tr>
              <th>Locație</th>
              <th>Status</th>
              <th class="col-detail">Motiv</th>
              <th class="col-detail">Verificat la</th>
            </tr>
          </thead>
          <tbody></tbody>
        </table>
      </div>
    </div>

    <div class="footer">
      Dacă un rând este „🟡 Nedetectabil”, cauza probabilă: API-ul nu a oferit semnal. Pentru Bolt poți seta <code>ASSUME_CLOSED_WHEN_UNCERTAIN_BOLT=true</code>.
    </div>
  </div>

<script>
const $ = (sel) => document.querySelector(sel);
let detailed = false;
let lastData = null;

function badge(cls, text) {
  return '<span class="status ' + cls + '">' + text + '</span>';
}

function rowHTML(it) {
  var cls = it.status.indexOf('🟢') === 0 ? 'ok'
          : it.status.indexOf('🔴') === 0 ? 'bad'
          : it.status.indexOf('❌') === 0 ? 'bad'
          : 'warn';

  var html = '<tr>';
  html += '<td><a href="' + it.url + '" target="_blank" rel="noreferrer">' +
          it.location + '</a> <span class="chip">' + it.brand + '</span></td>';
  html += '<td>' + badge(cls, it.status) + '</td>';
  html += '<td class="muted col-detail">' + it.reason + '</td>';
  html += '<td class="muted col-detail">' + it.checked_at + '</td>';
  html += '</tr>';
  return html;
}

function applyDetailMode() {
  document.querySelectorAll('.col-detail').forEach(function(el){
    if (detailed) el.classList.remove('hidden'); else el.classList.add('hidden');
  });
}

function render(data) {
  $("#last-check").textContent = data.last_full_check || "—";
  const filtered = (data.items || []).filter(it => detailed || it.platform !== "Test");

  const boltRows = [];
  const woltRows = [];
  filtered.forEach(function(it){
    (it.platform === "Bolt" ? boltRows : woltRows).push(rowHTML(it));
  });

  $("#bolt tbody").innerHTML = boltRows.join("") || '<tr><td colspan="4" class="muted">—</td></tr>';
  $("#wolt tbody").innerHTML = woltRows.join("") || '<tr><td colspan="4" class="muted">—</td></tr>';
  applyDetailMode();
}

async function load() {
  try {
    const r = await fetch("/api/status");
    lastData = await r.json();
    render(lastData);
  } catch(e) {
    console.error(e);
  }
}

$("#refresh").addEventListener("click", async function() {
  const btn = $("#refresh");
  btn.disabled = true;
  btn.textContent = "Se verifică…";
  try { await fetch("/api/refresh", {method:"POST"}); } catch(e) {}
  await load();
  btn.disabled = false;
  btn.textContent = "Reverifică acum";
});

$("#toggle").addEventListener("click", function(){
  detailed = !detailed;
  if (lastData) render(lastData);
});

load();
setInterval(load, 30000);
</script>
</body>
</html>
"""

@app.route("/")
def index():
    html = HTML.replace("__VERSION__", VERSION).replace("__INTERVAL__", str(CHECK_INTERVAL_SEC))
    return Response(html, mimetype="text/html")

@app.route("/api/status")
def api_status():
    items = []
    for it in sorted(RESTAURANTS, key=sort_key):
        r = last_results.get(it["url"])
        if r is None:
            items.append({
                "platform": it["platform"],
                "location": it["location"],
                "brand": brand_of(it["location"]),
                "url": it["url"],
                "status": "🟡 Nedetectabil",
                "reason": "Încă nu s-a verificat",
                "checked_at": "—",
            })
        else:
            items.append(r)
    return jsonify({
        "version": VERSION,
        "last_full_check": last_full_check_time,
        "interval_seconds": CHECK_INTERVAL_SEC,
        "items": items,
    })

@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    global last_results, last_full_check_time
    last_results = check_all()
    last_full_check_time = now_str()
    return jsonify({"ok": True, "refreshed_at": last_full_check_time})

# ────────────────────────────────────────────────────────────────────────────────
# Pornire în fundal
# ────────────────────────────────────────────────────────────────────────────────
def background_loop():
    global last_results, last_full_check_time
    # prim ciclu imediat ca să ai date la deschidere
    last_results = check_all()
    last_full_check_time = now_str()
    while True:
        time.sleep(CHECK_INTERVAL_SEC)
        last_results = check_all()
        last_full_check_time = now_str()

import threading
threading.Thread(target=background_loop, daemon=True).start()

if __name__ == "__main__":
    # requirements.txt: flask>=3.0 requests>=2.31 gunicorn>=21.2
    # (Wolt nu cere BS4 acum, dar poți lăsa pachetul)
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
