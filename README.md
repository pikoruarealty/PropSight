# PropSight

Analyze historical real-estate lead data exported as Excel workbooks: customer
segmentation, property-type breakdowns, budget-range analysis, funnels,
geography, source attribution, velocity, and qualitative-text signals — all
merged into one cross-filterable dashboard.

**Stateless by design.** Every uploaded workbook is parsed and analyzed entirely
in server memory (`io.BytesIO`, no temp files, no database). A server restart
wipes all reports. Reports are kept in an in-memory store keyed by `report_id`
so you can revisit one without re-uploading during the same server session.

## How data is interpreted

- Each workbook can have **multiple sheets**; all sheets share one column schema.
- The **workbook filename indicates the property type** ("Apartment Leads.xlsx" →
  Apartment). Detection is keyword-based and always confirmed/correctable in the
  UI before a report is built; undetected filenames require a manual selection.
- Column-header spelling drift ("lead Source" / "Lead Source" / "LeadSource") is
  normalized automatically.
- The header row contains **"Form" twice**; the second occurrence is reported
  separately as an unlabeled column — its meaning is not assumed.
- Field vocabularies (Stage, Interest Level, …) are **not hardcoded** —
  distributions are derived from whatever values the data contains. Sparse /
  mostly-empty columns are expected and handled throughout.

## Run

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install -e .
.venv\Scripts\uvicorn prop_sight.api.main:app --reload --port 8010
```

Open http://127.0.0.1:8010 — upload one or more `.xlsx` exports, confirm the
detected property types, and the dashboard builds.

> **IMPORTANT: single-process only.** Never run with `--workers > 1`. The
> in-memory report store (`REPORTS` in `api/state.py`) lives in one process and
> is not shared across uvicorn workers — multiple workers would each see a
> different subset of reports.

## Optional AI insights

Copy `.env.example` to `.env` and set `OPENROUTER_API_KEY` to enable the
AI-narrative panel (5–8 insights generated from the computed statistics via
OpenRouter). Without a key the panel shows a plain "disabled" message; nothing
else is affected. Insights are cached per report in memory only.

## Tests

```bash
.venv\Scripts\python -m pytest
```

All test fixtures build workbooks in memory (`tests/fixtures.py`) — no disk I/O.

## Sample data

`sample_data/generate_samples.py` writes small synthetic workbooks (seeded,
deterministic) for manual QA — including deliberately sparse rows, duplicate
flags, near-duplicate city spellings, mixed date formats, and one workbook with
an undetectable filename (`Q1 Export.xlsx`) to exercise the manual-selection path.

## Layout

```
src/prop_sight/
  ingestion/    excel reading, filename→property-type detection,
                header normalization, multi-workbook merge
  analytics/    budget, segmentation, funnel, property, geography,
                source, velocity, qualitative, llm_insights, report bundler
  api/          FastAPI app: routes/ (thin handlers), services/ (logic),
                templates/ + static/ (server-rendered UI, Tailwind + Chart.js CDN)
tests/          unit tests with in-memory workbook fixtures
sample_data/    synthetic QA workbooks (never real client data)
```
