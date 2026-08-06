"""Title filter (regex veto + fuzzy match) and deduped insert into jobs."""
import re
from datetime import datetime, timedelta, timezone

from rapidfuzz import process, fuzz

from config import TARGET_TITLES, NEGATIVE_PATTERNS
from db import get_db

_VETO_RES = [re.compile(p, re.I) for p in NEGATIVE_PATTERNS]

# Stripped AFTER the veto check, so "senior"/"staff" still trigger vetoes.
STRIP_RE = re.compile(
    r"\b(jr\.?|junior|entry.?level|ii|remote|hybrid|onsite|on.?site|contract)\b"
    r"|\(.*?\)|[-–—/,]",
    re.I,
)


def is_vetoed(raw_title: str) -> bool:
    t = raw_title.lower()
    return any(rx.search(t) for rx in _VETO_RES)


def normalize(title: str) -> str:
    return " ".join(STRIP_RE.sub(" ", title.lower()).split())


def match_title(raw: str):
    """Returns (matched_target, persona, score) or (None, None, score)."""
    if is_vetoed(raw):
        return None, None, 0
    norm = normalize(raw)
    if not norm:
        return None, None, 0
    best, score, _ = process.extractOne(
        norm, TARGET_TITLES.keys(), scorer=fuzz.token_sort_ratio
    )
    if score >= 85:
        return best, TARGET_TITLES[best], score
    # partial_ratio catches a target embedded in a longer title, e.g.
    # "Software Engineer, CUDA Deep Learning Systems"
    best_p, score_p, _ = process.extractOne(
        norm, TARGET_TITLES.keys(), scorer=fuzz.partial_ratio
    )
    if score_p >= 95 and len(best_p) >= 10:
        return best_p, TARGET_TITLES[best_p], score_p
    return None, None, max(score, score_p)


REL_TIME_RE = re.compile(r"(\d+)\s+(minute|hour|day|week)s?\s+ago", re.I)


def parse_posted(posted: str):
    m = REL_TIME_RE.search(posted or "")
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    delta = {"minute": timedelta(minutes=n), "hour": timedelta(hours=n),
             "day": timedelta(days=n), "week": timedelta(weeks=n)}[unit]
    return (datetime.now(timezone.utc) - delta).isoformat()


def store_cards(cards: list[dict]) -> dict:
    """Filter, dedupe-insert. Returns counts + newly inserted matched rows."""
    db = get_db()
    matched_rows = []
    for c in cards:
        target, persona, score = match_title(c["title"])
        row = {
            "source": "linkedin",
            "external_id": c["external_id"],
            "title": c["title"],
            "company": c["company"],
            "location": c["location"],
            "remote": "remote" in (c.get("location") or "").lower(),
            "posted_at": parse_posted(c.get("posted", "")),
            "persona": persona,
            "matched": persona is not None,
            "match_score": score,
            "apply_lane": "easy_apply" if c.get("easy_apply") else None,
        }
        res = (
            db.table("jobs")
            .upsert(row, on_conflict="source,external_id", ignore_duplicates=True)
            .execute()
        )
        if res.data and persona:  # data non-empty only when actually inserted
            matched_rows.append({**row, "link": c["link"]})
    return {
        "found": len(cards),
        "new_matched": matched_rows,
    }
