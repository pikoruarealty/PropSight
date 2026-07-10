"""Password hashing, JWT issuing, and the request dependencies that guard routes.

The token is delivered in an httpOnly cookie rather than kept in JS: the pages
are server-rendered, so a bearer header would mean every navigation had to be a
fetch. An `Authorization: Bearer` header is still accepted for API clients.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from urllib.parse import quote

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pymongo.errors import DuplicateKeyError

from .config import AUTH_COOKIE, JWT_ALGORITHM, JWT_SECRET, JWT_TTL_HOURS
from .db import users

ROLE_ADMIN = "admin"
ROLE_USER = "user"

# Self-registered accounts start pending and cannot sign in until an admin
# approves them. Accounts an admin creates directly are approved on creation —
# the admin vouching for them at creation time is the approval.
STATUS_APPROVED = "approved"
STATUS_PENDING = "pending"

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
MIN_PASSWORD_LENGTH = 8


class AuthError(ValueError):
    """A credential or registration problem safe to show the user."""


# --- Passwords -------------------------------------------------------------


def hash_password(password: str) -> str:
    # bcrypt silently truncates past 72 bytes, which would make two different
    # long passwords interchangeable. Reject rather than quietly accept.
    if len(password.encode("utf-8")) > 72:
        raise AuthError("Password must be at most 72 bytes.")
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


# --- Tokens ----------------------------------------------------------------


def create_token(user: dict) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": str(user["_id"]),
        "username": user["username"],
        "role": user.get("role", ROLE_USER),
        "iat": now,
        "exp": now + dt.timedelta(hours=JWT_TTL_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None


def _token_from_request(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(AUTH_COOKIE)


# --- User records ----------------------------------------------------------


def create_user(
    username: str, password: str, role: str = ROLE_USER, status: str = STATUS_APPROVED
) -> dict:
    username = username.strip()
    if not USERNAME_RE.match(username):
        raise AuthError(
            "Username must be 3-32 characters, letters, digits, dot, dash or underscore."
        )
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if role not in (ROLE_ADMIN, ROLE_USER):
        raise AuthError("Unknown role.")

    doc = {
        "_id": uuid.uuid4().hex,
        "username": username,
        # Usernames are matched case-insensitively so "pikorua" and "PIKORUA"
        # cannot become two accounts; the unique index is on this field.
        "username_lower": username.lower(),
        "password_hash": hash_password(password),
        "role": role,
        "status": status,
        "created_at": dt.datetime.now(dt.timezone.utc),
    }
    try:
        users().insert_one(doc)
    except DuplicateKeyError:
        raise AuthError(f"The username '{username}' is already taken.") from None
    return doc


def approve_user(user_id: str) -> dict:
    user = users().find_one({"_id": user_id})
    if not user:
        raise AuthError("User not found.")
    users().update_one({"_id": user_id}, {"$set": {"status": STATUS_APPROVED}})
    user["status"] = STATUS_APPROVED
    return user


def find_user(username: str) -> dict | None:
    return users().find_one({"username_lower": username.strip().lower()})


def authenticate(username: str, password: str) -> dict:
    user = find_user(username)
    if not user or not verify_password(password, user["password_hash"]):
        raise AuthError("Incorrect username or password.")
    # Missing "status" means a pre-approval-feature account — treat as approved
    # rather than locking out everyone who registered before this existed.
    if user.get("status", STATUS_APPROVED) == STATUS_PENDING:
        raise AuthError("Your account is awaiting administrator approval.")
    return user


def ensure_bootstrap_admin(username: str, password: str) -> None:
    """Create the built-in admin exactly once, on a database that has none.

    Keyed on the account, not on the collection being empty, so an operator who
    deletes it can get it back on the next restart. An existing account is left
    alone — re-seeding would silently revert a changed password.
    """
    if find_user(username) is None:
        create_user(username, password, role=ROLE_ADMIN)


# --- Dependencies ----------------------------------------------------------


class NotAuthenticated(Exception):
    """Raised by page dependencies; an app exception handler turns it into a
    redirect to /login. Pages redirect, APIs 401 — the same failure, two
    different right answers for the two kinds of caller."""

    def __init__(self, next_url: str = "/") -> None:
        self.next_url = next_url


def current_user(request: Request) -> dict:
    """The signed-in user, or a 401. Use on JSON endpoints."""
    token = _token_from_request(request)
    claims = decode_token(token) if token else None
    if not claims:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated.")

    # Re-read the record rather than trusting the claims: a deleted or demoted
    # user must lose access before their (still valid, still unexpired) token does.
    user = users().find_one({"_id": claims["sub"]})
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account no longer exists.")
    return user


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user.get("role") != ROLE_ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator access required.")
    return user


def optional_user(request: Request) -> dict | None:
    """The signed-in user if there is one. Never raises — for /login and /register,
    which redirect an already-authenticated visitor away instead of rejecting them."""
    token = _token_from_request(request)
    claims = decode_token(token) if token else None
    if not claims:
        return None
    return users().find_one({"_id": claims["sub"]})


def page_user(request: Request) -> dict:
    """The signed-in user for an HTML page, else a redirect to /login."""
    user = optional_user(request)
    if not user:
        target = request.url.path
        if request.url.query:
            target = f"{target}?{request.url.query}"
        raise NotAuthenticated(target)
    return user


def page_admin(user: dict = Depends(page_user)) -> dict:
    if user.get("role") != ROLE_ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator access required.")
    return user


def login_redirect(exc: NotAuthenticated) -> RedirectResponse:
    return RedirectResponse(
        f"/login?next={quote(exc.next_url, safe='')}", status_code=status.HTTP_303_SEE_OTHER
    )
