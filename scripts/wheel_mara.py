"""
MARA Wheel Monitor — runs every 15 min during market hours.
Identical logic to wheel_snap.py but for MARA with $1,100 max collateral.
Capital gate: only opens new cycle when free buying power >= $1,100.
"""
import os, sys, math, requests
from datetime import date, timedelta

API_KEY    = os.environ["ALPACA_API_KEY"]
API_SECRET = os.environ["ALPACA_API_SECRET"]
BASE_URL   = "https://api.alpaca.markets/v2"
DATA_URL   = "https://data.alpaca.markets/v2"
HEADERS    = {"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": API_SECRET}

SYMBOL         = "MARA"
MAX_CASH       = 1100
DTE_MIN        = 5
DTE_MAX        = 9
DELTA_MIN      = 0.20
DELTA_MAX      = 0.30
CLOSE_PCT      = 0.50
CALL_MIN_ABOVE = 1.08
RISK_FREE      = 0.045
HV_DAYS        = 30

def get(path, params=None, base=BASE_URL):
    r = requests.get(base + path, headers=HEADERS, params=params)
    r.raise_for_status()
    return r.json()

def post(path, body):
    r = requests.post(BASE_URL + path, headers=HEADERS, json=body)
    r.raise_for_status()
    return r.json()

def norm_cdf(x):
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

def bs_put_delta(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0: return -1.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    return norm_cdf(d1) - 1.0

def bs_put_price(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0: return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)

def bs_call_price(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0: return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)

def get_hv(sym, days=HV_DAYS):
    start = (date.today() - timedelta(days=days + 15)).isoformat()
    resp  = get(f"/stocks/{sym}/bars", {"timeframe": "1Day", "start": start, "limit": days + 15, "feed": "iex"}, base=DATA_URL)
    bars  = resp.get("bars", [])
    if len(bars) < 5:
        print("  Insufficient bar data — using default HV 80%")
        return 0.80
    closes  = [b["c"] for b in bars[-days:]]
    returns = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
    mean    = sum(returns) / len(returns)
    var     = sum((r - mean)**2 for r in returns) / max(len(returns) - 1, 1)
    hv      = math.sqrt(var) * math.sqrt(252)
    print(f"  {sym} HV({days}d) = {hv*100:.1f}%")
    return hv

def get_stock_price(sym):
    resp = get(f"/stocks/{sym}/quotes/latest", {"feed": "iex"}, base=DATA_URL)
    return float(resp["quote"]["ap"] or resp["quote"]["bp"] or 0)

def find_best_put(sym, S, hv):
    today     = date.today()
    date_from = (today + timedelta(days=DTE_MIN)).isoformat()
    date_to   = (today + timedelta(days=DTE_MAX + 2)).isoformat()
    resp = get("/options/contracts", {
        "underlying_symbols": sym, "type": "put",
        "expiration_date_gte": date_from, "expiration_date_lte": date_to, "limit": 200
    })
    contracts = resp.get("option_contracts", [])
    if not contracts:
        print(f"  No put contracts found for {sym}")
        return None

    mid_delta = (DELTA_MIN + DELTA_MAX) / 2.0
    best = None; best_diff = 999.0
    fallback = None; fallback_diff = 999.0

    for c in contracts:
        K = float(c["strike_price"])
        if K * 100 > MAX_CASH: continue
        exp = date.fromisoformat(c["expiration_date"])
        T = (exp - today).days / 365.0
        if T <= 0: continue
        delta = abs(bs_put_delta(S, K, T, RISK_FREE, hv))
        theo  = bs_put_price(S, K, T, RISK_FREE, hv)
        dte   = (exp - today).days

        if DELTA_MIN <= delta <= DELTA_MAX:
            diff = abs(delta - mid_delta)
            if diff < best_diff:
                best_diff = diff
                best = {"symbol": c["symbol"], "strike": K, "expiration": c["expiration_date"],
                        "dte": dte, "delta": round(delta, 4), "theo": round(theo, 4), "cash": K*100}

        diff2 = abs(delta - mid_delta)
        if diff2 < fallback_diff and delta >= 0.05:
            fallback_diff = diff2
            fallback = {"symbol": c["symbol"], "strike": K, "expiration": c["expiration_date"],
                        "dte": dte, "delta": round(delta, 4), "theo": round(theo, 4), "cash": K*100}

    if best: return best
    if fallback:
        print(f"  No exact delta match — using closest: strike={fallback['strike']} delta={fallback['delta']}")
        return fallback
    print(f"  No suitable put within budget ${MAX_CASH}")
    return None

def find_best_call(sym, S, cost_basis, hv):
    today     = date.today()
    date_from = (today + timedelta(days=DTE_MIN)).isoformat()
    date_to   = (today + timedelta(days=DTE_MAX + 2)).isoformat()
    min_strike = round(cost_basis * CALL_MIN_ABOVE, 2)
    resp = get("/options/contracts", {
        "underlying_symbols": sym, "type": "call",
        "expiration_date_gte": date_from, "expiration_date_lte": date_to, "limit": 200
    })
    contracts = [c for c in resp.get("option_contracts", []) if float(c["strike_price"]) >= min_strike]
    if not contracts:
        print(f"  No call strike >= ${min_strike}")
        return None
    contracts.sort(key=lambda c: float(c["strike_price"]))
    c = contracts[0]; K = float(c["strike_price"])
    exp = date.fromisoformat(c["expiration_date"])
    T = (exp - today).days / 365.0
    theo = bs_call_price(S, K, T, RISK_FREE, hv)
    return {"symbol": c["symbol"], "strike": K, "expiration": c["expiration_date"],
            "dte": (exp - today).days, "theo": round(theo, 4)}

def get_positions():
    return get("/positions")

def get_open_orders(sym_filter=None):
    orders = get("/orders", {"status": "open", "limit": 50})
    if sym_filter:
        orders = [o for o in orders if sym_filter in o.get("symbol", "")]
    return orders

def place_order(symbol, qty, side, limit_price):
    body = {"symbol": symbol, "qty": str(qty), "side": side,
            "type": "limit", "limit_price": str(round(limit_price, 2)), "time_in_force": "day"}
    o = post("/orders", body)
    print(f"  ORDER {side.upper()} {symbol} qty={qty} limit=${limit_price:.2f} → id={o['id'][:8]} status={o['status']}")
    return o

# ── main ──────────────────────────────────────────────────────────
print(f"=== MARA Wheel Monitor ===")

clock = get("/clock")
if not clock["is_open"]:
    print("Market closed — exiting")
    sys.exit(0)

positions   = get_positions()
open_orders = get_open_orders(SYMBOL)

snap_put = next((p for p in positions
                 if p["symbol"].startswith(SYMBOL) and len(p["symbol"]) > 10
                 and "P" in p["symbol"] and float(p["qty"]) < 0), None)

mara_shares = next((p for p in positions
                    if p["symbol"] == SYMBOL and int(float(p["qty"])) >= 100), None)

S = get_stock_price(SYMBOL)
print(f"{SYMBOL} price: ${S:.2f}")

# ── Stage 2: covered call ─────────────────────────────────────────
if mara_shares:
    qty_shares = int(float(mara_shares["qty"]))
    cost_basis = float(mara_shares["avg_entry_price"])
    print(f"STAGE 2: {qty_shares} shares, cost_basis=${cost_basis:.4f}")
    open_calls = [o for o in open_orders if "C" in o.get("symbol", "") and o.get("side") == "sell"]
    if open_calls:
        print(f"  Call already open: {open_calls[0]['symbol']} — holding")
        sys.exit(0)
    hv = get_hv(SYMBOL)
    contract = find_best_call(SYMBOL, S, cost_basis, hv)
    if not contract: sys.exit(0)
    limit = max(round(contract["theo"] * 0.90, 2), 0.01)
    print(f"  CALL: {contract['symbol']} strike={contract['strike']} exp={contract['expiration']} DTE={contract['dte']} theo={contract['theo']} limit={limit}")
    place_order(contract["symbol"], 1, "sell", limit)
    sys.exit(0)

# ── Stage 1: put cycle ────────────────────────────────────────────
if snap_put:
    sym        = snap_put["symbol"]
    avg_entry  = float(snap_put["avg_entry_price"])
    current_px = float(snap_put["current_price"])
    profit_pct = (avg_entry - current_px) / avg_entry * 100 if avg_entry > 0 else 0
    profit_usd = round((avg_entry - current_px) * 100, 2)
    exp_str    = sym[len(SYMBOL):len(SYMBOL)+6]
    exp_date   = date(2000 + int(exp_str[:2]), int(exp_str[2:4]), int(exp_str[4:6]))
    dte        = (exp_date - date.today()).days
    print(f"STAGE 1 OPEN: {sym} entry=${avg_entry:.4f} current=${current_px:.4f} profit={profit_pct:.1f}% (${profit_usd}) DTE={dte}")
    if profit_pct >= CLOSE_PCT * 100:
        pending_btc = [o for o in open_orders if o.get("symbol") == sym and o.get("side") == "buy"]
        if pending_btc:
            print("  BTC order already pending — waiting for fill")
        else:
            close_price = max(round(current_px, 2), 0.01)
            print(f"  50% profit reached — placing BTC @ ${close_price}")
            place_order(sym, 1, "buy", close_price)
    else:
        target = round(avg_entry * CLOSE_PCT, 2)
        print(f"  Holding — target close ${target:.2f} (need {50 - profit_pct:.1f}% more)")
    sys.exit(0)

# ── No position: capital gate then open new cycle ─────────────────
pending_sell = [o for o in open_orders if o.get("side") == "sell" and o.get("symbol", "").startswith(SYMBOL)]
if pending_sell:
    print(f"  Put sell order pending fill: {pending_sell[0]['symbol']} — waiting")
    sys.exit(0)

acct = get("/account")
bp   = float(acct["buying_power"])
print(f"No open MARA position. Buying power: ${bp:.2f} (need ${MAX_CASH})")

if bp < MAX_CASH:
    print(f"  Insufficient BP — waiting for capital (SNAP cycle to close or QBTS to be cut)")
    sys.exit(0)

print("Capital available — opening MARA wheel cycle")
hv       = get_hv(SYMBOL)
contract = find_best_put(SYMBOL, S, hv)
if not contract: sys.exit(0)

limit = max(round(contract["theo"] * 0.90, 2), 0.01)
print(f"  PUT: {contract['symbol']} strike={contract['strike']} exp={contract['expiration']} DTE={contract['dte']} delta={contract['delta']} theo={contract['theo']} limit={limit} cash=${contract['cash']}")
place_order(contract["symbol"], 1, "sell", limit)
