"""Groq narrative layer: 5-8 actionable insights over the computed report.

What we send:
  - Computed statistics: budget, configuration, HWC counts, buying/call status,
    the HWC x budget matrix, and lead-arrival timing.
  - Raw qualitative remarks: a capped sample of rep-written notes, the only
    genuinely qualitative signal in the dataset.

Sending the whole report would blow the request size limit, so `_condense`
picks the small, high-signal subset. Results are cached per report by the
caller; nothing is written to disk.
"""

from __future__ import annotations

import json

import pandas as pd

from . import llm

REMARKS_SAMPLE = 40
REMARKS_MAX_CHARS = 120

_PROMPT = """\
You are a real-estate sales analyst. You will receive:
1. Computed statistics from a CRM lead dataset (budget, configuration, HWC flags, buying status, call status, arrival timing).
2. A sample of raw qualitative remarks written by sales reps about each lead.

Return ONLY a JSON array of 5-8 insight objects, no prose, no code fences.
Each object: {"title": str, "finding": str (what the data shows, with numbers or quotes from remarks), "action": str (one concrete recommendation), "confidence": "high"|"medium"|"low"}.

Focus on:
- What budget ranges and configurations are most in demand?
- What do HWC-flagged leads specifically want?
- What patterns or sentiment emerge from the remarks?
- What does the buying/call status distribution say about pipeline health?
- When should the team be calling, given when leads arrive?

Many fields are recorded for only a minority of leads. When a statistic rests on
a small subset, say so in the finding and lower the confidence accordingly.
Base every finding strictly on the data provided.

DATA:
"""

_STAT_KEYS = [
    "meta", "core", "budget", "configuration",
    "call_status", "buying_status", "hwc_x_budget",
    "time", "data_quality",
]


def _condense(report: dict, df: pd.DataFrame | None = None) -> str:
    payload: dict = {"statistics": {k: report[k] for k in _STAT_KEYS if k in report}}

    if df is not None and "qualitative_remarks" in df.columns:
        remarks = df["qualitative_remarks"].dropna().astype(str).str.strip()
        remarks = remarks[remarks != ""]
        payload["sample_remarks"] = [r[:REMARKS_MAX_CHARS] for r in remarks.head(REMARKS_SAMPLE)]

    return json.dumps(payload, default=str)


def generate_insights(report: dict, df: pd.DataFrame | None = None) -> dict:
    """Return {"insights": [...]} or {"error": "..."} — never raises."""
    if not llm.is_enabled():
        return {
            "error": (
                "GROQ_API_KEY is not set — AI insights are disabled. "
                "Add the key to your .env file to enable this panel."
            )
        }

    insights, err = llm.chat_json_status(_PROMPT + _condense(report, df), max_tokens=2048)
    if err == "rate_limit":
        return {
            "error": "AI insights are temporarily rate-limited by the model provider. Try again in a minute.",
            "retryable": True,
        }
    if not isinstance(insights, list) or not insights:
        return {"error": "AI insights unavailable: the model returned no usable output."}

    cleaned = [
        {
            "title": str(item.get("title", "")),
            "finding": str(item.get("finding", "")),
            "action": str(item.get("action", "")),
            "confidence": str(item.get("confidence", "medium")),
        }
        for item in insights[:8]
        if isinstance(item, dict) and item.get("title")
    ]
    if not cleaned:
        return {"error": "AI insights unavailable: the model returned no usable insights."}
    return {"insights": cleaned}
