import os
import threading
import time
from datetime import datetime
import requests
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

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

STATUS = {}
LAST_CHECK = None

def check_restaurant_status():
    global STATUS, LAST_CHECK
    new_status = {}
    for platform, restaurants in RESTAURANTS.items():
        new_status[platform] = []
        for r in restaurants:
            try:
                resp = requests.get(r["url"], timeout=10)
                if resp.status_code == 200:
                    st = "🟢 Deschis"
                else:
                    st = f"🔴 Închis ({resp.status_code})"
            except Exception as e:
                st = f"❌ Eroare: {e}"
            new_status[platform].append({
                "name": r["name"],
                "url": r["url"],
                "status": st,
                "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
    STATUS = new_status
    LAST_CHECK = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def background_checker():
    while True:
        check_restaurant_status()
        time.sleep(60)

threading.Thread(target=background_checker, daemon=True).start()

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Status Restaurante</title>
    <meta http-equiv="refresh" content="30">
    <style>
        body { font-family: Arial; background: #111; color: #eee; text-align: center; }
        table { margin: 20px auto; border-collapse: collapse; width: 80%; }
        th, td { border: 1px solid #333; padding: 8px; }
        th { background: #222; }
        a { color: #4fa3ff; text-decoration: none; }
        .ok { color: #00ff00; }
        .err { color: #ff5555; }
    </style>
</head>
<body>
<h2>📊 Status restaurante (Wolt / Bolt)</h2>
<p>Ultima verificare completă: {{ last_check or "în curs…" }} • Auto-refresh la 30s • Interval verificare: 60s</p>
{% for platform, restaurants in status.items() %}
<h3>{{ platform }}</h3>
<table>
<tr><th>Restaurant</th><th>Status</th><th>Verificat la</th></tr>
{% for r in restaurants %}
<tr>
<td><a href="{{ r.url }}" target="_blank">{{ r.name }}</a></td>
<td>{{ r.status }}</td>
<td>{{ r.checked_at }}</td>
</tr>
{% endfor %}
</table>
{% endfor %}
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE, status=STATUS, last_check=LAST_CHECK)

@app.route("/api/status")
def api_status():
    return jsonify({
        "last_check": LAST_CHECK,
        "status": STATUS
    })

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
