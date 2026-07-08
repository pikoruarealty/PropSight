"""Groq AI narrative layer for lead insights.

Uses Groq's free-tier API (GROQ_API_KEY env var). Falls back gracefully to a
plain message when the key is absent or the call fails.

What we send:
  - Computed statistics: budget, configuration, HWC counts, buying status,
    call status, and the cross-tab matrices.
  - Raw qualitative remarks: up to REMARKS_SAMPLE rows, first REMARKS_MAX_CHARS
    chars each. This is the primary qualitative signal.

Results are cached per report_id in memory by the caller; nothing is ever
written to disk.
"""

from __future__ import annotations

import json
import os
import re

import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Best reasoning models on Groq as of early 2025 — change via GROQ_MODEL env var.
# Options include 'qwen/qwen3-32b' or 'llama-3.3-70b-versatile'
DEFAULT_MODEL = "llama-3.3-70b-versatile"

REMARKS_SAMPLE = 150   # max rows of raw remarks to include
REMARKS_MAX_CHARS = 180  # chars per remark before truncation

_PROMPT = """\
You are a real-estate sales analyst. You will receive:
1. Computed statistics from a CRM lead dataset (budget, configuration, HWC flags, buying status, call status).
2. A sample of raw qualitative remarks written by sales reps about each lead.

Return ONLY a JSON array of 5-8 insight objects, no prose, no code fences.
Each object: {"title": str, "finding": str (what the data shows, with numbers or quotes from remarks), "action": str (one concrete recommendation), "confidence": "high"|"medium"|"low"}.

Focus on:
- What budget ranges and configurations are most in demand?
- What do HWC-flagged leads specifically want?
- What patterns or sentiment emerge from the remarks?
- What does the buying/call status distribution say about pipeline health?

Base every finding strictly on the data provided.

DATA:
"""


def _condense(report: dict, df_remarks=None) -> str:
    """Build a compact prompt payload from stats + sampled remarks."""
    # Only send high-level stats and smaller cross-tabs to save tokens
    keys = [
        "meta", "core", "budget", "configuration",
        "call_status", "buying_status",
        "hwc_x_budget"
    ]
    stats = {k: report[k] for k in keys if k in report}
    payload: dict = {"statistics": stats}

    # Attach raw remarks sample if the caller supplied the DataFrame
    if df_remarks is not None:
        col = df_remarks.get("qualitative_remarks",
                             df_remarks.get("remarks",
                             __import__("pandas").Series(dtype=object)))
        non_empty = col.dropna().astype(str).str.strip()
        non_empty = non_empty[non_empty != ""]
        sample = non_empty.head(40).tolist()  # Reduced from 150 to 40 to avoid 413
        payload["sample_remarks"] = [r[:100] for r in sample]

    text = json.dumps(payload, default=str)
    # Don't blindly truncate the JSON string, let it be valid JSON.
    # The reduced sample size ensures it stays well under limits.
    return text


def generate_insights(report: dict, df=None) -> dict:
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        return {
            "error": (
                "GROQ_API_KEY is not set — AI insights are disabled. "
                "Add the key to your .env file to enable this panel."
            )
        }
    model = os.environ.get("GROQ_MODEL", DEFAULT_MODEL)
    try:
        resp = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "user", "content": _PROMPT + _condense(report, df)}
                ],
                "temperature": 0.3,
                "max_tokens": 2048,
            },
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        return {"error": f"AI insights unavailable: {exc}"}

    try:
        match = re.search(r"\[.*\]", content, re.DOTALL)
        insights = json.loads(match.group() if match else content)
        assert isinstance(insights, list) and insights
    except Exception:
        return {"error": "AI insights unavailable: model returned unparseable output."}

    cleaned = []
    for item in insights[:8]:
        if not isinstance(item, dict):
            continue
        cleaned.append(
            {
                "title": str(item.get("title", "")),
                "finding": str(item.get("finding", "")),
                "action": str(item.get("action", "")),
                "confidence": str(item.get("confidence", "medium")),
            }
        )
    if not cleaned:
        return {"error": "AI insights unavailable: model returned no usable insights."}
    return {"insights": cleaned}
