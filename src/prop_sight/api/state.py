"""In-memory report store. Nothing survives a server restart — by design.

Single-process only: REPORTS is a plain dict, not shared across uvicorn
workers. Run without --workers.
"""

from __future__ import annotations

import threading

# report_id -> {status, created_at, files, parsed, df, report, insights_cache}
REPORTS: dict[str, dict] = {}
REPORTS_LOCK = threading.RLock()
