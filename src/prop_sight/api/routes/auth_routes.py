"""Sign-in, registration, sign-out, and admin user management."""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .. import auth
from ..config import AUTH_COOKIE, JWT_TTL_HOURS, TEMPLATES_DIR
from ..db import users

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _safe_next(raw: str | None) -> str:
    """Only ever redirect back to a path on this host.

    `?next=https://evil.example` would otherwise turn the login page into an
    open redirect that phishing can point at.
    """
    if not raw:
        return "/"
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc or not raw.startswith("/"):
        return "/"
    return raw


def _issue(response: RedirectResponse, user: dict) -> RedirectResponse:
    response.set_cookie(
        AUTH_COOKIE,
        auth.create_token(user),
        max_age=JWT_TTL_HOURS * 3600,
        httponly=True,
        samesite="lax",
        # Left off so the cookie survives plain-HTTP local development; set
        # secure=True behind TLS in production.
    )
    return response


# --- Pages -----------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    if auth.optional_user(request):
        return RedirectResponse(_safe_next(next))
    return templates.TemplateResponse(request, "login.html", {"next": _safe_next(next)})


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    try:
        user = auth.authenticate(username, password)
    except auth.AuthError as exc:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": str(exc), "username": username, "next": _safe_next(next)},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    return _issue(RedirectResponse(_safe_next(next), status_code=status.HTTP_303_SEE_OTHER), user)


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    if auth.optional_user(request):
        return RedirectResponse("/")
    return templates.TemplateResponse(request, "register.html", {})


@router.post("/register")
def register_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
):
    error = None
    if password != confirm_password:
        error = "The two passwords do not match."
    else:
        try:
            # Self-registration can only ever produce a plain user, pending
            # approval — an admin role is something an admin grants, not
            # something you can type into a public form.
            auth.create_user(
                username, password, role=auth.ROLE_USER, status=auth.STATUS_PENDING
            )
        except auth.AuthError as exc:
            error = str(exc)

    if error:
        return templates.TemplateResponse(
            request,
            "register.html",
            {"error": error, "username": username},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    # No auto-login: the account cannot be used until an administrator
    # approves it, so signing it in here would just bounce straight to a
    # "pending approval" wall behind the login screen instead of in front of it.
    return templates.TemplateResponse(request, "register.html", {"pending": True})


@router.get("/logout")
def logout():
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(AUTH_COOKIE)
    return response


@router.get("/users", response_class=HTMLResponse)
def users_page(request: Request, admin: dict = Depends(auth.page_admin)):
    roster = sorted(users().find({}), key=lambda u: u["username_lower"])
    pending = [u for u in roster if u.get("status") == auth.STATUS_PENDING]
    return templates.TemplateResponse(
        request, "users.html", {"user": admin, "users": roster, "pending": pending}
    )


# --- JSON API --------------------------------------------------------------


class NewUser(BaseModel):
    username: str
    password: str
    role: str = auth.ROLE_USER


@router.get("/api/me")
def me(user: dict = Depends(auth.current_user)):
    return {"username": user["username"], "role": user.get("role", auth.ROLE_USER)}


@router.post("/api/users", status_code=status.HTTP_201_CREATED)
def create_user_api(body: NewUser, admin: dict = Depends(auth.require_admin)):
    try:
        created = auth.create_user(body.username, body.password, body.role)
    except auth.AuthError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    return {"username": created["username"], "role": created["role"]}


@router.post("/api/users/{user_id}/approve")
def approve_user_api(user_id: str, admin: dict = Depends(auth.require_admin)):
    try:
        approved = auth.approve_user(user_id)
    except auth.AuthError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    return {"username": approved["username"], "status": approved["status"]}


@router.delete("/api/users/{user_id}")
def delete_user_api(user_id: str, admin: dict = Depends(auth.require_admin)):
    if user_id == admin["_id"]:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "You cannot delete the account you are signed in as."
        )
    target = users().find_one({"_id": user_id})
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    # Locking every admin out of user management would need database access to
    # undo, so refuse to remove the last one.
    if target.get("role") == auth.ROLE_ADMIN and users().count_documents({"role": auth.ROLE_ADMIN}) <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot delete the only administrator.")
    users().delete_one({"_id": user_id})
    return {"deleted": user_id}
