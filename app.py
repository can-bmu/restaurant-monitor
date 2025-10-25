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
import uuid

import requests
from flask import Flask, jsonify, request, Response

# ────────────────────────────────────────────────────────────────────────────────
# Config
# ────────────────────────────────────────────────────────────────────────────────
VERSION = "v0.3.2"
TZ = ZoneInfo("Europe/Bucharest")

CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL_SEC", "60"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "6"))
REQ_TIMEOUT = int(os.getenv("REQ_TIMEOUT", "10"))

# Folosit de Wolt pentru a calcula „open” în funcție de zonă
WOLT_LAT = float(os.getenv("WOLT_LAT", "44.4268"))
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
    {"platform": "Bolt", "location": "Smash Pipera", "url": "https://food.bolt.eu/en-US/325-bucharest/p/157013-smash-gorilla"},
    {"platform": "Bolt", "location": "Tacos Olteniței", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/130672-gorilla's-crazy-tacos"},
    # Test deschis Bolt
    {"platform": "Bolt", "location": "Test: Liquid Spirits", "url": "https://food.bolt.eu/ro-RO/325-bucharest/p/126569-liquid-spirits"},

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
    # Test Wolt
    {"platform": "Wolt", "location": "Test: Shaormeria CA", "url": "https://wolt.com/ro/rou/bucharest/restaurant/shaormeria-ca-67dc3efb2e58c74a8f3511df"},
]

# ────────────────────────────────────────────────────────────────────────────────
# Helpers
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

def is_test_location(location: str) -> bool:
    return location.strip().lower().startswith("test:")

BRAND_ORDER = {"Burgers": 1, "Smash": 2, "Tacos": 3}
LOC_ORDER = {"militari": 1, "olteni": 2, "mosilor": 3, "moșilor": 3, "pipera": 4}

def sort_key(item: dict):
    b = BRAND_ORDER.get(brand_of(item["location"]), 99)
    loc = item["location"].lower()
    loc_score = next((v for k, v in LOC_ORDER.items() if k in loc), 99)
    # Bolt în față
    return (item["platform"] != "Bolt", b, loc_score, item["location"])

def _normalize_html_text(s: str):
    # Normalizează whitespace + diacritice și produce și o variantă ASCII
    s = html_lib.unescape(s).replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s)
    s_lower = s.lower()
    s_ascii = unicodedata.normalize("NFKD", s_lower).encode("ascii", "ignore").decode("ascii")
    return s_lower, s_ascii

# ────────────────────────────────────────────────────────────────────────────────
# Bolt: verificare oficială prin API
# ────────────────────────────────────────────────────────────────────────────────
def bolt_check_via_api(url: str):
    m = re.search(r"/p/(\d+)", url)
    if not m:
        return None
    pid = m.group(1)
    api = "https://deliveryuser.live.boltsvc.net/deliveryClient/public/getProviderAvailabilityStatus"
    params = {
        "provider_id": pid,
        "version": "FW.1.98",
        "language": "ro-RO",
        "device_name": "web",
        "device_os_version": "web",
        "deviceType": "web",
        "deviceId": str(uuid.uuid5(uuid.NAMESPACE_URL, f"bolt:{pid}")),
    }
    try:
        r = requests.get(api, headers=HEADERS, params=params, timeout=REQ_TIMEOUT)
        if r.status_code >= 400:
            return None
        data = r.json().get("data", {})
        # Dacă e disponibil pe oricare din canale, considerăm deschis
        if any(data.get(k) for k in [
            "is_available_for_delivery",
            "is_available_for_takeaway",
            "is_available_for_schedule_delivery",
            "is_available_for_schedule_takeaway",
        ]):
            return "🟢 Deschis", "Bolt API: disponibil"
        # Mesaj explicit „Deschide la …”
        ov = (data.get("provider_overlay_text") or {}).get("value")
        sb = (data.get("provider_snackbar_text") or {}).get("value")
        msg = (ov or sb or "").strip()
        if msg and "deschide la" in msg.lower():
            return "🔴 Închis", f'Bolt API: „{msg}”'
        return "🔴 Închis", "Bolt API: indisponibil"
    except Exception:
        return None

# ────────────────────────────────────────────────────────────────────────────────
# Wolt: API + fallback HTML
# ────────────────────────────────────────────────────────────────────────────────
def wolt_slug_from_url(url: str):
    segs = [s for s in urlparse(url).path.split("/") if s]
    if "restaurant" in segs:
        i = segs.index("restaurant")
        if i + 1 < len(segs):
            return segs[i + 1]
    return segs[-1] if segs else None

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
        data = r.json()
        is_open = (
            data.get("venue", {}).get("is_open")
            or data.get("page", {}).get("data", {}).get("venue", {}).get("is_open")
        )
        if is_open is True:
            return "🟢 Deschis", "Wolt API: is_open=True"
        if is_open is False:
            return "🔴 Închis", "Wolt API: is_open=False"
        return None
    except Exception:
        return None

def wolt_check_html(url: str):
    """
    Heuristică din HTML (server-side) pentru Wolt:
      - butonul 'Programează o comandă' => închis
      - 'Închis' / 'Se deschide la HH:MM' => închis
      - 'Deschis până la HH:MM' / 'open until HH:MM' => deschis
    """
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQ_TIMEOUT)
        t, t_ascii = _normalize_html_text(r.text)

        # buton indisponibilitate livrare
        if 'data-test-id="venuetoolbar.deliveryunavailablestatusbutton"' in t:
            return "🔴 Închis", "Wolt UI: ‘Programează o comandă’"

        # închis / se deschide la
        if re.search(r"\binchis\b", t) or re.search(r"\binchis\b", t_ascii):
            return "🔴 Închis", "Wolt UI: ‘Închis’"
        if re.search(r"\bse deschide la\s+\d{1,2}[:.]\d{2}\b", t) or re.search(r"\bse deschide la\s+\d{1,2}[:.]\d{2}\b", t_ascii):
            return "🔴 Închis", "Wolt UI: ‘Se deschide la …’"

        # deschis până la / open until
        if re.search(r"deschis p(?:â|a)na la\s+\d{1,2}[:.]\d{2}", t) or re.search(r"deschis pana la\s+\d{1,2}[:.]\d{2}", t_ascii):
            return "🟢 Deschis", "Wolt UI: ‘Deschis până la …’"
        if re.search(r"\bopen until\s+\d{1,2}[:.]\d{2}\b", t_ascii):
            return "🟢 Deschis", "Wolt UI: ‘Open until …’"

        # fallback moale
        if re.search(r"\bdeschis\b", t) or re.search(r"\bopen now\b", t_ascii):
            return "🟢 Deschis", "Wolt UI: ‘deschis/open now’"

        return "🟡 Nedetectabil", "Wolt UI: fără semnal clar"
    except Exception as e:
        return "❌ Eroare", f"Wolt HTML: {str(e)[:100]}"

# ────────────────────────────────────────────────────────────────────────────────
# Verificare globală
# ────────────────────────────────────────────────────────────────────────────────
last_full_check_time = None
last_results: dict[str, dict] = {}

def fetch_status_and_reason(url: str):
    if "bolt.eu" in url:
        r = bolt_check_via_api(url)
        if r:
            return r
        # fallback HTML Bolt (rareori util), marcat ca nedetectabil dacă nu găsim nimic
        return "🟡 Nedetectabil", "Bolt: fără semnal API și fără fallback"

    if "wolt.com" in url:
        r = wolt_check_via_api(url)
        if r:
            return r
        return wolt_check_html(url)

    return "🟡 Nedetectabil", "URL neidentificat"

def check_all():
    global last_full_check_time, last_results
    items = sorted(RESTAURANTS, key=sort_key)
    out = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fetch_status_and_reason, it["url"]): it for it in items}
        for fut in as_completed(futures):
            it = futures[fut]
            status, reason = fut.result()
            out[it["url"]] = {
                "platform": it["platform"],
                "location": it["location"],
                "brand": brand_of(it["location"]),
                "is_test": is_test_location(it["location"]),
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
# Flask web app
# ────────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/")
def index():
    return f"<h1 style='font-family:sans-serif'>Monitor restaurante v{VERSION}</h1><p><a href='/api/status'>/api/status</a></p>"

@app.route("/api/status")
def api_status():
    items = []
    for it in sorted(RESTAURANTS, key=sort_key):
        base = {
            "platform": it["platform"],
            "location": it["location"],
            "brand": brand_of(it["location"]),
            "is_test": is_test_location(it["location"]),
            "url": it["url"],
        }
        r = last_results.get(it["url"])
        items.append({
            **base,
            **(r or {"status": "🟡 Nedetectabil", "reason": "Încă nu s-a verificat", "checked_at": "—"}),
        })
    return jsonify({"version": VERSION, "last_full_check": last_full_check_time, "items": items})

@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    check_all()
    return jsonify({"ok": True, "refreshed_at": now_str()})

# ────────────────────────────────────────────────────────────────────────────────
# Start
# ────────────────────────────────────────────────────────────────────────────────
def _start_background():
    t = threading.Thread(target=background_loop, daemon=True)
    t.start()

_start_background()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
