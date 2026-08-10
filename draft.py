"""Turn matched jobs into approval-ready applications:
fetch JD -> resolve ATS -> tailor -> validate -> render PDF -> Telegram card.

Run:  python -m draft            (processes jobs with no application row yet)
      python -m draft --limit 3
"""
import json
import re
import sys
from pathlib import Path

from db import get_db
from filter import company_blocked
from linkedin.jd import fetch_jd
from linkedin.workplace import LOCAL
from tailor.openai_client import tailor
from tailor.validate import validate
from tailor.render import render
from tg.notify import send_job_card, alert

MASTER = json.loads(
    (Path(__file__).resolve().parent / "tailor" / "resume_data.json").read_text()
)


def safe_stem(job: dict) -> str:
    raw = f"{job['company']}_{job['title']}_{job['external_id']}"
    return re.sub(r"[^A-Za-z0-9_-]+", "_", raw)[:80]


def pending_jobs(limit: int) -> list[dict]:
    db = get_db()
    jobs = (
        db.table("jobs").select("*").eq("matched", True)
        .order("seen_at", desc=True).limit(limit * 4).execute().data
    )
    existing = {
        a["job_id"]
        for a in db.table("applications").select("job_id").execute().data
    }
    # Second line of defence behind store_cards: rows matched before a company
    # entered BLOCKED_COMPANIES must not draft either.
    return [
        j for j in jobs
        if j["id"] not in existing and not company_blocked(j["company"])
    ][:limit]


def draft_one(job: dict) -> str:
    """Returns a status string for logging."""
    db = get_db()

    jd = fetch_jd(job["external_id"])
    if not jd or len(jd) < 200:
        return "no-jd"
    db.table("jobs").update({"jd_text": jd[:20000]}).eq("id", job["id"]).execute()

    from applier.resolver import resolve_ats

    ats = resolve_ats(job["company"], job["title"])
    if ats:
        db.table("jobs").update(
            {"ats_type": ats["ats"], "ats_url": ats["url"], "apply_lane": "ats"}
        ).eq("id", job["id"]).execute()
        job.update(ats_type=ats["ats"], ats_url=ats["url"], apply_lane="ats")
    elif job.get("apply_lane") != "easy_apply":
        # No board API match. Before giving up, check whether it is Easy Apply —
        # that is a lane we can actually complete, and it is far more common
        # than card-level detection suggested.
        from linkedin.jd import jd_is_easy_apply

        lane = "easy_apply" if jd_is_easy_apply(job["external_id"]) else "unresolved"
        db.table("jobs").update({"apply_lane": lane}).eq("id", job["id"]).execute()
        job["apply_lane"] = lane

    tailored = tailor(jd, MASTER, job["persona"], job.get("id"))

    # Workplace gate. LinkedIn's guest search accepts f_WT and ignores it, so
    # "remote" searches return on-site jobs; this is where that filter actually
    # happens. Durham-area on-site is fine — anywhere else is not.
    wt = tailored.workplace_type
    local = bool(LOCAL.search(job.get("location") or "")) or bool(
        LOCAL.search(jd[:2000])
    )
    if wt in ("onsite", "hybrid") and not local:
        db.table("jobs").update({"matched": False}).eq("id", job["id"]).execute()
        db.table("applications").insert(
            {
                "job_id": job["id"],
                "status": "skipped",
                "error": f"{wt} outside Durham — {tailored.workplace_evidence[:160]}",
            }
        ).execute()
        return f"skipped ({wt}, not local)"

    ok, problems = validate(tailored, MASTER)
    if not ok:
        db.table("applications").insert(
            {
                "job_id": job["id"],
                "status": "failed",
                "validation_passed": False,
                "tailored_json": tailored.model_dump(),
                "error": "; ".join(problems[:5]),
            }
        ).execute()
        return f"validation-failed ({len(problems)} problems)"

    payload = tailored.model_dump()
    payload["contact"] = MASTER["contact"]
    payload["education"] = MASTER["education"]
    docx, pdf = render(payload, safe_stem(job))

    app_row = (
        db.table("applications")
        .insert(
            {
                "job_id": job["id"],
                "status": "pending",
                "resume_pdf": str(pdf),
                "resume_docx": str(docx),
                "tailored_json": payload,
                "cover_note": tailored.note,
                "validation_passed": True,
            }
        )
        .execute()
        .data[0]
    )

    msg = send_job_card(job, app_row, tailored.note)
    if msg and msg.get("ok"):
        db.table("applications").update(
            {
                "tg_message_id": msg["result"]["message_id"],
                "tg_chat_id": msg["result"]["chat"]["id"],
            }
        ).eq("id", app_row["id"]).execute()
    return "pending"


def run(limit: int = 5):
    jobs = pending_jobs(limit)
    print(f"drafting {len(jobs)} job(s)")
    for j in jobs:
        try:
            status = draft_one(j)
        except Exception as e:
            status = f"error: {type(e).__name__}: {e}"
            alert(f"⚠️ draft failed for {j['title']} @ {j['company']}: {e}")
        print(f"  {j['company'][:24]:26s} {j['title'][:34]:36s} -> {status}")


if __name__ == "__main__":
    limit = 5
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    run(limit)
