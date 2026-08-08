"""Classify a posting's workplace type from its job description.

LinkedIn's logged-out search accepts f_WT (remote / hybrid / on-site) and
silently ignores it — identical result sets for every value — so the filter has
to be rebuilt on our side. The card HTML carries no workplace marker either,
and only ~3% of stored locations have a "(Remote)" suffix, which leaves the JD
text as the one reliable signal.

Deliberately conservative: a posting is only called remote on explicit evidence,
and any on-site/hybrid phrasing wins over a generic "remote" mention, because
"remote" appears in plenty of hybrid listings ("2 days remote"). Ambiguous
postings return "unknown" rather than being guessed either way.
"""
import re

REMOTE = re.compile(
    r"(?i)\b("
    r"fully[- ]remote|100%\s*remote|remote[- ]first|remote[- ]only"
    r"|work from home|work[- ]from[- ]anywhere|remotely from anywhere"
    r"|this (?:is|role is) (?:a )?remote"
    r"|position is remote|role is remote|remote position|remote role"
    r")\b"
)

ONSITE = re.compile(
    r"(?i)\b("
    r"on-?site|in-?office|in[- ]person"
    r"|hybrid"
    r"|\d+\s*days?\s*(?:per|a)\s*week\s*(?:in|at|from)"
    r"|days? in (?:the )?office"
    r"|commut(?:e|ing) (?:to|distance)"
    r"|relocat(?:e|ion) (?:to|is required)"
    r")\b"
)

# Durham-area on-site is acceptable to this user; anywhere else is not.
LOCAL = re.compile(
    r"(?i)(durham|raleigh|chapel hill|research triangle|\brtp\b|triangle area"
    r"|north carolina|,\s*nc\b)"
)


def classify(jd_text: str | None, location: str | None = None) -> str:
    """Returns 'remote' | 'onsite' | 'unknown'.

    'onsite' covers hybrid too — both require physical presence, which is the
    distinction that actually matters here.
    """
    loc = location or ""
    # An explicit "(Remote)" suffix on the location is LinkedIn's own label and
    # beats anything inferred from prose.
    if re.search(r"\(\s*remote\s*\)\s*$", loc, re.I):
        return "remote"

    jd = jd_text or ""
    if not jd.strip():
        return "unknown"

    onsite_hit = ONSITE.search(jd)
    remote_hit = REMOTE.search(jd)

    # On-site phrasing wins: "remote" shows up inside plenty of hybrid posts.
    if onsite_hit:
        return "onsite"
    if remote_hit:
        return "remote"
    return "unknown"


def is_acceptable(jd_text: str | None, location: str | None,
                  allow_unknown: bool = True) -> tuple[bool, str]:
    """Should this job proceed to tailoring? Returns (ok, reason).

    Durham-area on-site is kept — that's the one place the user will commute to.
    'unknown' is kept by default and surfaced on the card, since dropping every
    posting that fails to state its arrangement would discard most of them.
    """
    kind = classify(jd_text, location)
    local = bool(LOCAL.search(location or "")) or bool(LOCAL.search((jd_text or "")[:2000]))

    if kind == "remote":
        return True, "remote"
    if kind == "onsite":
        if local:
            return True, "on-site (Durham area)"
        return False, "on-site/hybrid outside the Durham area"
    return (allow_unknown, "workplace type not stated") if allow_unknown else (
        False, "workplace type not stated"
    )
