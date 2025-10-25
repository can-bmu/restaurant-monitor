import os
import re
import html as html_lib
import json
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
VERSION = "v0.2.1 beta"
TZ = ZoneInfo("Europe/Bucharest")

CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL_SEC", "60"))
ASSUME_CLOSED_WHEN_UNCERTAIN_BOLT = os.getenv(
    "ASSUME_CLOSED_WHEN_UNCERTAIN_BOLT", "false"
).lower() in ("1", "true", "yes")

REQ_TIMEOUT = (4, 6)  # (connect, read) seconds
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "6"))

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
    {"platform": "Bolt", "location": "Burgers Militari",  "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/53203"},
    {"platform": "Bolt", "location": "Smash Militari",    "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/157022-smash-gorilla/info"},
    {"platform": "Bolt", "location": "Burgers Olteniței", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/81061-gorilla's-crazy-burgers-berceni"},
    {"platform": "Bolt", "location": "Smash Olteniței",   "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/156512"},
    {"platform": "Bolt", "location": "Smash Moșilor",     "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/157033-smash-gorilla"},
    {"platform": "Bolt", "location": "Burgers Moșilor",   "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/69192-gorilla's-crazy-burgers-mosilor"},
    {"platform": "Bolt", "location": "Burgers Pipera",    "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/122872-gorilla's-crazy-burgers-pipera"},
    {"platform": "Bolt", "location": "Smash Pipera",      "url": "https://food.bolt.eu/en-US/325-bucharest/p/157013-smash-gorilla/?utm_content=menu_header&utm_medium=product&utm_source=share_provider"},
    {"platform": "Bolt", "location": "Tacos Olteniței",   "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/130672-gorilla's-crazy-tacos"},
    # BOLT – test deschis
    {"platform": "Bolt", "location": "Test: Liquid Spirits", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/126569-liquid-spirits"},

    # WOLT
    {"platform": "Wolt", "location": "Burgers Militari",  "url": "https://wolt.com/en/rou/bucharest/restaurant/gorillas-crazy-burgers-gorjului-67dc3f47b93a5300e8efd705"},
    {"platform": "Wolt", "location": "Smash Militari",    "url": "https://wolt.com/ro/rou/bucharest/restaurant/smash-gorilla-gorjului-6880a63946c4278a97069f59"},
    {"platform": "Wolt", "location": "Burgers Olteniței", "url": "https://wolt.com/ro/rou/bucharest/restaurant/gorillas-crazy-burgers-oltenitei-67e189430bd3fc375bb3acc8"},
    {"platform": "Wolt", "location": "Smash Olteniței",   "url": "https://wolt.com/ro/rou/bucharest/restaurant/smash-gorilla-berceni-6880a32754547abea1869cec"},
    {"platform": "Wolt", "location": "Smash Moșilor",     "url": "https://wolt.com/en/rou/bucharest/restaurant/smash-gorilla-mosilor-6880a63946c4278a97069f5a"},
    {"platform": "Wolt", "location": "Burgers Moșilor",   "url": "https://wolt.com/en/rou/bucharest/restaurant/gorillas-crazy-burgers-mosilor-67dc3f47b93a5300e8efd706"},
    {"platform": "Wolt", "location": "Burgers Pipera",    "url": "https://wolt.com/ro/rou/bucharest/restaurant/gorillas-crazy-burgers-pipera-67e189430bd3fc375bb3acc9"},
    {"platform": "Wolt", "location": "Smash Pipera",      "url": "https://wolt.com/en/rou/bucharest/restaurant/smash-gorilla-pipera-6880a32754547abea1869ced"},
    {"platform": "Wolt", "location": "Tacos Olteniței",   "url": "https://wolt.com/en/rou/bucharest/restaurant/gorillas-crazy-tacos-berceni-67db0092e014794baf59070a"},
    # WOLT – test deschis
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
    return (item["platform"] != "Bolt", b, loc_score, item["location"])

def _normalize_html_text(s: str) -> tuple[str, str]:
    s = html_lib.unescape(s).lower().replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s)
    s_ascii = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return s, s_ascii

# ────────────────────────────────────────────────────────────────────────────────
# BOLT helpers
# ────────────────────────────────────────────────────────────────────────────────

BOLT_AVAIL_URL = "https://deliveryuser.live.boltsvc.net/deliveryClient/public/getProviderAvailabilityStatus"

def bolt_provider_id_from_url(url: str) -> str | None:
    # caută /p/<id> sau /p/<id>-slug
    m = re.search(r"/p/(\d+)(?:[^\d]|$)", url)
    if m:
        return m.group(1)
    return None

def bolt_check_availability(url: str) -> tuple[str, str] | None:
    provider_id = bolt_provider_id_from_url(url)
    if not provider_id:
        return None

    params = {
        "provider_id": provider_id,
        "version": "FW.1.98",
        "language": "ro-RO",
        "session_id": "sess-" + provider_id,
        "distinct_id": "dev-" + provider_id,
        "country": "ro",
        "device_name": "web",
        "device_os_version": "web",
        "deviceId": "dev-" + provider_id,
        "deviceType": "web",
    }
    try:
        r = requests.get(BOLT_AVAIL_URL, headers=HEADERS, params=params, timeout=REQ_TIMEOUT)
        if r.status_code >= 400:
            return None
        data = r.json().get("data", {})
        d = bool(data.get("is_available_for_delivery"))
        t = bool(data.get("is_available_for_takeaway"))
        sd = bool(data.get("is_available_for_schedule_delivery"))
        st = bool(data.get("is_available_for_schedule_takeaway"))
        overlay = data.get("provider_overlay_text", {})
        snack = data.get("provider_snackbar_text", {})
        overlay_txt = overlay.get("value") if isinstance(overlay, dict) else None
        snack_txt = snack.get("value") if isinstance(snack, dict) else None

        if any([d, t, sd, st]):
            reason = "Bolt API: disponibil"
            flags = []
            if d:  flags.append("delivery")
            if t:  flags.append("takeaway")
            if sd: flags.append("schedule_delivery")
            if st: flags.append("schedule_takeaway")
            if flags:
                reason += " (" + ", ".join(flags) + ")"
            if overlay_txt:
                reason += f" • {overlay_txt}"
            if snack_txt and snack_txt != overlay_txt:
                reason += f" • {snack_txt}"
            return "🟢 Deschis", reason
        else:
            # Închis, eventual cu message tip „Deschide la …”
            msg = "Bolt API: indisponibil"
            if overlay_txt:
                msg += f" • {overlay_txt}"
            if snack_txt and snack_txt != overlay_txt:
                msg += f" • {snack_txt}"
            return "🔴 Închis", msg
    except Exception:
        return None

# ────────────────────────────────────────────────────────────────────────────────
# WOLT helpers
# ────────────────────────────────────────────────────────────────────────────────

def wolt_slug_from_url(url: str) -> str | None:
    """
    Extrage slug-ul după '/restaurant/'.
    Ex: https://wolt.com/.../restaurant/shaormeria-ca-67dc...  -> 'shaormeria-ca-67dc...'
    """
    try:
        path = urlparse(url).path
        if "/restaurant/" in path:
            return path.split("/restaurant/")[-1].strip("/ ")
    except Exception:
        pass
    return None

def wolt_check_via_api(url: str) -> tuple[str, str] | None:
    slug = wolt_slug_from_url(url)
    if not slug:
        return None
    api = f"https://restaurant-api.wolt.com/v1/pages/venue/{slug}"
    try:
        r = requests.get(api, headers=HEADERS, timeout=REQ_TIMEOUT)
        if r.status_code >= 400:
            return None
        data = r.json()
        # Căutăm mai multe posibile căi pentru flag-ul open:
        # 1) data['venue']['is_open']
        # 2) data['venue']['online']
        # 3) data['page']['data']['venue']['open'] / 'online'
        # 4) data['page']['data']['sections'] ... (fallback)
        def deep_get(obj, keys, default=None):
            cur = obj
            for k in keys:
                if isinstance(cur, dict) and k in cur:
                    cur = cur[k]
                else:
                    return default
            return cur

        candidates = [
            ("is_open", deep_get(data, ["venue", "is_open"])),
            ("online", deep_get(data, ["venue", "online"])),
            ("is_open", deep_get(data, ["page", "data", "venue", "is_open"])),
            ("online", deep_get(data, ["page", "data", "venue", "online"])),
            ("delivery_open_status", deep_get(data, ["venue", "delivery_open_status"])),
            ("delivery_open_status", deep_get(data, ["page", "data", "venue", "delivery_open_status"])),
        ]

        # Normalizează statusul
        for key, val in candidates:
            if val is None:
                continue
            # bool direct
            if isinstance(val, bool):
                if val:
                    return "🟢 Deschis", f"Wolt API: {key}=true"
                else:
                    return "🔴 Închis", f"Wolt API: {key}=false"
            # string ex: 'OPEN', 'CLOSED'
            if isinstance(val, str):
                v = val.upper()
                if v in ("OPEN", "ONLINE", "OPEN_FOR_DELIVERIES"):
                    return "🟢 Deschis", f"Wolt API: {key}={val}"
                if v in ("CLOSED", "OFFLINE", "CLOSED_FOR_DELIVERIES"):
                    return "🔴 Închis", f"Wolt API: {key}={val}"

        # uneori există un banner textual „Se deschide la … / Deschis până la …”
        text_candidates = json.dumps(data, ensure_ascii=False).lower()
        if "se deschide la" in text_candidates or "deschide la" in text_candidates:
            return "🔴 Închis", "Wolt API: „Se deschide la …”"
        if "deschis până la" in text_candidates or "open until" in text_candidates:
            return "🟢 Deschis", "Wolt API: „Deschis până la …”"

        return None
    except Exception:
        return None

# ────────────────────────────────────────────────────────────────────────────────
# Heuristici HTML (fallback)
# ────────────────────────────────────────────────────────────────────────────────

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

def classify_with_reason_fallback(url: str, html: str) -> tuple[str, str]:
    """
    Fallback pe HTML pentru ambele platforme.
    """
    t, t_ascii = _normalize_html_text(html)
    avail_frag = _extract_availability_info_block(html)
    af_ascii = (
        unicodedata.normalize("NFKD", avail_frag).encode("ascii", "ignore").decode("ascii")
        if avail_frag
        else None
    )

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

    if "wolt.com" in url:
        if re.search(r'"is_open"\s*:\s*false', t):
            return "🔴 Închis", "Wolt JSON is_open=false"
        if re.search(r'"is_open"\s*:\s*true', t):
            return "🟢 Deschis", "Wolt JSON is_open=true"
        if re.search(r"\binchis\b", t) or re.search(r"\bclosed\b", t):
            return "🔴 Închis", "Wolt UI: conține „închis/closed”"
        if re.search(r"\bdeschis\b", t) or re.search(r"\bopen now\b", t):
            return "🟢 Deschis", "Wolt UI: conține „deschis/open now”"
        return "🟡 Nedetectabil", "Wolt: semnal UI/JSON absent"

    # fallback generic
    if re.search(r"\bclosed\b", t) or re.search(r"\binchis\b", t):
        return "🔴 Închis", "Text generic: ‘closed/închis’"
    if re.search(r"\bopen now\b", t) or re.search(r"\bdeschis acum\b", t):
        return "🟢 Deschis", "Text generic: ‘open now/deschis acum’"
    return "🟡 Nedetectabil", "Fără semnale în HTML"

# ────────────────────────────────────────────────────────────────────────────────
# Clasificator principal (cu API-uri + fallback)
# ────────────────────────────────────────────────────────────────────────────────

def classify_with_reason(url: str) -> tuple[str, str]:
    # 1) Încearcă API specializat
    if "bolt.eu" in url:
        api_res = bolt_check_availability(url)
        if api_res:
            return api_res
    elif "wolt.com" in url:
        api_res = wolt_check_via_api(url)
        if api_res:
            return api_res

    # 2) Fallback: HTML
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQ_TIMEOUT)
        if r.status_code >= 400:
            return (f"🔴 Închis ({r.status_code})", f"HTTP {r.status_code}")
        return classify_with_reason_fallback(url, r.text)
    except Exception as e:
        return "❌ Eroare", f"Eroare rețea: {str(e)[:140]}"

# ────────────────────────────────────────────────────────────────────────────────
# Motor de verificare
# ────────────────────────────────────────────────────────────────────────────────
last_full_check_time: str | None = None
last_results: dict[str, dict] = {}   # key = url

def fetch_status_and_reason(url: str) -> tuple[str, str]:
    try:
        return classify_with_reason(url)
    except Exception as e:
        return "❌ Eroare", f"Eroare internă: {str(e)[:140]}"

def check_all():
    global last_full_check_time, last_results
    items = sorted(RESTAURANTS, key=sort_key)

    out = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
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
# Web (UI cu comutator Simplu/Detaliat)
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
    .hide { display:none; }
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
      <button id="toggleView" class="btn" style="background:#2c3e50">Teste</button>
    </div>

    <div class="grid">
      <div class="card">
        <h2>Bolt</h2>
        <div class="muted" style="padding:0 18px 8px;">Ordonare: Burgers → Smash → Tacos, apoi locații</div>
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
      Dacă un rând este „🟡 Nedetectabil”, cauza probabilă: pagina e SPA și nu oferă text server-side.
      Pentru Bolt poți seta <code>ASSUME_CLOSED_WHEN_UNCERTAIN_BOLT=true</code> ca fallback → „Închis”.
    </div>
  </div>

<script>
const $ = (sel) => document.querySelector(sel);
let detailed = false;

function setDetailed(on) {
  detailed = !!on;
  document.querySelectorAll(".col-detail").forEach(el => {
    el.style.display = detailed ? "" : "none";
  });
  // ascunde și celulele corespunzătoare
  document.querySelectorAll("td.t-detail").forEach(el => {
    el.style.display = detailed ? "" : "none";
  });
}

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
  html += '<td class="muted t-detail" style="' + (detailed ? '' : 'display:none') + '">' + (it.reason || '—') + '</td>';
  html += '<td class="muted t-detail" style="' + (detailed ? '' : 'display:none') + '">' + (it.checked_at || '—') + '</td>';
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

$("#toggleView").addEventListener("click", function() {
  setDetailed(!detailed);
});

setDetailed(false);
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
    # Local dev
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
