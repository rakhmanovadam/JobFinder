"""End-of-day Telegram summary: what the agent did today and what it cost.

Run from a systemd timer once a day. Reports the local day (midnight to now),
not a rolling 24h, so "today" means what you'd expect.
"""
import sys
from datetime import datetime, timedelta

from db import get_db
from tg.notify import send


def _day_bounds(days_ago: int = 0) -> tuple[str, str, str]:
    now = datetime.now().astimezone()
    start = (now - timedelta(days=days_ago)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat(), start.strftime("%a %b %-d")


def collect(days_ago: int = 0) -> dict:
    db = get_db()
    lo, hi, label = _day_bounds(days_ago)

    jobs = (
        db.table("jobs").select("id,matched")
        .gte("seen_at", lo).lt("seen_at", hi).execute().data or []
    )
    apps = (
        db.table("applications").select("status,applied_at,created_at")
        .gte("created_at", lo).lt("created_at", hi).execute().data or []
    )
    # applied_at can fall on a later day than created_at — count by the act, not the draft.
    applied = (
        db.table("applications").select("id")
        .gte("applied_at", lo).lt("applied_at", hi).execute().data or []
    )
    usage = (
        db.table("api_usage").select("cost_usd,input_tokens,output_tokens,priced,purpose")
        .gte("at", lo).lt("at", hi).execute().data or []
    )

    by_status: dict[str, int] = {}
    for a in apps:
        by_status[a.get("status") or "unknown"] = by_status.get(a.get("status") or "unknown", 0) + 1

    return {
        "label": label,
        "found": len(jobs),
        "matched": sum(1 for j in jobs if j.get("matched")),
        "drafted": len(apps),
        "applied": len(applied),
        "by_status": by_status,
        "cost": sum(float(u.get("cost_usd") or 0) for u in usage),
        "calls": len(usage),
        "unpriced": sum(1 for u in usage if not u.get("priced")),
        "tokens": sum(
            int(u.get("input_tokens") or 0) + int(u.get("output_tokens") or 0) for u in usage
        ),
    }


def format_digest(d: dict) -> str:
    lines = [
        f"📊 <b>Daily summary — {d['label']}</b>",
        "",
        f"Applied: <b>{d['applied']}</b>",
        f"Cost: <b>${d['cost']:.2f}</b>",
        "",
        f"Jobs seen: {d['found']}  ·  matched: {d['matched']}",
        f"Cards drafted: {d['drafted']}",
    ]
    if d["by_status"]:
        breakdown = ", ".join(f"{k} {v}" for k, v in sorted(d["by_status"].items()))
        lines.append(f"Status: {breakdown}")
    lines.append(f"AI calls: {d['calls']}  ·  {d['tokens']:,} tokens")
    if d["unpriced"]:
        lines.append(
            f"⚠️ {d['unpriced']} call(s) from a model with no price set — "
            "cost above is an undercount."
        )
    if d["applied"] == 0 and d["drafted"] > 0:
        lines.append("")
        lines.append("No submissions today — cards are waiting on your ✅.")
    return "\n".join(lines)


def main():
    days_ago = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    d = collect(days_ago)
    text = format_digest(d)
    print(text)
    send(text)


if __name__ == "__main__":
    main()
