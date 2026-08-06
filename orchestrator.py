"""PROCESS 1 — one drip-sweep cycle: search each keyword 5-10 min apart,
store + notify per keyword as matches appear, log the full pass at the end.

Run:  python -m orchestrator            (respects night thinning)
      python -m orchestrator --force    (ignores night thinning; test runs)
      python -m orchestrator --fast     (short gaps; manual spike/testing)

Scheduled every 3 hours (8 slots/day). A full pass takes ~75-110 min, so the
Mac must stay awake through it (scheduler wraps with caffeinate). Overnight
slots (12am-7am local) run with 30% probability. A lockfile prevents
overlapping sessions on the shared browser profile.
"""
import fcntl
import json
import random
import sys
from datetime import datetime
from pathlib import Path

from db import get_db
from linkedin.discovery import sweep
from filter import store_cards
from tg.notify import send, alert

LOCK_PATH = Path(__file__).resolve().parent / ".sweep.lock"

NIGHT_HOURS = range(0, 7)        # local hours treated as "asleep"
NIGHT_RUN_PROBABILITY = 0.30


def acquire_lock():
    f = open(LOCK_PATH, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except BlockingIOError:
        return None


def notify_matches(keyword: str, rows: list[dict]):
    lines = [f"🔎 <b>{keyword}</b> — {len(rows)} new match(es)"]
    for r in rows:
        lines.append(
            f"\n📌 <b>{r['title']}</b> @ {r['company']}\n"
            f"{r['location']} · score {r['match_score']:.0f} · {r['persona']}\n"
            f"{r['link']}"
        )
    buf = ""
    for line in lines:
        if len(buf) + len(line) > 3800:
            send(buf)
            buf = line
        else:
            buf += ("\n" if buf else "") + line
    if buf:
        send(buf)


def run_cycle(force: bool = False, fast: bool = False):
    hour = datetime.now().hour
    if not force and hour in NIGHT_HOURS and random.random() > NIGHT_RUN_PROBABILITY:
        print(f"night slot ({hour}:00) — skipped this time")
        return

    lock = acquire_lock()
    if lock is None:
        print("another sweep is running — exiting")
        return

    db = get_db()
    paused = db.table("control").select("value").eq("key", "paused").execute()
    if paused.data and paused.data[0]["value"] is True:
        print("paused — skipping cycle")
        return

    total_found, total_new, total_unfiltered = 0, [], 0
    kwargs = {"gap": (12, 35)} if fast else {}
    # Authenticated: only the logged-in SERP honours f_WT (remote / on-site),
    # which the guest endpoint accepts and ignores. sweep() degrades to the
    # guest shape by itself if the session drops, and marks those cards so
    # store_cards refuses to promote them.
    for keyword, cards in sweep(guest=False, **kwargs):
        result = store_cards(cards)
        total_found += result["found"]
        total_unfiltered += result.get("unfiltered_skipped", 0)
        new = result["new_matched"]
        total_new.extend(new)
        if new:
            notify_matches(keyword, new)
        print(f"  {keyword}: {result['found']} cards, {len(new)} new matches")

    msg = (
        f"✅ Sweep done · scanned {total_found} cards · "
        f"{len(total_new)} new match(es) this pass"
    )
    if total_unfiltered:
        msg += (
            f"\n⚠️ {total_unfiltered} title match(es) held back — the LinkedIn "
            "session dropped mid-pass, so remote/on-site filtering did not "
            "apply. Re-run the login to restore it."
        )
    send(msg)
    db.table("run_log").insert(
        {
            "found": total_found,
            "matched": len(total_new),
            "drafted": 0,
            "applied": 0,
            "failed": 0,
            "notes": f"new: {json.dumps([r['external_id'] for r in total_new])}",
        }
    ).execute()
    print(f"cycle done: found={total_found} new_matched={len(total_new)}")


if __name__ == "__main__":
    try:
        run_cycle(force="--force" in sys.argv, fast="--fast" in sys.argv)
    except Exception as e:
        alert(f"❌ cycle crashed: {type(e).__name__}: {e}")
        raise
