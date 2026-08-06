"""Turn a live form into typed fields, then resolve each one:

Layer 1  enumerate DOM -> {label, type, required, options}
Layer 2  deterministic field_map.yaml + learned field_answers cache
Layer 3  AI (cheap model) with a schema whose enum is the form's own options,
         so an invented option is structurally impossible
Layer 4  any REQUIRED field still unresolved -> caller must not submit
"""
import re
from pathlib import Path

import yaml
from openai import OpenAI
from pydantic import BaseModel

from config import OPENAI_API_KEY, FIELD_MODEL
from db import get_db

ROOT = Path(__file__).resolve().parent.parent
PROFILE = yaml.safe_load((ROOT / "profile.yaml").read_text())
FIELD_MAP = yaml.safe_load((ROOT / "field_map.yaml").read_text())

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


def enumerate_fields(page) -> list[dict]:
    """Layer 1 — the form as data, not pixels.

    Handles native inputs AND the React comboboxes modern ATSes render as
    `<input type=text role=combobox>` with the options in a popup listbox.
    """
    return page.evaluate(
        """() => {
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
            return best || clean(el.name || el.placeholder || '');
        };

        const isCombo = el =>
            el.getAttribute('role') === 'combobox'
            || el.getAttribute('aria-haspopup') === 'listbox'
            || el.getAttribute('aria-autocomplete') === 'list'
            || !!el.closest('[class*="select"],[class*="Select"],[class*="dropdown"]');

        const out = [], seen = new Set();
        for (const el of document.querySelectorAll('input, select, textarea')) {
            if (el.type === 'hidden' || el.disabled) continue;
            if (el.offsetParent === null && el.type !== 'file') continue;

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
        return out;
    }"""
    )


def _locate(page, field: dict):
    """Best-effort locator for an enumerated field."""
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


def remember_answer(field: dict, answer, source: str):
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
    if _never_auto(label):
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
    if _never_auto(field["label"]):
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
            import json

            out = json.loads(r.output_text)
            return (out["value"], "ai") if out.get("confident") else (None, None)

        r = client.responses.parse(
            model=FIELD_MODEL,
            input=[{"role": "user", "content": base}],
            text_format=Mapped,
        )
        out = r.output_parsed
        if out.confident and out.value:
            return out.value, "ai"
    except Exception as e:
        print("   ai_resolve failed:", e)
    return None, None


def resolve_form(fields: list[dict]) -> tuple[dict, list[dict]]:
    """Returns (answers by field name, unresolved REQUIRED fields)."""
    answers, blocked = {}, []
    for f in fields:
        if f["type"] == "file":
            continue

        val = cached_answer(f)
        source = "cache" if val is not None else None
        if val is None:
            val, source = deterministic(f)
        if val is None:
            val, source = ai_resolve(f)

        if val is not None:
            answers[f["name"] or f["label"]] = {"value": val, "source": source, "field": f}
            if source in ("field_map", "ai"):
                remember_answer(f, val, source)
        elif f["required"]:
            blocked.append(f)
    return answers, blocked
