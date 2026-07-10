"""One-line plain-English captions for the cross-tab charts.

A stacked bar of `HWC-flagged x configuration` is unreadable to a salesperson
who does not think in cross-tabs. The caption states what the chart *means* —
"priority clients overwhelmingly want 4BHK" — rather than what it plots.

Every chart has a hand-written description as the fallback, so an absent or
failing LLM degrades to a static sentence rather than a blank card.
"""

from __future__ import annotations

from . import llm

# chart key -> (human title, static fallback caption)
CHARTS: dict[str, tuple[str, str]] = {
    "hwc_x_config": (
        "HWC-flagged x configuration",
        "Which unit types your priority (HWC-marked) clients are asking for.",
    ),
    "hwc_x_budget": (
        "HWC-flagged x budget bucket",
        "Whether priority clients sit in higher price bands than everyone else.",
    ),
    "buying_status_x_config": (
        "Buying status x configuration",
        "Whether leads closer to buying cluster around particular unit types.",
    ),
    "call_status_x_buying": (
        "Call status x buying status",
        "Whether leads you could not reach were actually warm buyers.",
    ),
    "config_x_budget": (
        "Configuration x budget bucket",
        "Which unit types sit in which price bands.",
    ),
    "lead_class_x_budget": (
        "Lead class x budget bucket",
        "Whether the leads your rules call good carry bigger budgets.",
    ),
    "lead_class_x_config": (
        "Lead class x configuration",
        "Which unit types the leads your rules call good are asking for.",
    ),
    "lead_class_x_call_status": (
        "Lead class x call status",
        "Whether the leads your rules call good are the ones you actually reach.",
    ),
}

_PROMPT = """\
You are explaining charts to a real-estate salesperson with no analytics training.

For each chart below you get a cross-tabulation: row categories, column
categories, and a matrix of lead counts. `coverage.counted` is how many leads
had BOTH values recorded, out of `coverage.total`.

For each chart, write ONE sentence (max 25 words) stating the single clearest
pattern in that chart's numbers, in plain language. Name real categories and
use a real number or percentage. No jargon, no "this chart shows".

If coverage.counted is under 25% of coverage.total, begin that sentence with
"Based on the minority of leads with both fields recorded,".

Return ONLY a JSON object mapping each chart key to its sentence.

CHARTS:
{payload}
"""


def _condense(report: dict) -> dict:
    """Pull just the matrices the captions describe, dropping everything else."""
    out = {}
    for key in CHARTS:
        chart = report.get(key)
        if isinstance(chart, dict) and chart.get("rows"):
            out[key] = {
                "rows": chart["rows"],
                "cols": chart["cols"],
                "matrix": chart["matrix"],
                "coverage": chart.get("coverage", {}),
            }
    return out


def fallback_summaries(report: dict) -> dict[str, str]:
    return {key: text for key, (_, text) in CHARTS.items() if report.get(key, {}).get("rows")}


def generate_chart_summaries(report: dict) -> dict[str, str]:
    """LLM caption per cross-tab, falling back to the static description.

    Never raises and never returns a key for a chart that has no data.
    """
    present = _condense(report)
    if not present:
        return {}

    summaries = fallback_summaries(report)
    if not llm.is_enabled():
        return summaries

    import json

    raw = llm.chat_json(
        _PROMPT.format(payload=json.dumps(present, default=str)),
        max_tokens=700,
        temperature=0.2,
    )
    if not isinstance(raw, dict):
        return summaries

    for key, text in raw.items():
        if key in present and isinstance(text, str) and text.strip():
            summaries[key] = text.strip()
    return summaries
