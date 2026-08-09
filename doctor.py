"""Step-by-step pipeline diagnostic.

Walks every stage in dependency order and reports PASS / WARN / FAIL with the
specific cause and what to do about it. Each check is isolated, so one broken
stage never hides the ones after it.

Run:  python -m doctor              (skips slow/paid checks)
      python -m doctor --full       (also tailors a real job: costs ~3¢)
      python -m doctor --stage jd   (one stage by name)
"""
import sys
import time
import traceback

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
_ICON = {PASS: "✅", WARN: "⚠️ ", FAIL: "❌"}

results: list[tuple[str, str, str, str]] = []  # stage, status, detail, fix


def record(stage, status, detail, fix=""):
    results.append((stage, status, detail, fix))
    print(f"{_ICON[status]} {stage:22s} {detail}")
    if fix and status != PASS:
        print(f"   ↳ {fix}")


def step(name):
    """Decorator: a raised exception is a FAIL, never a crashed run."""
    def deco(fn):
        def wrapped(*a, **kw):
            try:
                return fn(*a, **kw)
            except Exception as e:
                record(name, FAIL, f"{type(e).__name__}: {e}",
                       "unexpected — see traceback with --trace")
                if "--trace" in sys.argv:
                    traceback.print_exc()
        wrapped._stage = name
        return wrapped
    return deco


@step("config")
def check_config():
    import config
    missing = [k for k in ("OPENAI_API_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_KEY",
                           "TELEGRAM_BOT_TOKEN") if not getattr(config, k, None)]
    if missing:
        return record("config", FAIL, f"missing: {', '.join(missing)}",
                      "fill them in .env")
    if not config.TELEGRAM_CHAT_ID:
        return record("config", WARN, "TELEGRAM_CHAT_ID unset — nothing will be sent",
                      "set TELEGRAM_CHAT_ID in .env")
    record("config", PASS, f"{len(config.SEARCH_SPECS)} search specs, "
                           f"{len(config.TARGET_TITLES)} target titles")


@step("proxy")
def check_proxy():
    import config
    import httpx
    if not config.PROXY_URL:
        return record("proxy", WARN, "no proxy configured — traffic uses this host's IP",
                      "set PROXY_SERVER in .env if running from a datacenter")
    direct = httpx.get("https://ipv4.webshare.io/", timeout=20).text.strip()
    via = httpx.get("https://ipv4.webshare.io/", proxy=config.PROXY_URL, timeout=25).text.strip()
    if via == direct:
        return record("proxy", FAIL, f"proxy not taking effect (both {direct})",
                      "check PROXY_SERVER/USERNAME/PASSWORD")
    org = httpx.get(f"https://ipinfo.io/{via}/json", timeout=20).json().get("org", "?")
    dc = any(w in org.lower() for w in ("amazon", "google", "microsoft", "leaseweb",
                                        "digitalocean", "ovh", "hetzner", "linode"))
    record("proxy", WARN if dc else PASS, f"{direct} -> {via} ({org})",
           "this is a DATACENTER proxy — LinkedIn treats it like a bare server IP" if dc else "")


@step("database")
def check_db():
    from db import get_db
    db = get_db()
    counts = {}
    for t in ("jobs", "applications", "resume_master", "api_usage", "control",
              "field_answers", "run_log"):
        try:
            counts[t] = db.table(t).select("*", count="exact").limit(1).execute().count
        except Exception as e:
            return record("database", FAIL, f"table '{t}' unreadable: {str(e)[:60]}",
                          "run schema.sql in the Supabase SQL editor")
    if not counts.get("resume_master"):
        return record("database", FAIL, "resume_master is empty — tailoring has no source",
                      "python -m tailor.seed_resume")
    record("database", PASS, " · ".join(f"{k}={v}" for k, v in counts.items()))


@step("linkedin-guest")
def check_guest():
    import httpx
    import config
    r = httpx.get(
        "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
        params={"keywords": "software engineer", "geoId": config.GEO_US, "start": "0"},
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                               "AppleWebKit/537.36 Chrome/126.0 Safari/537.36"},
        proxy=config.PROXY_URL, timeout=25, follow_redirects=True)
    n = r.text.count("base-card")
    if r.status_code != 200 or n == 0:
        return record("linkedin-guest", FAIL, f"http {r.status_code}, {n} cards",
                      "LinkedIn may be rate-limiting this IP; try again later")
    record("linkedin-guest", PASS, f"http 200, ~{n} cards — discovery can run")


@step("linkedin-session")
def check_session():
    from linkedin.discovery import _profile_has_live_session
    if _profile_has_live_session():
        record("linkedin-session", PASS, "li_at present — authenticated features available")
    else:
        record("linkedin-session", WARN,
               "no li_at — guest mode only (no apply-URL resolution)",
               "re-run: python -m linkedin.session (discovery is unaffected)")


@step("jd-fetch")
def check_jd():
    from db import get_db
    from linkedin.jd import fetch_jd
    row = get_db().table("jobs").select("external_id").limit(1).execute().data
    if not row:
        return record("jd-fetch", WARN, "no jobs stored yet — nothing to fetch")
    jd = fetch_jd(row[0]["external_id"])
    if not jd or len(jd) < 200:
        return record("jd-fetch", FAIL, f"got {len(jd or '')} chars",
                      "guest JD endpoint may be blocked for this IP")
    record("jd-fetch", PASS, f"{len(jd)} chars")


@step("render")
def check_render():
    import shutil
    from tailor.render import NODE, SOFFICE
    missing = [n for n, p in (("node", NODE), ("soffice", SOFFICE))
               if not shutil.which(p) and not shutil.os.path.exists(p)]
    if missing:
        return record("render", FAIL, f"not found: {', '.join(missing)}",
                      "apt install nodejs libreoffice-writer (or brew install)")
    from pathlib import Path
    # package.json lives at the repo root, and render.py runs node with cwd=ROOT.
    if not (Path(__file__).parent / "node_modules").exists():
        return record("render", FAIL, "node_modules missing",
                      "npm install (from the repo root)")
    record("render", PASS, "node + soffice + node_modules present")


@step("telegram")
def check_telegram():
    import httpx
    from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    r = httpx.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe", timeout=20)
    if r.status_code != 200 or not r.json().get("ok"):
        return record("telegram", FAIL, f"getMe failed: {r.text[:80]}",
                      "check TELEGRAM_BOT_TOKEN")
    name = r.json()["result"]["username"]
    if "--send" in sys.argv and TELEGRAM_CHAT_ID:
        from tg.notify import send
        send("🩺 doctor: test message")
        return record("telegram", PASS, f"@{name} — test message sent")
    record("telegram", PASS, f"@{name} reachable (add --send to post a test)")


@step("email")
def check_email():
    from mailer import EMAIL_FROM, EMAIL_TO, RESEND_API_KEY
    if not RESEND_API_KEY:
        return record("email", WARN, "RESEND_API_KEY not set — no receipts will send",
                      "add RESEND_API_KEY to .env on the box")
    import httpx
    r = httpx.get("https://api.resend.com/domains",
                  headers={"Authorization": f"Bearer {RESEND_API_KEY}"}, timeout=20)
    if r.status_code == 401:
        return record("email", FAIL, "Resend rejected the key",
                      "check RESEND_API_KEY")
    record("email", PASS, f"{EMAIL_FROM} -> {EMAIL_TO}")


@step("openai")
def check_openai():
    from openai import OpenAI
    from config import OPENAI_API_KEY, TAILOR_MODEL, FIELD_MODEL
    client = OpenAI(api_key=OPENAI_API_KEY)
    ids = {m.id for m in client.models.list()}
    missing = [m for m in (TAILOR_MODEL, FIELD_MODEL) if m not in ids]
    if missing:
        return record("openai", FAIL, f"model(s) unavailable: {', '.join(missing)}",
                      "set TAILOR_MODEL / FIELD_MODEL in .env to models on this account")
    record("openai", PASS, f"{TAILOR_MODEL} + {FIELD_MODEL} available")


@step("tailor")
def check_tailor():
    if "--full" not in sys.argv:
        return record("tailor", WARN, "skipped (costs ~3¢) — rerun with --full")
    import json
    from pathlib import Path
    from db import get_db
    from tailor.openai_client import tailor
    from tailor.validate import validate
    master = json.loads((Path(__file__).parent / "tailor" / "resume_data.json").read_text())
    row = get_db().table("jobs").select("jd_text,persona").not_.is_(
        "jd_text", "null").limit(1).execute().data
    if not row:
        return record("tailor", WARN, "no stored JD to tailor against")
    t0 = time.time()
    out = tailor(row[0]["jd_text"], master, row[0].get("persona") or "swe")
    ok, problems = validate(out, master)
    detail = f"{time.time() - t0:.1f}s, workplace={out.workplace_type}"
    if not ok:
        return record("tailor", FAIL, f"{detail}, validation: {problems[:2]}",
                      "the model invented facts — check TAILOR_SYSTEM_PROMPT")
    record("tailor", PASS, f"{detail}, validation clean")


@step("applications")
def check_applications():
    from collections import Counter
    from db import get_db
    db = get_db()
    apps = db.table("applications").select("status,error").execute().data or []
    if not apps:
        return record("applications", WARN, "no applications yet")
    by = Counter(a["status"] for a in apps)
    failed = [a for a in apps if a["status"] == "failed" and a.get("error")]
    top = Counter(
        (a["error"] or "")[:60] for a in failed
    ).most_common(3)
    detail = " · ".join(f"{k}={v}" for k, v in sorted(by.items()))
    status = WARN if by.get("failed") else PASS
    record("applications", status, detail,
           "top failure causes:\n      " + "\n      ".join(
               f"{c}x {msg}" for msg, c in top) if top else "")


STAGES = [check_config, check_proxy, check_db, check_guest, check_session,
          check_jd, check_render, check_telegram, check_email, check_openai,
          check_tailor, check_applications]


def main():
    only = None
    if "--stage" in sys.argv:
        only = sys.argv[sys.argv.index("--stage") + 1]
    print("JobFinder doctor\n" + "=" * 60)
    for fn in STAGES:
        if only and only not in fn._stage:
            continue
        fn()
    print("=" * 60)
    bad = [r for r in results if r[1] == FAIL]
    warn = [r for r in results if r[1] == WARN]
    print(f"{len(results) - len(bad) - len(warn)} pass · {len(warn)} warn · {len(bad)} fail")
    if bad:
        print("\nBlocking issues, in order:")
        for stage, _, detail, fix in bad:
            print(f"  • {stage}: {detail}")
            if fix:
                print(f"      fix: {fix}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
