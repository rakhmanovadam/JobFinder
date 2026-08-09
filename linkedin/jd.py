"""Fetch full job descriptions from LinkedIn's public guest endpoint.

No login, no browser: the guest job-posting fragment is plain HTML.
"""
import html
import re

import httpx

from config import PROXY_URL

GUEST_JD = (
    "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\n{3,}")


def _strip_html(fragment: str) -> str:
    text = re.sub(r"<(br|/p|/li|/div)[^>]*>", "\n", fragment, flags=re.I)
    text = TAG_RE.sub("", text)
    text = html.unescape(text)
    lines = [ln.strip() for ln in text.splitlines()]
    return WS_RE.sub("\n\n", "\n".join(ln for ln in lines if ln))


def jd_is_easy_apply(job_id: str, timeout: float = 20.0) -> bool:
    """Does the guest job page advertise Easy Apply?

    Card-level detection only saw 4 in a thousand, because the authenticated
    search list shows "Easy Apply" on the detail pane rather than on the card.
    The guest posting page states it plainly, and costs nothing extra.
    """
    try:
        r = httpx.get(
            f"https://www.linkedin.com/jobs/view/{job_id}/",
            headers=HEADERS, timeout=timeout, follow_redirects=True,
            proxy=PROXY_URL,
        )
        if r.status_code != 200:
            return False
        return bool(re.search(r"easy\s*apply", r.text, re.I))
    except Exception:
        return False


def fetch_jd(job_id: str, timeout: float = 20.0) -> str | None:
    """Returns the job description text, or None if unavailable."""
    try:
        # Route through the same proxy the browser uses — otherwise this,
        # the highest-volume LinkedIn caller, egresses from the host IP.
        r = httpx.get(
            GUEST_JD.format(job_id=job_id),
            headers=HEADERS, timeout=timeout, follow_redirects=True,
            proxy=PROXY_URL,
        )
        if r.status_code != 200:
            return None
        m = re.search(
            r'<div class="[^"]*show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>',
            r.text, re.S,
        )
        body = m.group(1) if m else r.text
        text = _strip_html(body)
        return text or None
    except Exception:
        return None


if __name__ == "__main__":
    import sys

    jd = fetch_jd(sys.argv[1])
    print((jd or "NO JD")[:1500])
