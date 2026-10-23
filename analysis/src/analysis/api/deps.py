"""FastAPI dependencies shared by the routers: the store and the logged-in user."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Protocol

from fastapi import Depends, HTTPException, Request

from ..auth.tokens import TokenCodec
from ..db import AlertRow, RuleRow, UserRow

SESSION_COOKIE = "session"


class ApiStore(Protocol):
    async def list_rules(self) -> list[RuleRow]: ...
    async def get_rule(self, rule_id: str) -> RuleRow | None: ...
    async def create_rule(self, data: dict) -> RuleRow: ...
    async def update_rule(self, rule_id: str, data: dict) -> RuleRow | None: ...
    async def delete_rule(self, rule_id: str) -> bool: ...
    async def list_alerts(self, **filters: Any) -> tuple[list[AlertRow], int]: ...
    async def get_alert(self, alert_id: str) -> AlertRow | None: ...
    async def ack_alert(self, alert_id: str, now: datetime) -> AlertRow | None: ...
    async def create_user(self, username: str, password_hash: str, role: str) -> UserRow | None: ...
    async def get_user(self, user_id: str) -> UserRow | None: ...
    async def get_user_by_username(self, username: str) -> UserRow | None: ...
    async def list_users(self) -> list[UserRow]: ...
    async def update_user(self, user_id: str, data: dict) -> UserRow | None: ...
    async def record_login(self, user_id: str, now: datetime) -> None: ...
    async def set_password(self, user_id: str, password_hash: str) -> UserRow | None: ...
    async def bump_token_version(self, user_id: str) -> UserRow | None: ...


def get_store(request: Request) -> ApiStore:
    return request.app.state.store


Store = Annotated[ApiStore, Depends(get_store)]


async def user_from_token(store: ApiStore, tokens: TokenCodec, token: str | None) -> UserRow | None:
    """The active user a session token belongs to, or None. Shared by HTTP and WebSocket."""
    session = tokens.decode(token) if token else None
    if session is None:
        return None
    user_id, version = session
    user = await store.get_user(user_id)
    if user is None or not user.is_active or user.token_version != version:
        return None
    return user


async def current_user(request: Request, store: Store) -> UserRow:
    token = request.cookies.get(SESSION_COOKIE)
    user = await user_from_token(store, request.app.state.tokens, token)
    if user is None:
        raise HTTPException(401, "not authenticated")
    return user


CurrentUser = Annotated[UserRow, Depends(current_user)]


async def require_admin(user: CurrentUser) -> UserRow:
    if user.role != "admin":
        raise HTTPException(403, "admin role required")
    return user


Admin = Annotated[UserRow, Depends(require_admin)]
