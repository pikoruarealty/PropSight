# PropSight

Analyze real-estate lead exports — Excel workbooks and CSVs from several
unrelated CRMs — as **one deduplicated lead book**: budget bands, unit-type
demand, priority (HWC) clients, call/buying pipeline, lead-arrival timing, and
campaign attribution, all on one cross-filterable dashboard.

## The core idea

The same person is exported by three systems that share no lead ID. The only
field common to all of them is the **mobile number**, so that is the identity
key. Uploads are not separate datasets — they are different views of one lead
book — so the default page (`/`) is the **combined dashboard** over every upload,
deduplicated. Individual uploads remain visible under *Datasets* for
inspection and removal.

## Supported formats

| Format | Recognized by | Notes |
|---|---|---|
| Legacy calling workbooks | header aliases | many sheets, drifting spellings, one property type per sheet |
| Privyr export | header aliases + notes parsing | budget/city/config are buried in the free-text **Notes** column and are recovered from it |
| CRM CSV | header aliases | `Configuration Needed`, `Received`, … |
| **Anything else** | **LLM fallback** | column names + 10 sample rows are sent to Groq, which proposes a column→field mapping |

The LLM fallback only runs when the deterministic alias table matches fewer than
5 canonical fields, so a known format is never at the mercy of a model. Its
output is validated against the canonical field list — hallucinated columns,
hallucinated fields, and double-mappings are dropped. It sends real customer
names and phone numbers to Groq, so it is inert without `GROQ_API_KEY`.

## Deduplication

Two rows are the same lead when they share **both** a valid mobile and a matching
folded name. Phone alone is not enough: in the client's own data, 139 numbers are
shared by two or more genuinely different people. Merging is a *coalesce* — the
legacy sheet's budget and the Privyr row's campaign both survive onto one lead.

Phone numbers are normalized before comparison (`+91`, a mistyped `+1`, trunk
zeros, Excel floats, two numbers in one cell). Rows whose phone is present but
**invalid** are dropped; rows with **no** phone are kept but never merged, since
they have no identity key.

## Fields hidden inside free text

A rule of the form "column = value" is useless on a Privyr export, where the
budget, city and every ad-form answer live inside one prose `Notes` cell — the
whole blob is a distinct value per lead, so there is nothing to group by.

`ingestion/notes.py` lifts every recurring `Label: value` line into a real
column, so the sorter and the analytics see the same shape of data whatever CRM
produced the file:

- A label meaning a field we know (`What Is Your Budget?`) backfills that
  canonical column, so budget bands, cross-tabs and rules work on Privyr rows
  exactly as on the legacy sheets. A real spreadsheet column always outranks a
  value recovered from prose.
- A label we have no field for (`Are You Ready To Proceed With A Refundable
  EOI?`) becomes a `note:` column — groupable and exportable, with no claim made
  about its meaning. Your CRM CSV turns out to carry `Category`, `Timeline` and
  `Preferred callback` this way.

A label must *recur* to become a column, so ordinary prose ("called at 5:30")
never mints one. Labels already recognized bypass that check, because ad sets
word the same question differently across campaigns and the rarer wording is
exactly what a frequency filter would throw away.

When `GROQ_API_KEY` is set, unrecognized labels are additionally offered to the
LLM for a canonical mapping. That mapping only ever *enriches*: the raw question
keeps its own `note:` column regardless, because the model's guess is not
reproducible — it once decided `Timeline` ("how soon will they buy") meant
`looking_since` ("how long have they been searching"), which is close to the
opposite. A label can never be routed onto `name`, `email`, `phone`, or back
onto the notes column it came from.

## Sorting leads: Good, Bad, and why not

`/sorter` classifies every lead with rules you define — conditions ANDed within
a rule, rules evaluated top to bottom, first match wins.

**Rules bind to canonical fields, never to raw headers.** One rule written
against `configuration_required` fires on the legacy sheets ("Configuration
Required"), on the CRM CSV ("Configuration Needed"), and on Privyr (a question
inside the notes). Rules also see the *folded* values, so listing "Bungalow"
matches a lead recorded as "Bungalow only", and `budget_bucket` exists because
the same preparation the charts use has already run.

A lead matched by no rule is **unclassified** — a statement about the rules, not
the lead. The sorter groups those by reason ("No data in budget_bucket,
buying_status"), which is nearly always that the fields a rule needs were never
filled in. Unclassified is deliberately absent from the dashboard's charts:
comparing it against Good and Bad would mean nothing.

Classification feeds back into the dashboard as `Good leads` / `Bad leads`
filter chips and three cross-tabs (lead class × budget / configuration / call
status). Exports: one Excel per class — each carrying a `lead_class_reason`
column — plus a Meta customer-list CSV built from canonical fields, so its `fn`
is the lead's name rather than whichever column happened to contain "name".

## Why a chart says "112" when you uploaded 5,000 leads

Most leads have no budget and no configuration recorded. A cross-tab can only
count rows where *both* variables exist, so `Configuration × budget` is computed
from ~18% of the database. Every cross-tab is therefore captioned with the
denominator it actually used, and the **Data completeness** card shows the fill
rate of every charted field. Two cross-tabs sharing an axis legitimately disagree
on that axis's totals, because each drops a different set of rows.

## Chart conventions

- **1-D distributions** are donuts, with count and share direct-labelled in the
  legend — a donut's angles cannot be compared by eye.
- **Cross-tabs** stay stacked bars (a pie cannot show two variables) with a
  *"% of each row"* toggle, which is how rows of different size are compared.
- Categorical hues come from 8 fixed slots and are **never cycled**; the tail
  folds into "Other". The full matrix is always available under *View as table*.

## Run

PropSight needs a reachable **MongoDB**. A local `mongod` on the default port
works; otherwise set `MONGODB_URI` to an Atlas connection string.

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install -e .
.venv\Scripts\uvicorn prop_sight.api.main:app --reload --port 8010
```

Open http://127.0.0.1:8010 and sign in. On a fresh database a single
administrator is seeded: **`PIKORUA` / `Pikorua@123`** — change it, or override
the seed with `PROPSIGHT_ADMIN_USER` / `PROPSIGHT_ADMIN_PASSWORD` before the
first start. The account is only created when it does not already exist, so a
later password change is never reverted by a restart.

Then upload `.xlsx` / `.csv` exports, confirm the detected property types, and
the combined dashboard builds.

> **Single-process only.** Never run with `--workers > 1`. The report store
> (`REPORTS` in `api/state.py`) lives in one process and is not shared across
> uvicorn workers.

## Authentication

Signing in issues a JWT delivered in an httpOnly cookie (an `Authorization:
Bearer` header is also accepted, for API clients). Pages redirect anonymous
visitors to `/login`; JSON endpoints return `401`. Two roles:

| Role | Can |
|---|---|
| `user` | see every dataset, edit rules, upload, export |
| `admin` | the above, plus create and remove accounts at `/users` |

Anyone may self-register at `/register`, which creates a `user`. Every request
re-reads the account, so deleting a user revokes their still-unexpired token
immediately.

Set `JWT_SECRET` in production — rotating it is the intended way to force a
global sign-out. `JWT_TTL_HOURS` (default 12) controls session length.

## Storage

Everything durable lives in MongoDB (`propsight` database):

| What | Where |
|---|---|
| Users | `users` collection |
| Report metadata | `reports` collection |
| Categorization rules | `settings` document `_id: "rules"` |
| Lead frames | `frames` GridFS bucket, Parquet bytes keyed by report id |

Frames go in GridFS rather than in a binary field on the report document: one
upload is capped at 25 MB across 10 files, so a frame can outgrow Mongo's 16 MB
per-document limit, and GridFS chunks instead. The cached report blob is
**recomputed** on load rather than stored, so a report saved by an older build
still renders.

Nothing is written to `data/` any more. On first start, a pre-MongoDB install is
migrated automatically: `rules.json` and `meta.json` are copied in and renamed to
`*.migrated`, and each `df.parquet` is uploaded to GridFS, **read back and
compared byte-for-byte**, and only then deleted from disk. The migration is
idempotent, and leaves any frame it cannot verify exactly where it is. `data/` is
gitignored — a leftover from an older install holds real customer records.

## Environment

| Variable | Default |
|---|---|
| `MONGODB_URI` | `mongodb://localhost:27017` |
| `MONGODB_DB` | `propsight` |
| `JWT_SECRET` | a development placeholder — **override this** |
| `JWT_TTL_HOURS` | `12` |
| `PROPSIGHT_ADMIN_USER` | `PIKORUA` |
| `PROPSIGHT_ADMIN_PASSWORD` | `Pikorua@123` |
| `GROQ_API_KEY` | unset (AI features degrade, see below) |

## Optional AI features

Set `GROQ_API_KEY` in `.env`. Three features use it, each with a working fallback:

| Feature | Without a key |
|---|---|
| AI insights panel | shows a "disabled" message |
| Cross-tab captions | falls back to a hand-written sentence per chart |
| Unknown-format column mapping | the sheet is simply reported as "not lead data" |

Override the model with `GROQ_MODEL`. Insights are cached per report in memory,
never on disk.

## Tests

```bash
.venv\Scripts\python -m pytest
```

Fixtures build workbooks in memory (`tests/fixtures.py`) — no disk I/O. The LLM
is stubbed, never called.

## Layout

```
src/prop_sight/
  ingestion/    excel/csv reading, mojibake repair, property-type detection,
                header normalization, notes field extraction, phone
                normalization, cross-format dedupe, LLM schema fallback, merge
  analytics/    budget, segmentation, synonyms, timeseries, core (HWC), rules
                (lead classification), report bundler, shared Groq client,
                llm_insights, chart_summaries
  api/          FastAPI app: routes/ (thin handlers), services/ (logic incl.
                rules + exports), state.py (report store + combined pool),
                templates/ + static/ (Tailwind + Chart.js CDN)
tests/          unit tests with in-memory fixtures; the LLM is always stubbed
sample_data/    synthetic QA workbooks (never real client data)
```

`data/` holds `rules.json` (global categorization rules) alongside one directory
per confirmed upload. It is gitignored — it contains real customer records.
