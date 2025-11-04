import os
import re
import json
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
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request, Response

# ────────────────────────────────────────────────────────────────────────────────
# Config
# ────────────────────────────────────────────────────────────────────────────────
VERSION = "v0.4.0"
TZ = ZoneInfo("Europe/Bucharest")

CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL_SEC", "60"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "6"))
REQ_TIMEOUT = int(os.getenv("REQ_TIMEOUT", "10"))

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
# Funcții utilitare
# ────────────────────────────────────────────────────────────────────────────────
def now_str():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

def brand_of(location: str) -> str:
    l = location.lower()
    if "taco" in l:
        return "Tacos"
    if "smash" in l:
        return "Smash"
    return "Burgers"

def sort_key(item: dict):
    order = {"Burgers": 1, "Smash": 2, "Tacos": 3}
    loc_order = {"militari": 1, "olten": 2, "mosilor": 3, "pipera": 4}
    b = order.get(brand_of(item["location"]), 99)
    loc_score = 99
    for k, v in loc_order.items():
        if k in item["location"].lower():
            loc_score = v
            break
    return (item["platform"] != "Bolt", b, loc_score)

# ────────────────────────────────────────────────────────────────────────────────
# BOLT API
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
        j = r.json().get("data", {})
        if any([
            j.get("is_available_for_delivery"),
            j.get("is_available_for_takeaway"),
            j.get("is_available_for_schedule_delivery"),
            j.get("is_available_for_schedule_takeaway")
        ]):
            return "🟢 Deschis", "Bolt API: disponibil"
        txt = (j.get("provider_overlay_text") or {}).get("value") or ""
        if "deschide la" in txt.lower():
            return "🔴 Închis", f"Bolt API: {txt}"
        return "🔴 Închis", "Bolt API: indisponibil"
    except Exception:
        return None

# ────────────────────────────────────────────────────────────────────────────────
# WOLT API
# ────────────────────────────────────────────────────────────────────────────────
def wolt_slug_from_url(url: str):
    m = re.search(r"/restaurant/([^/?]+)", url)
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
        j = r.json().get("page_props", {}).get("venue", {})
        state = j.get("venue_state") or ""
        online = j.get("online", False)
        if online or "OPEN" in state.upper():
            return "🟢 Deschis", f"Wolt API: {state or 'online'}"
        elif "CLOSED" in state.upper():
            return "🔴 Închis", f"Wolt API: {state}"
        else:
            return "🟡 Nedetectabil", f"Wolt API: fără semnal clar ({state})"
    except Exception:
        return None

# ────────────────────────────────────────────────────────────────────────────────
# Verificare principală
# ────────────────────────────────────────────────────────────────────────────────
def fetch_status_and_reason(url: str):
    if "bolt.eu" in url:
        r = bolt_check_via_api(url)
        if r:
            return r
    if "wolt.com" in url:
        r = wolt_check_via_api(url)
        if r:
            return r
    return "🟡 Nedetectabil", "Fallback: fără semnal clar"

def check_all():
    results = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fetch_status_and_reason, r["url"]): r for r in RESTAURANTS}
        for f in as_completed(futures):
            it = futures[f]
            status, reason = f.result()
            results[it["url"]] = {
                "platform": it["platform"],
                "location": it["location"],
                "brand": brand_of(it["location"]),
                "status": status,
                "reason": reason,
                "checked_at": now_str()
            }
    return results

# ────────────────────────────────────────────────────────────────────────────────
# Web API
# ────────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)
last_results = {}
last_full_check = "—"

@app.route("/api/status")
def api_status():
    items = []
    for it in sorted(RESTAURANTS, key=sort_key):
        r = last_results.get(it["url"])
        if not r:
            items.append({
                "platform": it["platform"],
                "location": it["location"],
                "brand": brand_of(it["location"]),
                "url": it["url"],
                "status": "🟡 Nedetectabil",
                "reason": "Încă nu s-a verificat",
                "checked_at": "—"
            })
        else:
            items.append(r)
    return jsonify({"last_full_check": last_full_check, "items": items})

@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    global last_results, last_full_check
    last_results = check_all()
    last_full_check = now_str()
    return jsonify({"ok": True, "refreshed_at": last_full_check})

@app.route("/")
def index():
    return Response(f"<h2>Status Checker {VERSION}</h2><p>Accesează /api/status pentru JSON sau /api/refresh pentru refresh manual.</p>", mimetype="text/html")

# ────────────────────────────────────────────────────────────────────────────────
# Pornire automată în fundal
# ────────────────────────────────────────────────────────────────────────────────
def background_loop():
    global last_results, last_full_check
    while True:
        last_results = check_all()
        last_full_check = now_str()
        time.sleep(CHECK_INTERVAL_SEC)

threading.Thread(target=background_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
