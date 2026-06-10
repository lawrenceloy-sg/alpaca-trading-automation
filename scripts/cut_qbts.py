"""
QBTS Cut — market sell all QBTS shares at open.
Runs daily at 9:35 AM ET until QBTS is gone.
"""
import os, sys, requests

API_KEY    = os.environ["ALPACA_API_KEY"]
API_SECRET = os.environ["ALPACA_API_SECRET"]
BASE_URL   = "https://api.alpaca.markets/v2"
HEADERS    = {"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": API_SECRET}

def get(path, params=None):
    r = requests.get(BASE_URL + path, headers=HEADERS, params=params)
    r.raise_for_status()
    return r.json()

def post(path, body):
    r = requests.post(BASE_URL + path, headers=HEADERS, json=body)
    r.raise_for_status()
    return r.json()

clock = get("/clock")
if not clock["is_open"]:
    print("Market closed — exiting")
    sys.exit(0)

try:
    pos = requests.get(BASE_URL + "/positions/QBTS", headers=HEADERS)
    if pos.status_code == 404:
        print("QBTS position not found — already closed or never existed")
        sys.exit(0)
    pos.raise_for_status()
    pos = pos.json()
except requests.HTTPError as e:
    print(f"Error fetching QBTS: {e}")
    sys.exit(1)

qty = pos["qty"]
px  = pos["current_price"]
pl  = round(float(pos["unrealized_pl"]), 2)
print(f"QBTS found: qty={qty} px=${px} unrealized_pl=${pl}")
print("Placing market sell...")

order = post("/orders", {
    "symbol": "QBTS",
    "qty": qty,
    "side": "sell",
    "type": "market",
    "time_in_force": "day"
})
print(f"SELL ORDER PLACED — id={order['id']} status={order['status']}")
