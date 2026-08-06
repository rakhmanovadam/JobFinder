"""Load tailor/resume_data.json into the resume_master table.

Run:  python -m tailor.seed_resume
Idempotent: wipes and re-inserts (resume_master is source-of-truth-from-file).
"""
import json
from pathlib import Path

from db import get_db

DATA_PATH = Path(__file__).resolve().parent / "resume_data.json"


def seed():
    data = json.loads(DATA_PATH.read_text())
    db = get_db()
    db.table("resume_master").delete().neq(
        "id", "00000000-0000-0000-0000-000000000000"
    ).execute()

    rows = []

    def add(section, content, tags):
        rows.append({"section": section, "persona": tags, "content": content})

    add("contact", data["contact"], ["all"])
    for e in data["education"]:
        add("education", e, e.get("tags", ["all"]))
    for e in data["experience"]:
        add("experience", e, e["tags"])
    for p in data["projects"]:
        add("project", p, p["tags"])
    for l in data["leadership"]:
        add("leadership", l, l["tags"])
    add("skill", data["skills"], ["all"])
    add("honors", {"items": data["honors"]}, ["all"])
    add("certification", data["certifications"][0], ["biotech", "all"])

    db.table("resume_master").insert(rows).execute()
    print(f"seeded {len(rows)} resume_master rows")


if __name__ == "__main__":
    seed()
