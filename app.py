import os
import re
import html as html_lib
import unicodedata
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
from uuid import uuid4

import requests
from flask import Flask, jsonify, Response

# ────────────────────────────────────────────────────────────────────────────────
# Config
# ────────────────────────────────────────────────────────────────────────────────
VERSION = "v0.1.7 beta"
TZ = ZoneInfo("Europe/Bucharest")

CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL_SEC", "60"))
ASSUME_CLOSED_WHEN_UNCERTAIN_BOLT = os.getenv(
    "ASSUME_CLOSED_WHEN_UNCERTAIN_BOLT", "false"
).lower() in ("1", "true", "yes")

# Parametri default pentru API-ul Bolt (override prin ENV dacă vrei)
BOLT_API_VERSION  = os.getenv("BOLT_API_VERSION", "FW.1.98")
BOLT_API_LANGUAGE = os.getenv("BOLT_API_LANGUAGE", "ro-RO")
BOLT_API_COUNTRY  = os.getenv("BOLT_API_COUNTRY", "ro")

# Stabilim un deviceId/sesiune per proces (poți seta și din ENV)
DEVICE_ID  = os.getenv("BOLT_DEVICE_ID", str(uuid4()))
SESSION_ID = os.getenv("BOLT_SESSION_ID", str(uuid4()))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8",
}

# ────────────────────────────────────────────────────────────────────────────────
# Date: restaurante (Bolt + Wolt)
# ────────────────────────────────────────────────────────────────────────────────

RESTAURANTS = [
    # BOLT
    {"platform": "Bolt", "location": "Burgers Militari",   "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/53203"},
    {"platform": "Bolt", "location": "Smash Militari",     "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/157022-smash-gorilla/info"},
    {"platform": "Bolt", "location": "Burgers Olteniței",  "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/81061-gorilla's-crazy-burgers-berceni"},
    {"platform": "Bolt", "location": "Smash Olteniței",    "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/156512"},
    {"platform": "Bolt", "location": "Smash Moșilor",      "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/157033-smash-gorilla"},
    {"platform": "Bolt", "location": "Burgers Moșilor",    "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/69192-gorilla's-crazy-burgers-mosilor"},
    {"platform": "Bolt", "location": "Burgers Pipera",     "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/122872-gorilla's-crazy-burgers-pipera"},
    {"platform": "Bolt", "location": "Smash Pipera",       "url": "https://food.bolt.eu/en-US/325-bucharest/p/157013-smash-gorilla/?utm_content=menu_header&utm_medium=product&utm_source=share_provider"},
    {"platform": "Bolt", "location": "Tacos Olteniței",    "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/130672-gorilla's-crazy-tacos"},
    # Test Bolt
    {"platform": "Bolt", "location": "Test: Liquid Spirits","url": "https://food.bolt.eu/ro-RO/325-bucharest/p/126569-liquid-spirits"},

    # WOLT
    {"platform": "Wolt", "location": "Burgers Militari",   "url": "https://wolt.com/en/rou/bucharest/restaurant/gorillas-crazy-burgers-gorjului-67dc3f47b93a5300e8efd705"},
    {"platform": "Wolt", "location": "Smash Militari",     "url": "https://wolt.com/ro/rou/bucharest/restaurant/smash-gorilla-gorjului-6880a63946c4278a97069f59"},
    {"platform": "Wolt", "location": "Burgers Olteniței",  "url": "https://wolt.com/ro/rou/bucharest/restaurant/gorillas-crazy-burgers-oltenitei-67e189430bd3fc375bb3acc8"},
    {"platform": "Wolt", "location": "Smash Olteniței",    "url": "https://wolt.com/ro/rou/bucharest/restaurant/smash-gorilla-berceni-6880a32754547abea1869cec"},
    {"platform": "Wolt", "location": "Smash Moșilor",      "url": "https://wolt.com/en/rou/bucharest/restaurant/smash-gorilla-mosilor-6880a63946c4278a97069f5a"},
    {"platform": "Wolt", "location": "Burgers Moșilor",    "url": "https://wolt.com/en/rou/bucharest/restaurant/gorillas-crazy-burgers-mosilor-67dc3f47b93a5300e8efd706"},
    {"platform": "Wolt", "location": "Burgers Pipera",     "url": "https://wolt.com/ro/rou/bucharest/restaurant/gorillas-crazy-burgers-pipera-67e189430bd3fc375bb3acc9"},
    {"platform": "Wolt", "location": "Smash Pipera",       "url": "https://wolt.com/en/rou/bucharest/restaurant/smash-gorilla-pipera-6880a32754547abea1869ced"},
    {"platform": "Wolt", "location": "Tacos Olteniței",    "url": "https://wolt.com/en/rou/bucharest/restaurant/gorillas-crazy-tacos-berceni-67db0092e014794baf59070a"},
    # Test Wolt
    {"platform": "Wolt", "location": "Test: Shaormeria CA","url": "https://wolt.com/ro/rou/bucharest/restaurant/shaormeria-ca-67dc3efb2e58c74a8f3511df"},
]

# ────────────────────────────────────────────────────────────────────────────────
# Utilități
# ────────────────────────────────────────────────────────────────────────────────

def now_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

def brand_of(location: str) -> str:
    l = location.lower()
    if "taco" in l:
        return "Tacos"
    if "smash" in l:
        return "Smash"
    return "Burgers"

BRAND_ORDER = {"Burgers": 1, "Smash": 2, "Tacos": 3}
LOC_ORDER = {"militari": 1, "olteni": 2, "olteniț": 2, "mosilor": 3, "moșilor": 3, "pipera": 4}

def sort_key(item: dict):
    b = BRAND_ORDER.get(brand_of(item["location"]), 99)
    loc = item["location"].lower()
    loc_score = 99
    for k, v in LOC_ORDER.items():
        if k in loc:
            loc_score = v
            break
    return (item["platform"] != "Bolt", b, loc_score, item["location"])

def _normalize_html_text(s: str) -> tuple[str, str]:
    s = html_lib.unescape(s).lower().replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s)
    s_ascii = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return s, s_ascii

def _extract_availability_info_block(t: str) -> str | None:
    m = re.search(
        r'data-testid="screens\.Provider\.MenuHeader\.availabilityInfo"[^>]*>(.*?)</div>',
        t, flags=re.DOTALL,
    )
    if not m:
        return None
    frag = m.group(1)
    frag = re.sub(r"<[^>]+>", " ", frag)
    frag = html_lib.unescape(frag).lower().replace("\u00a0", " ")
    frag = re.sub(r"\s+", " ", frag)
    return frag

# ────────────────────────────────────────────────────────────────────────────────
# Bolt API helpers
# ────────────────────────────────────────────────────────────────────────────────

def bolt_provider_id_from_url(url: str) -> str | None:
    m = re.search(r"/p/(\d+)(?:-[^/]*)?$", url, flags=re.IGNORECASE)
    return m.group(1) if m else None

def deep_find_key_ci(obj, key_candidates):
    """Caută recursiv prima valoare a unei chei (case-insensitive) în dict/list."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in key_candidates:
                return v
            found = deep_find_key_ci(v, key_candidates)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for e in obj:
            found = deep_find_key_ci(e, key_candidates)
            if found is not None:
                return found
    return None

def bolt_api_status_for_url(url: str) -> tuple[str | None, str]:
    """Întoarce (status, motiv) sau (None, motiv) dacă nu poate determina din API."""
    provider_id = bolt_provider_id_from_url(url)
    if not provider_id:
        return None, "Bolt API: provider_id negăsit în URL"

    api_url = "https://deliveryuser.live.boltsvc.net/deliveryClient/public/getProviderAvailabilityStatus"
    params = {
        "provider_id": provider_id,
        "version": BOLT_API_VERSION,
        "language": BOLT_API_LANGUAGE,
        "session_id": SESSION_ID,
        "distinct_id": f"$device:{DEVICE_ID}",
        "country": BOLT_API_COUNTRY,
        "device_name": "web",
        "device_os_version": "web",
        "deviceId": DEVICE_ID,
        "deviceType": "web",
    }
    headers = dict(HEADERS)
    headers["Referer"] = url

    try:
        r = requests.get(api_url, params=params, headers=headers, timeout=8)
        if r.status_code >= 400:
            return None, f"Bolt API HTTP {r.status_code}"

        # Încearcă JSON
        j = r.json()

        # 1) Cazul nou (exact ca în răspunsul tău): booleene is_available_*
        iafd  = bool(j.get("data", {}).get("is_available_for_delivery", False))
        iafta = bool(j.get("data", {}).get("is_available_for_takeaway", False))
        iasd  = bool(j.get("data", {}).get("is_available_for_schedule_delivery", False))
        iast  = bool(j.get("data", {}).get("is_available_for_schedule_takeaway", False))
        overlay = j.get("data", {}).get("provider_overlay_text") or j.get("data", {}).get("provider_snackbar_text")

        if iafd or iafta or iasd or iast:
            flags = []
            if iafd:  flags.append("delivery")
            if iafta: flags.append("takeaway")
            if iasd:  flags.append("schedule_delivery")
            if iast:  flags.append("schedule_takeaway")
            reason = "Bolt API: disponibil (" + ", ".join(flags) + ")"
            if overlay:
                reason += f" | overlay: {overlay}"
            return "🟢 Deschis", reason

        # 2) Cazul vechi: availabilityStatus = open/closed ori status
        v = deep_find_key_ci(j, {"availabilitystatus", "status"})
        if isinstance(v, str):
            val = v.strip().lower()
            if val == "open":
                return "🟢 Deschis", "Bolt API: availabilityStatus=open"
            if val == "closed":
                return "🔴 Închis", "Bolt API: availabilityStatus=closed"

        # Dacă overlay sugerează închis
        t_overlay = (overlay or "").lower()
        if "închis" in t_overlay or "inchis" in t_overlay or "temporarily closed" in t_overlay:
            return "🔴 Închis", f"Bolt API: overlay='{overlay}'"

        return None, "Bolt API: răspuns ambiguu"
    except Exception as e:
        return None, f"Bolt API eroare: {str(e)[:140]}"

# ────────────────────────────────────────────────────────────────────────────────
# Clasificare pe baza HTML (fallback)
# ────────────────────────────────────────────────────────────────────────────────

def classify_with_reason(url: str, html: str) -> tuple[str, str]:
    """
    Returnează (status, motiv).
    Status: '🔴 Închis', '🟢 Deschis', '🟡 Nedetectabil', '❌ Eroare'
    """
    t, t_ascii = _normalize_html_text(html)
    avail_frag = _extract_availability_info_block(html)
    af_ascii = (
        unicodedata.normalize("NFKD", avail_frag).encode("ascii", "ignore").decode("ascii")
        if avail_frag
        else None
    )

    # ── BOLT (fallback UI)
    if "bolt.eu" in url:
        if "închis temporar" in t or "inchis temporar" in t_ascii or "temporarily closed" in t_ascii:
            return "🔴 Închis", "Bolt UI: „Închis temporar”"
        if re.search(r'"availabilitystatus"\s*:\s*"closed"', t):
            return "🔴 Închis", "Bolt JSON availabilityStatus=closed"
        if re.search(r'aria-label="[^"]*(închis|temporar|closed)[^"]*"', html, flags=re.IGNORECASE):
            return "🔴 Închis", "Bolt aria-label: conține 'închis/temporar/closed'"
        if avail_frag:
            if re.search(r"\binchis temporar\b", avail_frag) or (af_ascii and re.search(r"\binchis temporar\b", af_ascii)):
                return "🔴 Închis", "Bolt availabilityInfo: „Închis temporar”"
            if re.search(r"\binchis\b", avail_frag) or (af_ascii and re.search(r"\binchis\b", af_ascii)):
                return "🔴 Închis", "Bolt availabilityInfo: „Închis”"
            if re.search(r"deschide la \d{1,2}[:.]\d{2}", avail_frag) or (af_ascii and re.search(r"deschide la \d{1,2}[:.]\d{2}", af_ascii)):
                return "🔴 Închis", "Bolt availabilityInfo: „Deschide la HH:MM”"
            if re.search(r"\btemporarily closed\b", avail_frag) or (af_ascii and re.search(r"\btemporarily closed\b", af_ascii)):
                return "🔴 Închis", "Bolt availabilityInfo: „temporarily closed”"
        if ASSUME_CLOSED_WHEN_UNCERTAIN_BOLT:
            return "🔴 Închis", "Bolt: fallback ‘assume closed’ (nedetectabil)"
        return "🟡 Nedetectabil", "Bolt: niciun semnal clar (nici closed, nici opens-at)"

    # ── WOLT
    if "wolt.com" in url:
        if re.search(r'"is_open"\s*:\s*false', t):
            return "🔴 Închis", "Wolt JSON is_open=false"
        if re.search(r'"is_open"\s*:\s*true', t):
            return "🟢 Deschis", "Wolt JSON is_open=true"
        if re.search(r"\binchis\b", t) or re.search(r"\bclosed\b", t):
            return "🔴 Închis", "Wolt UI: conține „închis/closed”"
        return "🟡 Nedetectabil", "Wolt: is_open absent/nedetectabil"

    # ── fallback generic
    if re.search(r"\bclosed\b", t) or re.search(r"\binchis\b", t):
        return "🔴 Închis", "Text generic: ‘closed/închis’"
    if re.search(r"\bopen now\b", t) or re.search(r"\bdeschis acum\b", t):
        return "🟢 Deschis", "Text generic: ‘open now/deschis acum’"
    return "🟡 Nedetectabil", "Fără semnale în HTML"

# ────────────────────────────────────────────────────────────────────────────────
# Motor de verificare
# ────────────────────────────────────────────────────────────────────────────────
last_full_check_time: str | None = None
last_results: dict[str, dict] = {}   # key = url

def fetch_status_and_reason(url: str) -> tuple[str, str]:
    # 1) Bolt: încearcă API-ul oficial
    if "bolt.eu" in url:
        status, reason = bolt_api_status_for_url(url)
        if status is not None:
            return status, reason
        # dacă nu a decis, cade pe verificarea HTML

    # 2) Descărcare HTML & clasificare
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code >= 400:
            return f"🔴 Închis ({r.status_code})", f"HTTP {r.status_code}"
        return classify_with_reason(url, r.text)
    except Exception as e:
        return "❌ Eroare", f"Eroare rețea: {str(e)[:140]}"

def check_all():
    global last_full_check_time, last_results
    items = sorted(RESTAURANTS, key=sort_key)
    out = {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        future_map = {pool.submit(fetch_status_and_reason, it["url"]): it for it in items}
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
    last_results = out
    last_full_check_time = now_str()

def background_loop():
    try:
        check_all()
    except Exception:
        pass
    while True:
        time.sleep(CHECK_INTERVAL_SEC)
        try:
            check_all()
        except Exception:
            pass

# ────────────────────────────────────────────────────────────────────────────────
# Web
# ────────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)

HTML_TEMPLATE = """<!doctype html>
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
    .card h2 { margin:0; padding:16px 18px; font-size:20px; border-bottom:1px solid #22313a; }
    table { width:100%; border-collapse:collapse; }
    th, td { padding:12px 16px; border-bottom:1px solid #22313a; text-align:left; font-size:14px; vertical-align:top; }
    th { color:#bbd0da; font-weight:700; }
    .status { font-weight:700; }
    .ok { color:var(--ok); }
    .bad { color:var(--bad); }
    .warn { color:var(--warn); }
    .muted { color:var(--muted); }
    .chip { display:inline-block; background:var(--chip); color:#a7d6c2; padding:2px 8px; border-radius:999px; font-size:12px; margin-left:8px; }
    .footer { margin-top:18px; color:var(--muted); font-size:12px; }
    .version { margin-left:auto; background:#23313a; color:#9cc5b3; padding:2px 8px; border-radius:999px; font-size:12px; }
    .headerline { display:flex; gap:12px; align-items:center; }
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
    </div>

    <div class="grid">
      <div class="card">
        <h2>Bolt</h2>
        <div class="muted" style="padding:0 18px 8px;">Ordonare: Burgers → Smash → Tacos, apoi locații</div>
        <table id="bolt">
          <thead>
            <tr><th>Locație</th><th>Status</th><th>Motiv</th><th>Verificat la</th></tr>
          </thead>
          <tbody></tbody>
        </table>
      </div>

      <div class="card">
        <h2>Wolt</h2>
        <table id="wolt">
          <thead>
            <tr><th>Locație</th><th>Status</th><th>Motiv</th><th>Verificat la</th></tr>
          </thead>
          <tbody></tbody>
        </table>
      </div>
    </div>

    <div class="footer">
      Dacă un rând este „🟡 Nedetectabil”, cauza probabilă: pagina e SPA și nu oferă încă text server-side.
      Pentru Bolt poți seta <code>ASSUME_CLOSED_WHEN_UNCERTAIN_BOLT=true</code> ca fallback → „Închis”.
    </div>
  </div>

<script>
const $ = (sel) => document.querySelector(sel);

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
  html += '<td class="muted">' + it.reason + '</td>';
  html += '<td class="muted">' + it.checked_at + '</td>';
  html += '</tr>';
  return html;
}

function fillTables(data) {
  $("#last-check").textContent = data.last_full_check || "—";
  const boltRows = [];
  const woltRows = [];
  (data.items || []).forEach(function(it){
    (it.platform === "Bolt" ? boltRows : woltRows).push(rowHTML(it));
  });
  $("#bolt tbody").innerHTML = boltRows.join("") || '<tr><td colspan="4" class="muted">—</td></tr>';
  $("#wolt tbody").innerHTML = woltRows.join("") || '<tr><td colspan="4" class="muted">—</td></tr>';
}

async function load() {
  try {
    const r = await fetch("/api/status");
    const j = await r.json();
    fillTables(j);
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

load();
setInterval(load, 30000);
</script>
</body>
</html>
"""

@app.route("/")
def index() -> Response:
    html = HTML_TEMPLATE.replace("__VERSION__", VERSION).replace("__INTERVAL__", str(CHECK_INTERVAL_SEC))
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
    check_all()
    return jsonify({"ok": True, "refreshed_at": now_str()})

# ────────────────────────────────────────────────────────────────────────────────
# Pornire app + background checker
# ────────────────────────────────────────────────────────────────────────────────
def _start_background():
    t = threading.Thread(target=background_loop, daemon=True)
    t.start()

_start_background()

if __name__ == "__main__":
    # dev local
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
