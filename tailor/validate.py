"""Deterministic anti-fabrication validator — the real safety layer.

Prose may be reworded freely; the atoms (orgs, titles, dates, skills, numbers)
must trace back to the source. No LLM involved.
"""
import re


def norm(s: str) -> str:
    return " ".join((s or "").lower().replace("&", "and").split())


NUM_RE = re.compile(r"\d[\d,]*\.?\d*\+?%?")


def extract_numbers(text: str) -> list[str]:
    return [n.strip() for n in NUM_RE.findall(text or "")]


def _num_key(n: str) -> str:
    """Compare numbers by digits only: '110,000+' and '110000' are the same."""
    return re.sub(r"[^\d]", "", n)


def build_index(master: dict) -> dict:
    orgs, titles, dates, skills, numbers = set(), set(), set(), set(), set()

    def ingest(item):
        orgs.add(norm(item.get("org", "")))
        titles.add(norm(item.get("title", "")))
        dates.add(norm(item.get("dates", "")))
        # Years in a date range are real, traceable numbers. Indexing only
        # bullets meant education entries — whose bullets are empty and whose
        # years live in `dates` — had their years treated as invented metrics,
        # so a résumé stating the candidate's own enrollment dates failed
        # validation. That was the single largest cause of failed drafts.
        for field in ("dates", "title"):
            for n in extract_numbers(item.get(field, "")):
                numbers.add(_num_key(n))
        for b in item.get("bullets", []):
            for n in extract_numbers(b):
                numbers.add(_num_key(n))

    for key in ("experience", "projects", "leadership", "education"):
        for item in master.get(key, []):
            ingest(item)

    sk = master.get("skills", {})
    for group in ("technical", "languages", "lab"):
        for s in sk.get(group, []):
            skills.add(norm(s))

    for h in master.get("honors", []):
        for n in extract_numbers(h):
            numbers.add(_num_key(n))

    return {
        "orgs": orgs,
        "titles": titles,
        "dates": dates,
        "skills": skills,
        "numbers": numbers,
    }


def validate(tailored, master: dict) -> tuple[bool, list[str]]:
    """Returns (passed, problems). Any problem = discard the draft."""
    problems: list[str] = []
    idx = build_index(master)

    entries = list(getattr(tailored, "experience", [])) + list(
        getattr(tailored, "projects", [])
    )

    for e in entries:
        if norm(e.org) not in idx["orgs"]:
            problems.append(f"unknown org: {e.org}")
        if norm(e.title) not in idx["titles"]:
            problems.append(f"unknown title: {e.title} (at {e.org})")
        if norm(e.dates) not in idx["dates"]:
            problems.append(f"altered dates: {e.dates} (at {e.org})")
        for b in e.bullets:
            for n in extract_numbers(b):
                if _num_key(n) not in idx["numbers"]:
                    problems.append(f"fabricated metric '{n}' in {e.org}")

    for s in getattr(tailored, "skills", []):
        if norm(s) not in idx["skills"]:
            problems.append(f"unknown skill: {s}")

    # The summary and note are prose, but numbers in them must still trace.
    for field in ("summary", "note"):
        for n in extract_numbers(getattr(tailored, field, "")):
            if _num_key(n) not in idx["numbers"]:
                problems.append(f"fabricated metric '{n}' in {field}")

    return (not problems, problems)
