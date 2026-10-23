"""Signed session tokens (HS256 JWT) carrying the user id and the user's session version.

The role is deliberately not in the token: the server loads the user on every request, so a role
change or a disabled account takes effect immediately. The clock is injected so expiry is
testable without sleeping.

The version (`ver`) is compared with the user's `token_version` on every request; bumping it
(password change, log out everywhere, admin reset, disabling) ends every session issued before.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import jwt

Clock = Callable[[], datetime]
_ALGORITHM = "HS256"


class TokenCodec:
    def __init__(
        self, secret: str, ttl_s: float, clock: Clock = lambda: datetime.now(UTC)
    ) -> None:
        self._secret = secret
        self._ttl = timedelta(seconds=ttl_s)
        self._clock = clock

    @property
    def ttl_s(self) -> int:
        return int(self._ttl.total_seconds())

    def issue(self, user_id: str, version: int) -> str:
        now = self._clock()
        claims = {
            "sub": user_id,
            "ver": version,
            "iat": int(now.timestamp()),
            "exp": int((now + self._ttl).timestamp()),
        }
        return jwt.encode(claims, self._secret, algorithm=_ALGORITHM)

    def decode(self, token: str) -> tuple[str, int] | None:
        """(user id, session version), or None if the token is malformed, forged or expired."""
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=[_ALGORITHM],
                options={"require": ["sub", "exp", "ver"], "verify_exp": False},
            )
        except jwt.InvalidTokenError:
            return None
        if claims["exp"] <= self._clock().timestamp():
            return None
        sub, ver = claims["sub"], claims["ver"]
        if not isinstance(sub, str) or not sub or not isinstance(ver, int) or isinstance(ver, bool):
            return None
        return sub, ver
