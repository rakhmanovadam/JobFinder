"""Turn a live form into typed fields, then resolve each one:

Layer 1  enumerate DOM -> {label, type, required, options}
Layer 2  deterministic field_map.yaml + learned field_answers cache
Layer 3  AI (cheap model) with a schema whose enum is the form's own options,
         so an invented option is structurally impossible
Layer 4  any REQUIRED field still unresolved -> caller must not submit
"""
import json
import re
from pathlib import Path

import yaml
from openai import OpenAI
from pydantic import BaseModel

from config import OPENAI_API_KEY, FIELD_MODEL
from db import get_db
from usage import record
from voice import NATURAL_VOICE

ROOT = Path(__file__).resolve().parent.parent
PROFILE = yaml.safe_load((ROOT / "profile.yaml").read_text())
FIELD_MAP = yaml.safe_load((ROOT / "field_map.yaml").read_text())
# profile.yaml is logistics (address, work auth, EEO). The actual experience —
# what an essay answer has to be built from — lives in the résumé master.
RESUME = json.loads((ROOT / "tailor" / "resume_data.json").read_text())

client = OpenAI(api_key=OPENAI_API_KEY)

YES = re.compile(r"(?i)^(yes|y|true)$")
NO = re.compile(r"(?i)^(no|n|false)$")


def dig(path: str):
    """profile.yaml lookup by dotted path; None if missing or unset."""
    cur = PROFILE
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def enumerate_fields(page, root: str | None = None) -> list[dict]:
    """Layer 1 — the form as data, not pixels.

    Handles native inputs AND the React comboboxes modern ATSes render as
    `<input type=text role=combobox>` with the options in a popup listbox.

    `root` scopes the scan to one container. LinkedIn's Easy Apply form is a
    modal sitting on top of a full job page, so an unscoped scan would pull in
    the page's own search box and filters alongside the actual questions.
    """
    return page.evaluate(
        """(rootSel) => {
        const SCOPE = rootSel ? document.querySelector(rootSel) : document;
        if (!SCOPE) return [];
        const clean = s => (s || '').replace(/\\s+/g, ' ').replace(/\\*/g, '').trim();

        // Nearest label text WITHOUT swallowing sibling questions: prefer an
        // explicit <label for>, then aria-label, then the closest small wrapper.
        const labelFor = (el) => {
            if (el.id) {
                const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                if (l) return clean(l.innerText);
            }
            const aria = el.getAttribute('aria-label')
                || el.getAttribute('aria-labelledby');
            if (el.getAttribute('aria-labelledby')) {
                const t = document.getElementById(el.getAttribute('aria-labelledby'));
                if (t) return clean(t.innerText);
            }
            if (aria && !el.getAttribute('aria-labelledby')) return clean(aria);

            // Walk up at most 3 levels and take the SHORTEST sensible text —
            // a container holding many questions yields a long blob, which we
            // reject rather than mislabel the field.
            let node = el.parentElement, best = '';
            for (let i = 0; i < 3 && node; i++, node = node.parentElement) {
                const own = clean(node.innerText);
                if (own && own.length < 160 && (!best || own.length < best.length)) {
                    best = own;
                }
            }
            // A custom dropdown renders its own placeholder as the only text
            // inside the wrapper, so the walk above yields "Select..." — the
            // widget's resting state, not the question. Look backwards for the
            // preceding prompt text instead.
            const junk = t => !t || /^(select\\.*|choose\\.*|--.*)$/i.test(t);
            if (junk(best)) {
                let n = el;
                for (let i = 0; i < 4 && n; i++, n = n.parentElement) {
                    let sib = n.previousElementSibling;
                    while (sib) {
                        const t = clean(sib.innerText);
                        if (t && t.length >= 6 && t.length < 160 && !junk(t)) return t;
                        sib = sib.previousElementSibling;
                    }
                }
            }
            return best || clean(el.name || el.placeholder || '');
        };

        // Consent/cookie widgets look exactly like form controls (a labelled
        // group of two short buttons) and sit above the real form. They are
        // never part of an application — reject them by container.
        const inBanner = (el) => !!el.closest(
            '[id*="cookie" i],[class*="cookie" i],[id*="consent" i],'
            + '[class*="consent" i],[id*="onetrust" i],[class*="onetrust" i],'
            + '[class*="gdpr" i],[id*="gdpr" i],[aria-label*="cookie" i]'
        );

        // For any grouped control (radios, button pairs) the enclosing text is
        // the OPTIONS, not the question — "YES NO", "Male Female". Walk up
        // subtracting the option text, and if nothing is left look backwards
        // for the prompt, which is often a sibling rather than an ancestor.
        const questionFor = (start, optTexts) => {
            let n = start;
            for (let i = 0; i < 5 && n; i++, n = n.parentElement) {
                let t = clean(n.innerText);
                if (!t || t.length > 400) continue;
                for (const o of optTexts) t = t.split(o).join(' ');
                t = clean(t);
                if (t.length >= 6) return t.slice(0, 160);
            }
            n = start;
            for (let i = 0; i < 5 && n; i++, n = n.parentElement) {
                let sib = n.previousElementSibling;
                while (sib) {
                    const t = clean(sib.innerText);
                    if (t && t.length >= 6 && t.length < 200) return t.slice(0, 160);
                    sib = sib.previousElementSibling;
                }
            }
            return '';
        };

        const isCombo = el =>
            el.getAttribute('role') === 'combobox'
            || el.getAttribute('aria-haspopup') === 'listbox'
            || el.getAttribute('aria-autocomplete') === 'list'
            || !!el.closest('[class*="select"],[class*="Select"],[class*="dropdown"]');

        const out = [], seen = new Set();

        // Radio groups first. A radio's own <label for> is its OPTION ("Male"),
        // so enumerating radios individually turns every option into its own
        // question — the EEO block came out as fields named "Male" and
        // "Female", which then got answered "Male" and "No". One question per
        // `name`, with the option labels as the choices.
        const radioByName = new Map();
        for (const el of SCOPE.querySelectorAll('input[type="radio"]')) {
            if (el.disabled || inBanner(el) || !el.name) continue;
            if (!radioByName.has(el.name)) radioByName.set(el.name, []);
            radioByName.get(el.name).push(el);
        }
        for (const [name, els] of radioByName) {
            const visible = els.filter(e => e.offsetParent !== null);
            if (!visible.length) continue;
            const optTexts = visible.map(e => labelFor(e)).map(clean);
            const container = visible[0].closest('fieldset')
                || visible[0].parentElement?.parentElement
                || visible[0].parentElement;
            const legend = container?.querySelector?.('legend');
            const label = clean(legend?.innerText || '')
                || questionFor(container, optTexts);
            if (!label) continue;
            const key = `${label}|radio`;
            if (seen.has(key)) continue;
            seen.add(key);
            out.push({
                label: label.slice(0, 160),
                name,
                id: '',
                type: 'radio',
                required: visible.some(e => e.required
                    || e.getAttribute('aria-required') === 'true'),
                options: optTexts.filter(Boolean),
                // The form stores value=, the human reads the label — keep the
                // mapping so the filler can check the right input.
                option_values: Object.fromEntries(
                    visible.map((e, i) => [optTexts[i], e.value])
                ),
            });
        }

        for (const el of SCOPE.querySelectorAll('input, select, textarea')) {
            if (el.type === 'hidden' || el.disabled) continue;
            if (el.type === 'radio') continue;   // handled as groups above
            if (el.offsetParent === null && el.type !== 'file') continue;
            if (inBanner(el)) continue;

            const label = labelFor(el).slice(0, 160);
            const combo = el.tagName !== 'SELECT' && isCombo(el);
            const type = el.tagName === 'SELECT' ? 'select'
                : el.tagName === 'TEXTAREA' ? 'textarea'
                : combo ? 'combobox' : el.type;

            // Dedupe on label+type: ATSes often render a hidden twin per field.
            const key = `${label}|${type}`;
            if (!label || seen.has(key)) continue;
            seen.add(key);

            // Combobox options are read later, one at a time, by
            // hydrate_combobox_options() — reading them here from JS returns
            // whichever listbox happens to be mounted (usually the country
            // list), which would silently mislabel every dropdown.
            const options = el.tagName === 'SELECT'
                ? Array.from(el.options).map(o => clean(o.text))
                    .filter(t => t && !/^select\\.\\.\\.?$/i.test(t))
                : [];

            out.push({
                label,
                name: el.name || el.id || '',
                id: el.id || '',
                type,
                required: el.required || el.getAttribute('aria-required') === 'true',
                options,
            });
        }

        // Button-group questions (Ashby/Greenhouse render Yes/No as a pair of
        // <button>s, which the input/select/textarea sweep above never sees).
        // Tag the container so the filler can find it again without relying on
        // text position.
        let gi = 0;
        const groups = new Set();
        for (const btn of SCOPE.querySelectorAll('button')) {
            if (btn.disabled || btn.offsetParent === null) continue;
            if (inBanner(btn)) continue;
            const t = clean(btn.textContent);
            if (!t || t.length > 24) continue;
            const box = btn.parentElement;
            if (!box || groups.has(box)) continue;

            const kids = Array.from(box.querySelectorAll('button'))
                .filter(b => !b.disabled && b.offsetParent !== null);
            const texts = kids.map(b => clean(b.textContent)).filter(x => x && x.length <= 24);
            // A real choice group: 2-5 short-labelled buttons, all distinct.
            if (texts.length < 2 || texts.length > 5) continue;
            if (new Set(texts).size !== texts.length) continue;

            const label = questionFor(box, texts);
            if (!label) continue;
            const key = `${label}|buttongroup`;
            if (seen.has(key)) continue;
            seen.add(key);
            groups.add(box);

            const gid = 'jf-grp-' + (gi++);
            box.setAttribute('data-jf-group', gid);
            out.push({
                label,
                name: gid,
                id: '',
                type: 'buttongroup',
                required: box.closest('[class*="required"]') !== null
                          || /\\*/.test(label),
                options: texts,
            });
        }
        return out;
    }""",
        root,
    )


def _locate(page, field: dict):
    """Best-effort locator for an enumerated field."""
    if field.get("type") == "buttongroup":
        # enumerate_fields tagged the container with data-jf-group.
        loc = page.locator(f'[data-jf-group="{field["name"]}"]')
        return loc.first if loc.count() else None
    if field.get("id"):
        # ids can start with a digit (Greenhouse uses numeric ids), which is a
        # valid DOM id but an invalid CSS selector — use the attribute form.
        loc = page.locator(f'[id="{field["id"]}"]')
        if loc.count():
            return loc.first
    if field.get("name"):
        loc = page.locator(f'[name="{field["name"]}"]')
        if loc.count():
            return loc.first
    return None


def hydrate_combobox_options(page, fields: list[dict]) -> None:
    """Open each combobox individually and read ITS listbox.

    Doing this per-field is the only reliable way: several ATSes keep one
    listbox mounted at a time, so a bulk read returns the wrong options for
    every field but the first.
    """
    for f in fields:
        if f["type"] != "combobox" or f["options"]:
            continue
        loc = _locate(page, f)
        if loc is None:
            continue
        try:
            loc.scroll_into_view_if_needed(timeout=3000)
            loc.click(timeout=3000)
            page.wait_for_timeout(400)
            opts = page.evaluate(
                """(sel) => {
                    const el = sel.id ? document.getElementById(sel.id)
                        : document.querySelector(`[name="${sel.name}"]`);
                    if (!el) return [];
                    const id = el.getAttribute('aria-controls')
                        || el.getAttribute('aria-owns');
                    let list = id ? document.getElementById(id) : null;
                    if (!list) {
                        // fall back to the visible listbox nearest this input
                        const boxes = Array.from(
                            document.querySelectorAll('[role="listbox"], ul[class*="menu"]')
                        ).filter(b => b.offsetParent !== null);
                        const r = el.getBoundingClientRect();
                        let bestD = 1e9;
                        for (const b of boxes) {
                            const br = b.getBoundingClientRect();
                            const d = Math.abs(br.top - r.bottom) + Math.abs(br.left - r.left);
                            if (d < bestD) { bestD = d; list = b; }
                        }
                    }
                    if (!list) return [];
                    return [...new Set(
                        Array.from(list.querySelectorAll('[role="option"], li'))
                            .map(o => (o.innerText || '').replace(/\\s+/g, ' ').trim())
                            .filter(Boolean)
                    )].slice(0, 60);
                }""",
                {"id": f.get("id", ""), "name": f.get("name", "")},
            )
            f["options"] = opts
            page.keyboard.press("Escape")
            page.wait_for_timeout(150)
        except Exception:
            continue


def _never_auto(label: str) -> bool:
    return any(re.search(p, label) for p in FIELD_MAP.get("never_auto", []))


def _is_essay(label: str) -> bool:
    """Open prose: nothing to map to, so the mapping lanes must skip it and
    the writing lane must take it."""
    return any(re.search(p, label) for p in FIELD_MAP.get("essay_fields", []))


def _match_bool_to_options(value: bool, options: list[str]) -> str | None:
    for o in options:
        if value and YES.match(o.strip()):
            return o
        if not value and NO.match(o.strip()):
            return o
    return None


def cached_answer(field: dict):
    r = (
        get_db().table("field_answers").select("answer")
        .eq("field_label", field["label"]).eq("field_type", field["type"]).execute()
    )
    return r.data[0]["answer"] if r.data else None


def _cacheable(field: dict) -> bool:
    """The cache is keyed by label, so a bad label poisons it permanently and
    silently — the stored answer then wins over field_map on every later form.
    Real questions are short-ish and are not just the option text.
    """
    label = (field.get("label") or "").strip()
    if not (3 <= len(label) <= 120):
        return False
    if any(label.strip().lower() == str(o).strip().lower()
           for o in field.get("options") or []):
        return False
    return True


def remember_answer(field: dict, answer, source: str):
    if not _cacheable(field):
        return
    try:
        get_db().table("field_answers").upsert(
            {
                "field_label": field["label"],
                "field_type": field["type"],
                "answer": answer,
                "source": source,
            },
            on_conflict="field_label,field_type",
        ).execute()
    except Exception:
        pass


def deterministic(field: dict):
    """Layer 2 — returns (value, source) or (None, None)."""
    label = field["label"]
    if _never_auto(label) or _is_essay(label):
        return None, None

    for rule in FIELD_MAP.get("boolean_fields", []):
        if re.search(rule["pattern"], label):
            val = dig(rule["path"])
            if val is None:
                return None, None
            if field["options"]:
                return _match_bool_to_options(bool(val), field["options"]), "field_map"
            return ("Yes" if val else "No"), "field_map"

    for rule in FIELD_MAP.get("choice_fields", []):
        if re.search(rule["pattern"], label):
            val = dig(rule["path"])
            if val is None:
                return None, None
            for o in field["options"]:
                if o.strip().lower() == str(val).strip().lower():
                    return o, "field_map"
            return None, None

    for rule in FIELD_MAP.get("text_fields", []):
        if re.search(rule["pattern"], label):
            val = dig(rule["path"])
            return (str(val), "field_map") if val is not None else (None, None)

    return None, None


class Choice(BaseModel):
    value: str
    confident: bool


class Mapped(BaseModel):
    value: str | None
    confident: bool


def ai_resolve(field: dict):
    """Layer 3 — constrained resolution. Returns (value, source) or (None, None)."""
    if _never_auto(field["label"]) or _is_essay(field["label"]):
        return None, None

    facts = {
        k: v for k, v in PROFILE.items() if k not in ("eeo",) and v is not None
    }
    base = (
        "You map a job-application form field to the candidate's real data.\n"
        "NEVER invent a value. If the candidate's data does not clearly answer "
        "the field, set confident=false.\n\n"
        f"CANDIDATE DATA:\n{facts}\n\n"
        f"FIELD LABEL: {field['label']}\nTYPE: {field['type']}"
    )

    try:
        if field["options"]:
            # enum restricted to this form's own options -> hallucination is
            # structurally impossible
            schema = {
                "type": "object",
                "properties": {
                    "value": {"type": "string", "enum": field["options"]},
                    "confident": {"type": "boolean"},
                },
                "required": ["value", "confident"],
                "additionalProperties": False,
            }
            r = client.responses.create(
                model=FIELD_MODEL,
                input=[{"role": "user", "content": base}],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "choice",
                        "schema": schema,
                        "strict": True,
                    }
                },
            )
            record(FIELD_MODEL, r, "field_choice")
            import json

            out = json.loads(r.output_text)
            return (out["value"], "ai") if out.get("confident") else (None, None)

        r = client.responses.parse(
            model=FIELD_MODEL,
            input=[{"role": "user", "content": base}],
            text_format=Mapped,
        )
        record(FIELD_MODEL, r, "field_text")
        out = r.output_parsed
        if out.confident and out.value:
            return out.value, "ai"
    except Exception as e:
        print("   ai_resolve failed:", e)
    return None, None


ESSAY_RE = re.compile(
    r"(?i)\b(why|what|how|tell us|describe|explain|interest(ed)? in|"
    r"excites?|motivat|cover letter|in your own words)\b"
)


def ai_compose(field: dict, context: dict | None):
    """Layer 3b — open-ended prose ("Why do you want to join X?").

    ai_resolve is a *mapper*: it is told never to invent, so an essay prompt
    always comes back not-confident and the whole application goes to
    needs_manual. These questions have no answer sitting in profile.yaml —
    they have to be written. Grounded strictly in the résumé, and the user
    still sees every word on the Telegram card before anything is submitted.
    """
    if _never_auto(field["label"]) or field["options"]:
        return None, None

    ctx = context or {}
    prompt = (
        "Write this job-application answer as the candidate, in first person.\n"
        "Stating interest, motivation, and what the candidate wants to learn is "
        "exactly what these questions ask for — that is not a factual claim and "
        "is allowed.\n"
        "HARD RULES: every CONCRETE claim — employer, school, title, date, "
        "metric, technology, skill — must appear in CANDIDATE RESUME below. "
        "Never claim experience the résumé does not show. No placeholders like "
        "[Company]. 2-4 sentences, plain and specific, no flattery padding.\n"
        "Set confident=false ONLY if the résumé is so unrelated to the role "
        "that no honest answer is possible.\n\n"
        f"{NATURAL_VOICE}\n\n"
        f"CANDIDATE RESUME:\n{RESUME}\n\n"
        f"CANDIDATE LOGISTICS:\n{PROFILE}\n\n"
        f"ROLE: {ctx.get('title', '')} at {ctx.get('company', '')}\n"
        f"JOB DESCRIPTION (context only, not facts about the candidate):\n"
        f"{(ctx.get('jd') or '')[:3000]}\n\n"
        f"QUESTION: {field['label']}"
    )
    try:
        r = client.responses.parse(
            model=FIELD_MODEL,
            input=[{"role": "user", "content": prompt}],
            text_format=Mapped,
        )
        record(FIELD_MODEL, r, "field_essay")
        out = r.output_parsed
        if out.confident and out.value:
            return out.value.strip(), "ai_essay"
    except Exception as e:
        print("   ai_compose failed:", e)
    return None, None


def resolve_form(
    fields: list[dict], context: dict | None = None
) -> tuple[dict, list[dict], list[dict]]:
    """Returns (answers, unresolved REQUIRED fields, unresolved OPTIONAL fields).

    Optional fields used to be dropped without a word. That is silent data
    loss on a real application: a portfolio link or a "how did you hear about
    us" left blank because nothing knew the answer, and nobody was ever asked
    for it. They are reported separately from the required ones so the caller
    can ask about them without treating them as blocking.
    """
    answers, blocked, unanswered = {}, [], []
    for f in fields:
        if f["type"] == "file":
            continue

        # field_map/profile.yaml BEFORE the learned cache: profile.yaml is the
        # authority, the cache is only a memory of past guesses. With the cache
        # first, an answer learned once outranked the config forever — a stale
        # address stayed cached under "Email" and kept being filled in even
        # after profile.yaml was corrected.
        val, source = deterministic(f)
        if val is None:
            val = cached_answer(f)
            source = "cache" if val is not None else None
        if val is None:
            val, source = ai_resolve(f)
        # Essays only after the factual lanes miss: they are per-company, so
        # they are neither cached nor reusable.
        if val is None and (
            f["type"] == "textarea"
            or _is_essay(f["label"])
            or ESSAY_RE.search(f["label"])
        ):
            val, source = ai_compose(f, context)

        if val is not None:
            answers[f["name"] or f["label"]] = {"value": val, "source": source, "field": f}
            if source in ("field_map", "ai"):
                remember_answer(f, val, source)
        elif f["required"]:
            blocked.append(f)
        else:
            unanswered.append(f)
    return answers, blocked, unanswered
