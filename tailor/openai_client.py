"""Tailoring via OpenAI Structured Outputs. Selects/reorders/rephrases real
resume content for a JD — never invents. Enforcement is the validator, not this."""
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel

from config import OPENAI_API_KEY, TAILOR_MODEL
from usage import record

client = OpenAI(api_key=OPENAI_API_KEY)

TAILOR_SYSTEM_PROMPT = """You tailor a résumé to a job description.

You MAY:
- SELECT which of the candidate's real experiences, projects, and skills to include
- REORDER them by relevance to the job description
- REPHRASE bullets to mirror the job description's vocabulary

You MAY NOT invent, add, infer, or exaggerate any skill, employer, job title,
date, degree, certification, tool, technology, or metric that is not explicitly
present in the SOURCE material. If the job description asks for a skill the
candidate does not have, omit it — never claim it. Every organization name,
title, date, number, and skill in your output must appear verbatim in the
SOURCE. Rephrasing prose is allowed; changing facts is not.

Also produce a 3-sentence tailored note explaining the candidate's fit, drawing
only on real source material."""


class Experience(BaseModel):
    org: str
    title: str
    dates: str
    bullets: list[str]


class Tailored(BaseModel):
    summary: str
    experience: list[Experience]
    projects: list[Experience]
    skills: list[str]
    note: str
    # LinkedIn's guest search ignores f_WT, so the workplace filter is rebuilt
    # here. The model is already reading the whole JD; classifying it costs
    # nothing extra and is far better than regex on postings that never use the
    # word "remote". "unstated" is a real answer — guessing is worse than saying
    # so, since the caller surfaces unstated jobs instead of dropping them.
    workplace_type: Literal["remote", "hybrid", "onsite", "unstated"]
    workplace_evidence: str


def _slice_master(master: dict, persona: str) -> dict:
    """Only feed the model sections tagged for this persona (plus 'all')."""

    def keep(item):
        tags = item.get("tags", ["all"])
        return persona in tags or "all" in tags

    return {
        "contact": master["contact"],
        "education": master["education"],
        "experience": [e for e in master["experience"] if keep(e)],
        "projects": [p for p in master["projects"] if keep(p)],
        "leadership": [l for l in master["leadership"] if keep(l)],
        "skills": master["skills"],
        "honors": master["honors"],
        "certifications": master["certifications"],
    }


def build_prompt(jd_text: str, master_slice: dict) -> str:
    import json

    return (
        "JOB DESCRIPTION:\n"
        f"{jd_text.strip()[:6000]}\n\n"
        "SOURCE (the candidate's real, verified history — the ONLY facts you may use):\n"
        f"{json.dumps(master_slice, indent=2)}\n\n"
        "Produce the tailored résumé content. Keep it to what fits on one page: "
        "at most 3 experience entries and 3 projects, at most 4 bullets each, "
        "and at most 12 skills.\n\n"
        "Also classify the job's workplace arrangement from the description:\n"
        "  remote   — no regular presence at an office required\n"
        "  hybrid   — some days on site, some remote\n"
        "  onsite   — regular in-person presence required\n"
        "  unstated — the description genuinely does not say\n"
        "Use 'unstated' rather than inferring from the city or from the job "
        "existing at all — a location alone is not evidence of the arrangement. "
        "In workplace_evidence, quote the phrase you based this on, or write "
        "'none' when unstated."
    )


def tailor(jd_text: str, master: dict, persona: str, job_id: str | None = None) -> Tailored:
    master_slice = _slice_master(master, persona)
    r = client.responses.parse(
        model=TAILOR_MODEL,
        input=[
            {"role": "system", "content": TAILOR_SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(jd_text, master_slice)},
        ],
        text_format=Tailored,
    )
    record(TAILOR_MODEL, r, "tailor", job_id)
    return r.output_parsed
