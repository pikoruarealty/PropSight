"""FastAPI app entry point.

Run single-process from the repo root:
    uvicorn prop_sight.api.main:app --reload --port 8010
(REPORTS lives in process memory — never use --workers.)

Requires a reachable MongoDB; set MONGODB_URI if it is not on localhost.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles

from . import auth, db, migrate, state
from .config import BOOTSTRAP_ADMIN_PASSWORD, BOOTSTRAP_ADMIN_USERNAME, STATIC_DIR
from .routes import auth_routes, pages, reports_api, sorter_api, upload

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.ping()
    db.init_indexes()
    auth.ensure_bootstrap_admin(BOOTSTRAP_ADMIN_USERNAME, BOOTSTRAP_ADMIN_PASSWORD)
    migrate.run()
    # Deferred until the database is up: the metadata for every saved report
    # lives there, and the frames on disk are meaningless without it.
    state.load_all_reports()
    yield


app = FastAPI(title="PropSight", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.exception_handler(auth.NotAuthenticated)
async def _redirect_to_login(request: Request, exc: auth.NotAuthenticated):
    return auth.login_redirect(exc)


app.include_router(auth_routes.router)
app.include_router(pages.router)

# The JSON API is guarded at the router, not per endpoint: a route added later
# is then authenticated by default rather than by remembering to say so.
protected = [Depends(auth.current_user)]
app.include_router(upload.router, dependencies=protected)
app.include_router(reports_api.router, dependencies=protected)
app.include_router(sorter_api.router, dependencies=protected)
