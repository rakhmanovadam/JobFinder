"""Resolve a LinkedIn job to the company's real ATS posting (Greenhouse /
Lever / Ashby) using public, unauthenticated board APIs.

Tier 1: companies.yaml hand map.  Tier 2: slug guessing.
Unresolvable -> None, and the job becomes a manual link (never auto-applied).
"""
import re
from functools import lru_cache
from pathlib import Path

import httpx
import yaml
from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parent.parent
COMPANIES = yaml.safe_load((ROOT / "companies.yaml").read_text()) or {}

TIMEOUT = 12.0
TITLE_MATCH_MIN = 80

GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
LEVER = "https://api.lever.co/v0/postings/{token}?mode=json"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{token}"
SMARTRECRUITERS = "https://api.smartrecruiters.com/v1/companies/{token}/postings"
WORKABLE = "https://apply.workable.com/api/v1/widget/accounts/{token}"

PROVIDERS = ("greenhouse", "lever", "ashby", "smartrecruiters", "workable")


def slugify(name: str) -> str:
    s = re.sub(r"[^\w\s-]", "", (name or "").lower())
    return re.sub(r"[\s_]+", "-", s).strip("-")


def slug_candidates(company: str) -> list[str]:
    base = slugify(company)
    if not base:
        return []
    stripped = re.sub(
        r"-(inc|llc|ltd|corp|corporation|company|co|group|technologies|technology|labs|holdings)$",
        "", base,
    )
    cands = [base, stripped, base.replace("-", ""), stripped.replace("-", "")]
    first = base.split("-")[0]
    if len(first) > 3:
        cands.append(first)
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _get(url: str):
    try:
        r = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def _best(postings: list[tuple[str, str]], title: str):
    """postings: [(posting_title, url)] -> best url above the match threshold."""
    best_url, best_score = None, 0
    for ptitle, url in postings:
        score = fuzz.token_sort_ratio(title.lower(), (ptitle or "").lower())
        if score > best_score:
            best_url, best_score = url, score
    return (best_url, best_score) if best_score >= TITLE_MATCH_MIN else (None, best_score)


@lru_cache(maxsize=512)
def _probe(ats: str, token: str) -> tuple:
    """Returns a tuple of (title, url) postings, or () if the board is absent."""
    if ats == "greenhouse":
        data = _get(GREENHOUSE.format(token=token))
        jobs = (data or {}).get("jobs") or []
        return tuple((j.get("title", ""), j.get("absolute_url", "")) for j in jobs)
    if ats == "lever":
        data = _get(LEVER.format(token=token))
        jobs = data if isinstance(data, list) else []
        return tuple((j.get("text", ""), j.get("hostedUrl", "")) for j in jobs)
    if ats == "ashby":
        data = _get(ASHBY.format(token=token))
        jobs = (data or {}).get("jobs") or []
        return tuple((j.get("title", ""), j.get("jobUrl", "")) for j in jobs)
    if ats == "smartrecruiters":
        data = _get(SMARTRECRUITERS.format(token=token))
        jobs = (data or {}).get("content") or []
        return tuple(
            (
                j.get("name", ""),
                f"https://jobs.smartrecruiters.com/{token}/{j.get('id', '')}",
            )
            for j in jobs
        )
    if ats == "workable":
        data = _get(WORKABLE.format(token=token))
        jobs = (data or {}).get("jobs") or []
        return tuple((j.get("title", ""), j.get("url", "")) for j in jobs)
    return ()


def resolve_ats(company: str, title: str) -> dict | None:
    """Returns {'ats': ..., 'token': ..., 'url': ..., 'score': ...} or None."""
    mapped = COMPANIES.get(company) or COMPANIES.get((company or "").strip())
    if mapped:
        postings = _probe(mapped["ats"], mapped["token"])
        url, score = _best(list(postings), title)
        if url:
            return {"ats": mapped["ats"], "token": mapped["token"], "url": url,
                    "score": score}

    for token in slug_candidates(company):
        for ats in PROVIDERS:
            postings = _probe(ats, token)
            if not postings:
                continue
            url, score = _best(list(postings), title)
            if url:
                return {"ats": ats, "token": token, "url": url, "score": score}
    return None


if __name__ == "__main__":
    import sys

    print(resolve_ats(sys.argv[1], sys.argv[2]))
