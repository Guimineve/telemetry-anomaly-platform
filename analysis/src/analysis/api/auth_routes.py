"""Registration, login/logout, the current user, and admin user management."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..auth.passwords import hash_password, verify_password
from .deps import SESSION_COOKIE, Admin, CurrentUser, Store, require_admin
from .schemas import ErrorOut, LoginIn, RegisterIn, UserOut, UserPatch

ERR = {"model": ErrorOut}
auth_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
users_router = APIRouter(
    prefix="/api/v1/users",
    tags=["users"],
    dependencies=[Depends(require_admin)],
    responses={401: ERR, 403: ERR},
)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _cookie_args(request: Request) -> dict[str, Any]:
    mode = request.app.state.settings.cookie_secure
    secure = mode == "true" or (mode == "auto" and request.url.scheme == "https")
    return {"path": "/", "httponly": True, "samesite": "strict", "secure": secure}


def _start_session(request: Request, response: Response, user_id: str) -> None:
    tokens = request.app.state.tokens
    response.set_cookie(
        SESSION_COOKIE, tokens.issue(user_id), max_age=tokens.ttl_s, **_cookie_args(request)
    )


@auth_router.post(
    "/register",
    response_model=UserOut,
    status_code=201,
    responses={409: ERR, 429: ERR},
    summary="Create a viewer account and log in",
)
async def register(body: RegisterIn, request: Request, response: Response, store: Store):
    limiter = request.app.state.register_limiter
    ip = f"ip:{_client_ip(request)}"
    if limiter.blocked(ip):
        raise HTTPException(429, "too many registrations; try again later")
    limiter.record(ip)
    password_hash = await asyncio.to_thread(hash_password, body.password)
    user = await store.create_user(body.username, password_hash, "viewer")
    if user is None:
        raise HTTPException(409, "username already taken")
    _start_session(request, response, user.id)
    return user


@auth_router.post(
    "/login",
    response_model=UserOut,
    responses={401: ERR, 429: ERR},
    summary="Log in; sets the session cookie",
)
async def login(body: LoginIn, request: Request, response: Response, store: Store):
    limiter = request.app.state.login_limiter
    keys = (f"ip:{_client_ip(request)}", f"user:{body.username}")
    if limiter.blocked(*keys):
        raise HTTPException(429, "too many failed attempts; try again later")
    user = await store.get_user_by_username(body.username)
    # always verify, even for an unknown user, so timing does not reveal which usernames exist
    ok = await asyncio.to_thread(
        verify_password, user.password_hash if user else None, body.password
    )
    if not ok or user is None or not user.is_active:
        limiter.record(*keys)
        raise HTTPException(401, "invalid username or password")
    limiter.reset(keys[1])
    now = datetime.now(UTC)
    await store.record_login(user.id, now)
    user.last_login_at = now
    _start_session(request, response, user.id)
    return user


@auth_router.post("/logout", status_code=204, summary="Log out; clears the session cookie")
async def logout(request: Request):
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, **_cookie_args(request))
    return response


@auth_router.get("/me", response_model=UserOut, responses={401: ERR}, summary="Current user")
async def me(user: CurrentUser):
    return user


@users_router.get("", response_model=list[UserOut], summary="List users (admin)")
async def list_users(store: Store):
    return await store.list_users()


@users_router.patch(
    "/{user_id}",
    response_model=UserOut,
    responses={404: ERR, 409: ERR},
    summary="Change a user's role or disable them (admin)",
)
async def update_user(user_id: str, body: UserPatch, admin: Admin, store: Store):
    data = body.model_dump(exclude_none=True)
    demotes_self = data.get("role", "admin") != "admin" or not data.get("is_active", True)
    if user_id == admin.id and demotes_self:
        raise HTTPException(409, "you cannot demote or disable yourself")
    row = await store.update_user(user_id, data)
    if row is None:
        raise HTTPException(404, "user not found")
    return row
