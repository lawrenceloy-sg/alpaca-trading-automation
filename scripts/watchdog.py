"""
Watchdog — checks if wheel workflows ran recently during market hours.
Triggers stale wheels, exits 1 on unrecoverable failure (GitHub emails on failure).
"""
import os, sys, requests
from datetime import datetime, timezone

GH_TOKEN    = os.environ["GH_PAT"].lstrip('﻿').strip()
REPO        = "lawrenceloy-sg/alpaca-trading-automation"
GH_HEADERS  = {
    "Authorization": f"Bearer {GH_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28"
}
ALP_HEADERS = {
    "APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
    "APCA-API-SECRET-KEY": os.environ["ALPACA_API_SECRET"]
}

STALE_MIN = 25  # trigger if last run older than this during market hours
WHEELS    = ["wheel_snap.yml", "wheel_mara.yml"]

def market_open():
    r = requests.get("https://api.alpaca.markets/v2/clock", headers=ALP_HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()["is_open"]

def last_run_age_minutes(wf):
    r = requests.get(
        f"https://api.github.com/repos/{REPO}/actions/workflows/{wf}/runs",
        headers=GH_HEADERS, params={"per_page": 1}, timeout=10
    )
    r.raise_for_status()
    runs = r.json().get("workflow_runs", [])
    if not runs:
        return None
    created = datetime.fromisoformat(runs[0]["created_at"].replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - created).total_seconds() / 60

def trigger(wf):
    r = requests.post(
        f"https://api.github.com/repos/{REPO}/actions/workflows/{wf}/dispatches",
        headers=GH_HEADERS, json={"ref": "main"}, timeout=10
    )
    return r.status_code == 204

now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")
print(f"=== Watchdog {now_utc} ===")

if not market_open():
    print("Market closed — no action needed")
    sys.exit(0)

print("Market open — checking wheel freshness")
failed = []

for wf in WHEELS:
    age = last_run_age_minutes(wf)
    if age is None:
        print(f"  {wf}: no runs found — triggering")
        ok = trigger(wf)
        print(f"    trigger {'OK' if ok else 'FAILED'}")
        if not ok:
            failed.append(wf)
    elif age > STALE_MIN:
        print(f"  {wf}: STALE ({age:.0f} min) — triggering")
        ok = trigger(wf)
        print(f"    trigger {'OK' if ok else 'FAILED'}")
        if not ok:
            failed.append(wf)
    else:
        print(f"  {wf}: OK ({age:.0f} min ago)")

if failed:
    print(f"\nFAILED to trigger: {failed}")
    sys.exit(1)  # causes GitHub to email you

print("\nAll wheels healthy")
