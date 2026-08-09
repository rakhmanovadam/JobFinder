"""Central config. Loads .env from the project root regardless of cwd."""
import os
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
TAILOR_MODEL = os.environ.get("TAILOR_MODEL", "gpt-5.6-terra")
FIELD_MODEL = os.environ.get("FIELD_MODEL", "gpt-5.6-luna")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

PROFILE_DIR = str(ROOT / os.environ.get("PROFILE_DIR", "li_profile"))

# None on a home IP. On a VPS, set PROXY_SERVER (+ optional user/pass) in .env —
# datacenter ranges get challenged by LinkedIn even for logged-out browsing.
_proxy_server = os.environ.get("PROXY_SERVER", "").strip()
if _proxy_server:
    PROXY = {"server": _proxy_server}
    if os.environ.get("PROXY_USERNAME"):
        PROXY["username"] = os.environ["PROXY_USERNAME"]
        PROXY["password"] = os.environ.get("PROXY_PASSWORD", "")
else:
    PROXY = None

# Same proxy as a URL, for the plain-HTTP callers (httpx) that never touch
# Camoufox. Without this, JD fetches would hit LinkedIn straight from the
# host IP while the browser traffic went through the proxy — the noisier
# half of our LinkedIn traffic arriving from a datacenter.
if PROXY:
    _scheme, _, _hostport = PROXY["server"].partition("://")
    if PROXY.get("username"):
        PROXY_URL = (
            f"{_scheme}://{quote(PROXY['username'], safe='')}:"
            f"{quote(PROXY.get('password', ''), safe='')}@{_hostport}"
        )
    else:
        PROXY_URL = PROXY["server"]
else:
    PROXY_URL = None

# USD per 1M tokens, per model. Set the real rates from your OpenAI dashboard —
# a model absent here is logged with token counts but priced at 0, and the daily
# digest reports how many such calls it saw rather than quietly under-reporting.
def _price(var: str, default: float) -> float:
    return float(os.environ.get(var, default))


MODEL_PRICES = {
    TAILOR_MODEL: {
        "input": _price("TAILOR_PRICE_IN", 1.25),
        "output": _price("TAILOR_PRICE_OUT", 10.0),
    },
    FIELD_MODEL: {
        "input": _price("FIELD_PRICE_IN", 0.25),
        "output": _price("FIELD_PRICE_OUT", 2.0),
    },
}

# Hour to send the end-of-day digest, local time (0-23).
DIGEST_HOUR = int(os.environ.get("DIGEST_HOUR", "21"))

MAX_APPLIES_PER_HOUR = int(os.environ.get("MAX_APPLIES_PER_HOUR", "8"))
MAX_APPLIES_PER_DAY = int(os.environ.get("MAX_APPLIES_PER_DAY", "40"))

# ---------------------------------------------------------------------------
# Search: Raleigh-Durham geo, on-site+remote, entry level, past 24h.
# One keyword per cycle, rotated (index persisted in control table) — keeps
# the anti-ban "one search pass per run" rule while covering every category.
# ---------------------------------------------------------------------------
GEO_US = "103644278"             # United States (remote-anywhere roles)
GEO_DURHAM = "101915197"         # Raleigh-Durham-Chapel Hill area
LI_GEO_ID = GEO_DURHAM           # legacy alias

WT_REMOTE = "2"                  # remote only
WT_ONSITE_REMOTE = "1%2C2"       # on-site OR remote

# Sweep cadence: every 3 hours (8 slots/day, overnight slots mostly skipped).
# Search window is 6h — 2x the cadence — so a missed/aborted sweep loses
# nothing; dedupe eats the overlap.
SWEEP_TPR = "r21600"

# Search scope. _DURHAM_LOCAL is the shape of the link Adam supplied:
# geoId=101915197 with f_WT=1,2. It matches the actual constraint better than
# a nationwide remote-only search does, because it returns BOTH remote roles
# and on-site roles near home, and nothing on-site anywhere else.
#
# _REMOTE_US stays for the roles most likely to be advertised as remote to the
# whole country, so those are not lost by scoping everything to one metro.
_REMOTE_US = {"geo": GEO_US, "wt": WT_REMOTE}
_DURHAM_LOCAL = {"geo": GEO_DURHAM, "wt": WT_ONSITE_REMOTE}

SEARCH_SPECS = [
    {"kw": "entry level software engineer", **_DURHAM_LOCAL},
    {"kw": "entry level software engineer", **_REMOTE_US},
    {"kw": "junior web developer", **_DURHAM_LOCAL},
    {"kw": "qa engineer", **_DURHAM_LOCAL},
    {"kw": "qa engineer", **_REMOTE_US},
    {"kw": "software engineer in test", **_DURHAM_LOCAL},
    {"kw": "ai engineer", **_DURHAM_LOCAL},
    {"kw": "ai engineer", **_REMOTE_US},
    {"kw": "data analyst", **_DURHAM_LOCAL},
    {"kw": "data analyst", **_REMOTE_US},
    {"kw": "learning designer", **_REMOTE_US},
    {"kw": "curriculum developer", **_REMOTE_US},
    {"kw": "social media manager", **_DURHAM_LOCAL},
    {"kw": "social media manager", **_REMOTE_US},
    {"kw": "growth marketing", **_REMOTE_US},
    {"kw": "sales development representative", **_REMOTE_US},
    {"kw": "communications coordinator", **_REMOTE_US},
    {"kw": "research assistant", **_DURHAM_LOCAL},
    {"kw": "laboratory technician", **_DURHAM_LOCAL},
    {"kw": "clinical research intern", **_DURHAM_LOCAL},
    {"kw": "biotech lab assistant", **_DURHAM_LOCAL},
]

SEARCH_KEYWORDS = [s["kw"] for s in SEARCH_SPECS]

# Searches per pass. 5-10 min between each, so the whole list every time would
# run past the 3-hour cadence; the list is shuffled, so 8 slots a day still
# cover everything.
MAX_SPECS_PER_PASS = int(os.environ.get("MAX_SPECS_PER_PASS", "12"))

# LinkedIn pages the result list 25 at a time. Nothing ever requested page two,
# so every search silently threw away everything past the first 25 — a search
# reporting 38 results only ever reached the database as 25.
PAGE_SIZE = 25
# 4 pages = up to 100 per search. The 6h time window rarely holds that many, so
# most searches stop on their own well before the cap.
MAX_PAGES_PER_SEARCH = int(os.environ.get("MAX_PAGES_PER_SEARCH", "4"))


def search_url(
    keyword: str,
    tpr: str = SWEEP_TPR,
    geo: str = GEO_US,
    wt: str = WT_REMOTE,
    start: int = 0,
) -> str:
    """tpr: LinkedIn time-posted filter — r3600 = past hour, r21600 = past 6h.
    start: result offset, in multiples of PAGE_SIZE."""
    url = (
        "https://www.linkedin.com/jobs/search/?f_E=2"
        f"&f_TPR={tpr}&f_WT={wt}&geoId={geo}"
        f"&sortBy=DD&refresh=true&keywords={quote(keyword)}"
    )
    return url if not start else f"{url}&start={start}"

# ---------------------------------------------------------------------------
# Target titles -> persona. Fuzzy-matched (>=85 token_sort, >=95 partial).
# Personas: swe | qa | data_ai | edtech | marketing | sales | biotech | comms
# ---------------------------------------------------------------------------
def _expand(titles: list[str], persona: str) -> dict:
    return {t.lower(): persona for t in titles}


TARGET_TITLES = {
    **_expand([
        "software engineer", "software developer", "frontend developer",
        "frontend engineer", "full stack developer", "full stack engineer",
        "backend developer", "backend engineer", "mobile app developer",
        "ios developer", "android developer", "hybrid app developer",
        "web developer", "web application developer", "javascript developer",
        "typescript developer", "api developer", "api integration engineer",
        "cloud application developer", "ui developer", "ui engineer",
        "devops engineer", "deployment engineer", "platform engineer",
        "software engineering apprentice", "software engineering fellow",
        "software engineering intern", "software developer intern",
    ], "swe"),
    **_expand([
        "software engineer in test", "sdet", "qa engineer", "qa analyst",
        "test automation engineer", "manual qa tester", "qa tester",
        "qa intern", "quality engineering intern", "regression test specialist",
        "mobile app qa tester", "api test engineer", "smoke test analyst",
        "release test analyst", "quality assurance engineer",
    ], "qa"),
    **_expand([
        "ai application developer", "llm application engineer", "ai engineer",
        "prompt engineer", "ai product intern", "machine learning intern",
        "machine learning engineer", "ai automation specialist",
        "ai workflow engineer", "data analyst", "data science intern",
        "analytics engineer", "data engineer", "data engineer intern",
        "web scraping specialist", "data collection specialist",
        "automation engineer", "scripting developer",
        "recommendation systems intern", "personalization intern",
        "growth data analyst",
    ], "data_ai"),
    **_expand([
        "ed tech product developer", "edtech product developer",
        "ed tech startup intern", "founder in residence",
        "curriculum developer", "instructional design assistant",
        "instructional designer", "learning designer",
        "learning experience designer", "lx designer", "learning engineer",
        "educational content developer", "assessment content designer",
        "quiz content designer", "academic tutor", "act tutor", "sat tutor",
        "test prep tutor", "stem camp instructor", "coding instructor",
        "teaching assistant", "education technology support specialist",
        "edtech implementation assistant",
    ], "edtech"),
    **_expand([
        "social media manager", "social media coordinator",
        "growth marketing associate", "growth analyst", "growth hacker",
        "growth engineer", "content creator", "ugc creator",
        "content strategist", "digital marketing specialist",
        "digital marketing intern", "paid media specialist",
        "performance marketing associate", "short form video producer",
        "tiktok strategist", "reels strategist", "video editor",
        "community manager", "brand ambassador", "campus ambassador",
        "influencer marketing assistant", "email marketing specialist",
        "outbound campaign specialist", "marketing automation specialist",
        "seo assistant", "content marketing assistant", "marketing analyst",
        "social media consultant",
    ], "marketing"),
    **_expand([
        "sales development representative", "business development representative",
        "lead generation specialist", "outbound sales associate",
        "ed tech sales associate", "edtech sales associate",
        "partnerships associate", "partnerships intern",
        "account development intern", "customer success associate",
        "sales operations assistant", "demand generation assistant",
    ], "sales"),
    **_expand([
        "biotech lab assistant", "laboratory technician", "lab technician",
        "research assistant", "undergraduate research fellow",
        "clinical research intern", "clinical trials assistant",
        "life sciences intern", "biomanufacturing technician",
        "quality control technician", "lab operations assistant",
        "bioinformatics intern", "science communicator",
        "science content creator", "stem outreach assistant",
    ], "biotech"),
    **_expand([
        "communications coordinator", "communications intern", "copywriter",
        "content writer", "website designer", "webmaster",
        "digital media assistant", "multimedia content producer",
    ], "comms"),
}

# Regex vetoes applied to the RAW lowercase title before any stripping.
# Deliberately narrow: "manager" alone is allowed (social media manager),
# "lead" alone is allowed (lead generation specialist).
NEGATIVE_PATTERNS = [
    r"\bsenior\b", r"\bsr\.?\s", r"\bprincipal\b", r"\bstaff\b",
    r"\bdirector\b", r"\bvp\b", r"\bvice president\b", r"\bhead of\b",
    r"\barchitect\b", r"\bengineering manager\b", r"\bproduct manager\b",
    r"\btech lead\b", r"\bteam lead\b",
    r"\blead (software |)?(engineer|developer|designer|scientist)\b",
    r"\b(iii|iv|v)\b", r"\b[3-9]\+?\s*(-|to)?\s*\d*\s*years\b",
]
