"""OpenAI token/cost accounting.

Every model call records its token counts so the daily digest can report real
spend instead of a guess. Prices are per 1M tokens and come from config so a
rate change is an .env edit, not a code change.
"""
from db import get_db
from config import MODEL_PRICES


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Price a call. Unknown models price at 0 rather than inventing a rate —
    the digest flags those separately so silence never reads as 'free'."""
    rate = MODEL_PRICES.get(model)
    if not rate:
        return 0.0
    return (input_tokens * rate["input"] + output_tokens * rate["output"]) / 1_000_000


def record(model: str, response, purpose: str, job_id: str | None = None) -> float:
    """Log one call's usage. Never raises — accounting must not break a run."""
    try:
        u = getattr(response, "usage", None)
        if u is None:
            return 0.0
        # Responses API uses input/output_tokens; chat completions use prompt/completion.
        inp = getattr(u, "input_tokens", None) or getattr(u, "prompt_tokens", 0) or 0
        out = getattr(u, "output_tokens", None) or getattr(u, "completion_tokens", 0) or 0
        c = cost_usd(model, inp, out)
        get_db().table("api_usage").insert({
            "model": model,
            "purpose": purpose,
            "job_id": job_id,
            "input_tokens": inp,
            "output_tokens": out,
            "cost_usd": c,
            "priced": model in MODEL_PRICES,
        }).execute()
        return c
    except Exception as e:  # noqa: BLE001 — telemetry is never fatal
        print(f"[usage] record failed ({model}/{purpose}): {e}")
        return 0.0
