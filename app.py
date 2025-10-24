import os
import re
import html as html_lib
import unicodedata
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from flask import Flask, jsonify, Response

# ────────────────────────────────────────────────────────────────────────────────
# Config
# ────────────────────────────────────────────────────────────────────────────────
VERSION = "v0.1.6 beta"
TZ = ZoneInfo("Europe/Bucharest")

CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL_SEC", "60"))
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

BOLT_API_URL = (
    "https://deliveryuser.live.boltsvc.net/"
    "deliveryClient/public/getProviderAvailabilityStatus"
)

# ────────────────────────────────────────────────────────────────────────────────
# Date: restaurante (Bolt + Wolt)
# ────────────────────────────────────────────────────────────────────────────────
RESTAURANTS = [
    # BOLT (Gorilla)
    {"platform": "Bolt", "location": "Burgers Militari", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/53203"},
    {"platform": "Bolt", "location": "Smash Militari", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/157022-smash-gorilla/info"},
    {"platform": "Bolt", "location": "Burgers Olteniței", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/81061-gorilla's-crazy-burgers-berceni"},
    {"platform": "Bolt", "location": "Smash Olteniței", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/156512"},
    {"platform": "Bolt", "location": "Smash Moșilor", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/157033-smash-gorilla"},
    {"platform": "Bolt", "location": "Burgers Moșilor", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/69192-gorilla's-crazy-burgers-mosilor"},
    {"platform": "Bolt", "location": "Burgers Pipera", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/122872-gorilla's-crazy-burgers-pipera"},
    {"platform": "Bolt", "location": "Smash Pipera", "url": "https://food.bolt.eu/en-US/325-bucharest/p/157013-smash-gorilla/?utm_content=menu_header&utm_medium=product&utm_source=share_provider"},
    {"platform": "Bolt", "location": "Tacos Olteniței", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/130672-gorilla's-crazy-tacos"},

    # BOLT (test deschis/închis cu API)
    {"platform": "Bolt", "location": "Test: Liquid Spirits", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/126569-liquid-spirits"},

    # WOLT (Gorilla)
    {"platform": "Wolt", "location": "Burgers Militari", "url": "https://wolt.com/en/rou/bucharest/restaurant/gorillas-crazy-burgers-gorjului-67dc3f47b93a5300e8efd705"},
    {"platform": "Wolt", "location": "Smash Militari", "url": "https://wolt.com/ro/rou/bucharest/restaurant/smash-gorilla-gorjului-6880a63946c4278a97069f59"},
    {"platform": "Wolt", "location": "Burgers Olteniței", "url": "https://wolt.com/ro/rou/bucharest/restaurant/gorillas-crazy-burgers-oltenitei-67e189430bd3fc375bb3acc8"},
    {"platform": "Wolt", "location": "Smash Olteniței", "url": "https://wolt.com/ro/rou/bucharest/restaurant/smash-gorilla-berceni-6880a32754547abea1869cec"},
    {"platform": "Wolt", "location": "Smash Moșilor", "url": "https://wolt.com/en/rou/bucharest/restaurant/smash-gorilla-mosilor-6880a63946c4278a97069f5a"},
    {"platform": "Wolt", "location": "Burgers Moșilor", "url": "https://wolt.com/en/rou/bucharest/restaurant/gorillas-crazy-burgers-mosilor-67dc3f47b93a5300e8efd706"},
    {"platform": "Wolt", "location": "Burgers Pipera", "url": "https://wolt.com/ro/rou/bucharest/restaurant/gorillas-crazy-burgers-pipera-67e189430bd3fc375bb3acc9"},
    {"platform": "Wolt", "location": "Smash Pipera", "url": "https://wolt.com/en/rou/bucharest/restaurant/smash-gorilla-pipera-6880a32754547abea1869ced"},
    {"platform": "Wolt", "location": "Tacos Olteniței", "url": "https://wolt.com/en/rou/bucharest/restaurant/gorillas-crazy-tacos-berceni-67db0092e014794baf59070a"},

    # WOLT (test deschis/închis)
    {"platform": "Wolt", "location": "Test: Shaormeria CA", "url": "https://wolt.com/ro/rou/bucharest/restaurant/shaormeria-ca-67dc3efb2e58c74a8f3511df"},
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
    # Bolt înainte, apoi Wolt; apoi brand; apoi locația
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
# Bolt API helper
# ────────────────────────────────────────────────────────────────────────────────
PROVIDER_ID_RE = re.compile(r"/p/(\d+)")

def bolt_provider_id_from_url(url: str) -> str | None:
    m = PROVIDER_ID_RE.search(url)
    return m.group(1) if m else None

def bolt_api_probe(url: str, timeout: float = 8.0) -> tuple[str, str] | None:
    """
    Întoarce (status, motiv) folosind API-ul public Bolt.
    Dacă nu putem determina (eroare / lipsă id), returnează None pentru fallback pe HTML.
    """
    provider_id = bolt_provider_id_from_url(url)
    if not provider_id:
        return None

    # Referer = pagina restaurantului (ajută uneori)
    referer = f"{urlparse(url).scheme}://{urlparse(url).netloc}{urlparse(url).path}"
    params = {
        "provider_id": provider_id,
        "version": "FW.1.98",
        "language": "ro-RO",
        "session_id": f"mon-{provider_id}",
        "distinct_id": f"$device:mon-{provider_id}",
        "country": "ro",
        "device_name": "web",
        "device_os_version": "web",
        "deviceId": f"mon-{provider_id}",
        "deviceType": "web",
    }
    try:
        r = requests.get(
            BOLT_API_URL,
            params=params,
            headers={**HEADERS, "Referer": referer},
            timeout=timeout,
        )
        j = r.json()
    except Exception:
        return None

    if not isinstance(j, dict) or "data" not in j:
        return None

    d = j.get("data") or {}
    # Booleenele de disponibilitate
    avail_keys = [
        "is_available_for_delivery",
        "is_available_for_takeaway",
        "is_available_for_schedule_delivery",
        "is_available_for_schedule_takeaway",
    ]
    any_true = any(bool(d.get(k)) for k in avail_keys)

    # Texte explicative (overlay/snackbar)
    overlay = d.get("provider_overlay_text") or {}
    snack = d.get("provider_snackbar_text") or {}
    overlay_val = overlay.get("value") if isinstance(overlay, dict) else overlay
    snack_val = snack.get("value") if isinstance(snack, dict) else snack

    if any_true:
        # Deschis pe cel puțin un canal
        reason_bits = []
        if d.get("is_available_for_delivery"):
            reason_bits.append("delivery")
        if d.get("is_available_for_takeaway"):
            reason_bits.append("takeaway")
        if d.get("is_available_for_schedule_delivery"):
            reason_bits.append("schedule_delivery")
        if d.get("is_available_for_schedule_takeaway"):
            reason_bits.append("schedule_takeaway")
        return "🟢 Deschis", f"Bolt API: disponibil ({', '.join(reason_bits)})"

    # Toate false → Închis; încearcă să folosești mesajul lor (ex. „Deschide la 14:00”)
    msg = overlay_val or snack_val
    if isinstance(msg, str) and msg.strip():
        return "🔴 Închis", f"Bolt API: {msg.strip()}"

    return "🔴 Închis", "Bolt API: indisponibil (toate canalele false)"

# ────────────────────────────────────────────────────────────────────────────────
# Clasificare HTML (fallback)
# ────────────────────────────────────────────────────────────────────────────────
def classify_with_reason(url: str, html: str) -> tuple[str, str]:
    """
    Returnează (status, motiv) pe baza HTML (fallback).
    """
    t, t_ascii = _normalize_html_text(html)
    avail_frag = _extract_availability_info_block(html)
    af_ascii = (
        unicodedata.normalize("NFKD", avail_frag).encode("ascii", "ignore").decode("ascii")
        if avail_frag
        else None
    )

    # ── BOLT (fallback doar dacă API-ul nu a furnizat răspuns)
    if "bolt.eu" in url:
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

def fetch_status_and_reason(it: dict) -> tuple[str, str]:
    url = it["url"]

    # 1) BOLT: încearcă API-ul întâi
    if "bolt.eu" in url:
        api_res = bolt_api_probe(url)
        if api_res is not None:
            return api_res

    # 2) HTML fallback (Bolt când API e None, Wolt întotdeauna)
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
        future_map = {pool.submit(fetch_status_and_reason, it): it for it in items}
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
      Pentru Bolt folosim API-ul lor public; dacă e indisponibil, revenim la HTML.
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
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
