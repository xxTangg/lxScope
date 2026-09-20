# -*- coding: utf-8 -*-
"""JWT authentication and lightweight account management for the Web UI."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field

_DEFAULT_ISSUER = "agentscope-web-ui"
_DEFAULT_AUDIENCE = "agentscope-api"
_DEFAULT_TOKEN_MINUTES = 24 * 60
_AUTH_KEY_PREFIX = "longxin:auth"


class LoginRequest(BaseModel):
    """Username/password login payload."""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class RegisterRequest(BaseModel):
    """Self-service registration payload."""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=1024)


class AuthUser(BaseModel):
    """Public identity returned to the Web UI."""

    id: str
    username: str
    role: Literal["user", "admin"] = "user"
    status: Literal["active", "locked", "banned", "deleted"] = "active"
    capabilities: list[str] = Field(default_factory=list)
    token_version: int = Field(default=0, exclude=True)


class LoginResponse(BaseModel):
    """Bearer token response."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: AuthUser


class TokenUsageResponse(BaseModel):
    """Aggregate model token usage for the current user."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    total_tokens: int = 0
    message_count: int = 0
    session_count: int = 0


class _StoredAccount(BaseModel):
    username: str
    user_id: str
    salt: str
    password_digest: str
    created_at: str
    password_expires_at: str | None = None
    role: Literal["user", "admin"] = "user"
    status: Literal["active", "locked", "banned", "deleted"] = "active"
    failed_attempts: int = 0
    locked_until: str | None = None
    token_version: int = 0
    temporary_password_expires_at: str | None = None


@dataclass(frozen=True)
class _Account:
    username: str
    user_id: str
    salt: bytes
    password_digest: bytes
    password_expires_at: datetime | None = None
    role: Literal["user", "admin"] = "user"
    status: Literal["active", "locked", "banned", "deleted"] = "active"
    failed_attempts: int = 0
    locked_until: str | None = None
    token_version: int = 0
    temporary_password_expires_at: str | None = None


def _derive_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _account_conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="This username is already registered.",
    )


class JWTAuthService:
    """Authenticate configured and Redis-backed accounts and issue JWTs."""

    def __init__(
        self,
        users: dict[str, tuple[str, str] | tuple[str, str, str]],
        secret: str,
        *,
        storage: Any | None = None,
        on_registered: Callable[[str], Awaitable[None]] | None = None,
        issuer: str = _DEFAULT_ISSUER,
        audience: str = _DEFAULT_AUDIENCE,
        token_minutes: int = _DEFAULT_TOKEN_MINUTES,
    ) -> None:
        if not users:
            raise ValueError("At least one authentication account is required.")
        if len(secret.encode("utf-8")) < 32:
            raise ValueError("The JWT secret must be at least 32 bytes long.")
        if token_minutes <= 0:
            raise ValueError("JWT token lifetime must be greater than zero.")

        accounts: dict[str, _Account] = {}
        accounts_by_id: dict[str, _Account] = {}
        for username, config in users.items():
            if len(config) == 2:
                user_id, password = config
                configured_role = "admin" if username.strip().lower() == "admin" else "user"
            else:
                user_id, password, configured_role = config
            if configured_role not in {"user", "admin"}:
                raise ValueError("Auth roles must be either 'user' or 'admin'.")
            normalized = self._normalize_username(username)
            if not user_id.strip() or not password:
                raise ValueError("Auth user ids and passwords are required.")
            if normalized in accounts:
                raise ValueError(f"Duplicate auth username {normalized!r}.")
            if user_id in accounts_by_id:
                raise ValueError(f"Duplicate auth user id {user_id!r}.")
            salt = secrets.token_bytes(16)
            account = _Account(
                username=normalized,
                user_id=user_id,
                salt=salt,
                password_digest=_derive_password(password, salt),
                role=configured_role,
            )
            accounts[normalized] = account
            accounts_by_id[user_id] = account

        self._accounts = accounts
        self._accounts_by_id = accounts_by_id
        self._storage = storage
        self._on_registered = on_registered
        self._secret = secret
        self._issuer = issuer
        self._audience = audience
        self._token_lifetime = timedelta(minutes=token_minutes)
        self.router = self._build_router()

    @staticmethod
    def _normalize_username(username: str) -> str:
        normalized = username.strip()
        if not normalized or any(char.isspace() for char in normalized):
            raise ValueError("Username cannot be empty or contain whitespace.")
        return normalized

    @staticmethod
    def _username_key(username: str) -> str:
        digest = hashlib.sha256(username.encode("utf-8")).hexdigest()
        return f"{_AUTH_KEY_PREFIX}:username:{digest}"

    @staticmethod
    def _user_key(user_id: str) -> str:
        return f"{_AUTH_KEY_PREFIX}:user:{user_id}"

    def _redis_or_none(self) -> Any | None:
        return self._storage.get_client() if self._storage is not None else None

    def _redis(self) -> Any:
        client = self._redis_or_none()
        if client is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Account storage is not ready.",
            )
        return client

    @property
    def user_ids(self) -> tuple[str, ...]:
        """Return environment-configured tenant ids for startup provisioning."""
        return tuple(self._accounts_by_id)

    @property
    def admin_user_ids(self) -> tuple[str, ...]:
        """Return configured administrator ids for application wiring."""
        return tuple(
            account.user_id
            for account in self._accounts_by_id.values()
            if account.role == "admin"
        )

    async def is_admin_user(self, user_id: str) -> bool:
        """Return whether an account is an active administrator."""
        account = await self._account_by_id(user_id)
        return bool(
            account is not None
            and account.role == "admin"
            and account.status == "active"
        )

    @staticmethod
    def _capabilities(role: str) -> list[str]:
        if role == "admin":
            return [
                "admin.users.read",
                "admin.users.write",
                "admin.quota.read",
                "admin.quota.write",
                "admin.sales_hub.write",
            ]
        return ["chat.read", "chat.write"]

    @classmethod
    def _public_user(cls, account: _Account) -> AuthUser:
        return AuthUser(
            id=account.user_id,
            username=account.username,
            role=account.role,
            status=account.status,
            capabilities=cls._capabilities(account.role),
            token_version=account.token_version,
        )

    @staticmethod
    def _account_from_stored(stored: _StoredAccount) -> _Account:
        return _Account(
            username=stored.username,
            user_id=stored.user_id,
            salt=base64.b64decode(stored.salt),
            password_digest=base64.b64decode(stored.password_digest),
            password_expires_at=(
                datetime.fromisoformat(stored.password_expires_at.replace("Z", "+00:00"))
                if stored.password_expires_at
                else None
            ),
            role=stored.role,
            status=stored.status,
            failed_attempts=stored.failed_attempts,
            locked_until=stored.locked_until,
            token_version=stored.token_version,
            temporary_password_expires_at=stored.temporary_password_expires_at,
        )

    async def _registered_by_username(self, username: str) -> _Account | None:
        client = self._redis_or_none()
        if client is None:
            return None
        raw = await client.get(self._username_key(username))
        if raw is None:
            return None
        stored = _StoredAccount.model_validate_json(raw)
        return self._account_from_stored(stored)

    async def _registered_by_id(self, user_id: str) -> _Account | None:
        client = self._redis_or_none()
        if client is None:
            return None
        username = await client.get(self._user_key(user_id))
        if not username:
            return None
        return await self._registered_by_username(username)

    async def _account_by_username(self, username: str) -> _Account | None:
        registered = await self._registered_by_username(username)
        return registered or self._accounts.get(username)

    async def _account_by_id(self, user_id: str) -> _Account | None:
        registered = await self._registered_by_id(user_id)
        return registered or self._accounts_by_id.get(user_id)

    async def _save_account(self, account: _Account) -> None:
        if account.user_id in self._accounts_by_id:
            self._accounts_by_id[account.user_id] = account
            self._accounts[account.username] = account
        client = self._redis_or_none()
        if client is None:
            return
        stored = _StoredAccount(
            username=account.username,
            user_id=account.user_id,
            salt=base64.b64encode(account.salt).decode("ascii"),
            password_digest=base64.b64encode(account.password_digest).decode("ascii"),
            created_at=datetime.now(timezone.utc).isoformat(),
            password_expires_at=(
                account.password_expires_at.isoformat()
                if account.password_expires_at is not None
                else None
            ),
            role=account.role,
            status=account.status,
            failed_attempts=account.failed_attempts,
            locked_until=account.locked_until,
            token_version=account.token_version,
            temporary_password_expires_at=account.temporary_password_expires_at,
        )
        await client.set(self._username_key(account.username), stored.model_dump_json())
        await client.set(self._user_key(account.user_id), account.username)

    @staticmethod
    def _lock_is_active(account: _Account) -> bool:
        if account.status != "locked":
            return False
        if not account.locked_until:
            return True
        try:
            return datetime.fromisoformat(account.locked_until) > datetime.now(
                timezone.utc,
            )
        except ValueError:
            return True

    @staticmethod
    def _temporary_password_is_expired(account: _Account) -> bool:
        if not account.temporary_password_expires_at:
            return False
        try:
            expires_at = datetime.fromisoformat(
                account.temporary_password_expires_at.replace("Z", "+00:00"),
            )
        except ValueError:
            return True
        if expires_at.tzinfo is None:
            return True
        return expires_at <= datetime.now(timezone.utc)

    async def authenticate(self, username: str, password: str) -> AuthUser:
        try:
            normalized = self._normalize_username(username)
        except ValueError as exc:
            raise _unauthorized("Invalid username or password.") from exc
        account = await self._account_by_username(normalized)
        if account is not None and account.password_expires_at is not None:
            if account.password_expires_at <= datetime.now(timezone.utc):
                account = None
        if account is None:
            await asyncio.to_thread(_derive_password, password, b"longxin-login--")
            raise _unauthorized("Invalid username or password.")
        if account.status in {"banned", "deleted"} or self._lock_is_active(account):
            raise _unauthorized("This account is not available.")

        if self._temporary_password_is_expired(account):
            raise _unauthorized("The temporary password has expired.")

        candidate = await asyncio.to_thread(_derive_password, password, account.salt)
        if not hmac.compare_digest(candidate, account.password_digest):
            attempts = account.failed_attempts + 1
            locked = attempts >= 5
            await self._save_account(
                replace(
                    account,
                    failed_attempts=attempts,
                    status="locked" if locked else account.status,
                    locked_until=(
                        (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
                        if locked
                        else account.locked_until
                    ),
                    token_version=account.token_version + (1 if locked else 0),
                ),
            )
            raise _unauthorized("Invalid username or password.")
        if account.failed_attempts or account.status == "locked":
            account = replace(
                account,
                failed_attempts=0,
                status="active",
                locked_until=None,
            )
            await self._save_account(account)
        return self._public_user(account)

    async def reset_password(
        self,
        identity: str,
        password: str,
        *,
        expires_at: datetime | None = None,
    ) -> AuthUser:
        """Reset a password by user id or username and persist the change."""
        try:
            account = await self._account_by_id(identity)
            if account is None:
                account = await self._account_by_username(
                    self._normalize_username(identity),
                )
        except ValueError as exc:
            raise ValueError("Invalid username.") from exc
        if not password:
            raise ValueError("A new password is required.")
        if account is None:
            raise ValueError("The requested account does not exist.")

        salt = secrets.token_bytes(16)
        digest = await asyncio.to_thread(_derive_password, password, salt)
        updated = replace(
            account,
            salt=salt,
            password_digest=digest,
            password_expires_at=expires_at,
            failed_attempts=0,
            locked_until=None,
            status="active" if account.status == "locked" else account.status,
            token_version=account.token_version + 1,
        )
        await self._save_account(updated)
        return self._public_user(updated)

    async def register(self, username: str, password: str) -> AuthUser:
        try:
            normalized = self._normalize_username(username)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        if await self._account_by_username(normalized):
            raise _account_conflict()

        salt = secrets.token_bytes(16)
        digest = await asyncio.to_thread(_derive_password, password, salt)
        user_id = f"user-{uuid4().hex}"
        stored = _StoredAccount(
            username=normalized,
            user_id=user_id,
            salt=base64.b64encode(salt).decode("ascii"),
            password_digest=base64.b64encode(digest).decode("ascii"),
            created_at=datetime.now(timezone.utc).isoformat(),
            role="user",
        )
        client = self._redis()
        created = await client.set(
            self._username_key(normalized),
            stored.model_dump_json(),
            nx=True,
        )
        if not created:
            raise _account_conflict()
        await client.set(self._user_key(user_id), normalized)
        if self._on_registered is not None:
            await self._on_registered(user_id)
        return self._public_user(self._account_from_stored(stored))

    async def create_user(self, username: str, password: str) -> AuthUser:
        """Create a normal user for the administrator API."""
        try:
            normalized = self._normalize_username(username)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        if await self._account_by_username(normalized):
            raise _account_conflict()
        salt = secrets.token_bytes(16)
        digest = await asyncio.to_thread(_derive_password, password, salt)
        account = _Account(
            username=normalized,
            user_id=f"user-{uuid4().hex}",
            salt=salt,
            password_digest=digest,
        )
        await self._save_account(account)
        if self._on_registered is not None:
            await self._on_registered(account.user_id)
        return self._public_user(account)

    async def list_accounts(self) -> list[AuthUser]:
        """Return configured and persisted accounts for administration."""
        accounts = dict(self._accounts_by_id)
        client = self._redis_or_none()
        if client is not None:
            async for key in client.scan_iter(
                match=f"{_AUTH_KEY_PREFIX}:username:*",
                count=100,
            ):
                raw = await client.get(key)
                if raw:
                    account = self._account_from_stored(
                        _StoredAccount.model_validate_json(raw),
                    )
                    accounts[account.user_id] = account
        return [
            self._public_user(account)
            for account in sorted(accounts.values(), key=lambda item: item.username)
        ]

    async def set_status(
        self,
        user_id: str,
        account_status: Literal["active", "locked", "banned", "deleted"],
    ) -> AuthUser:
        account = await self._account_by_id(user_id)
        if account is None:
            raise HTTPException(status_code=404, detail="User not found.")
        updated = replace(
            account,
            status=account_status,
            locked_until=None,
            token_version=account.token_version + 1,
        )
        await self._save_account(updated)
        return self._public_user(updated)

    async def verify_password(self, user_id: str, password: str) -> bool:
        account = await self._account_by_id(user_id)
        if account is None or self._temporary_password_is_expired(account):
            return False
        candidate = await asyncio.to_thread(_derive_password, password, account.salt)
        return hmac.compare_digest(candidate, account.password_digest)

    async def reset_password(
        self,
        user_id: str,
        password: str,
        *,
        temporary_password_expires_at: str | None = None,
    ) -> AuthUser:
        account = await self._account_by_id(user_id)
        if account is None:
            raise HTTPException(status_code=404, detail="User not found.")
        salt = secrets.token_bytes(16)
        digest = await asyncio.to_thread(_derive_password, password, salt)
        updated = replace(
            account,
            salt=salt,
            password_digest=digest,
            failed_attempts=0,
            locked_until=None,
            status="active" if account.status == "locked" else account.status,
            token_version=account.token_version + 1,
            temporary_password_expires_at=temporary_password_expires_at,
        )
        await self._save_account(updated)
        return self._public_user(updated)

    async def revoke_sessions(self, user_id: str) -> AuthUser:
        account = await self._account_by_id(user_id)
        if account is None:
            raise HTTPException(status_code=404, detail="User not found.")
        updated = replace(account, token_version=account.token_version + 1)
        await self._save_account(updated)
        return self._public_user(updated)

    def issue_access_token(self, user: AuthUser) -> tuple[str, int]:
        now = datetime.now(timezone.utc)
        expires = now + self._token_lifetime
        token = jwt.encode(
            {
                "sub": user.id,
                "username": user.username,
                "iss": self._issuer,
                "aud": self._audience,
                "iat": now,
                "exp": expires,
                "token_version": user.token_version,
            },
            self._secret,
            algorithm="HS256",
        )
        return token, int(self._token_lifetime.total_seconds())

    def _decode_identity(self, token: str) -> tuple[str, str, int]:
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                issuer=self._issuer,
                audience=self._audience,
                options={"require": ["sub", "username", "iss", "aud", "iat", "exp"]},
            )
        except jwt.InvalidTokenError as exc:
            raise _unauthorized("Invalid or expired access token.") from exc
        user_id = payload.get("sub")
        username = payload.get("username")
        if not isinstance(user_id, str) or not isinstance(username, str):
            raise _unauthorized("Invalid access token identity.")
        token_version = payload.get("token_version", 0)
        if not isinstance(token_version, int):
            raise _unauthorized("Invalid access token version.")
        return user_id, username, token_version

    async def get_current_user(
        self,
        authorization: str | None = Header(default=None),
    ) -> AuthUser:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise _unauthorized("Bearer access token is required.")
        user_id, username, token_version = self._decode_identity(token)
        account = await self._account_by_id(user_id)
        if (
            account is None
            or account.username != username
            or account.token_version != token_version
            or account.status in {"banned", "deleted"}
            or self._lock_is_active(account)
        ):
            raise _unauthorized("This user is no longer available.")
        return self._public_user(account)

    async def get_current_user_id(
        self,
        authorization: str | None = Header(default=None),
    ) -> str:
        return (await self.get_current_user(authorization)).id

    async def get_token_usage(
        self,
        user_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> TokenUsageResponse:
        """Aggregate persisted model usage, optionally within a time range."""
        client = self._redis()
        result = TokenUsageResponse()
        normalized_start = (
            start.astimezone(timezone.utc)
            if start is not None and start.tzinfo is not None
            else start.replace(tzinfo=timezone.utc) if start is not None else None
        )
        normalized_end = (
            end.astimezone(timezone.utc)
            if end is not None and end.tzinfo is not None
            else end.replace(tzinfo=timezone.utc) if end is not None else None
        )
        pattern = f"agentscope:user:{user_id}:session:*:messages"
        async for key in client.scan_iter(match=pattern, count=100):
            session_has_usage = False
            if normalized_start is None and normalized_end is None:
                result.session_count += 1
            for raw in await client.lrange(key, 0, -1):
                try:
                    message = json.loads(raw)
                    usage = message.get("usage")
                except (json.JSONDecodeError, AttributeError):
                    continue
                if not isinstance(usage, dict):
                    continue
                if start is not None or end is not None:
                    created_at = message.get("created_at")
                    try:
                        occurred_at = datetime.fromisoformat(
                            str(created_at).replace("Z", "+00:00"),
                        )
                        if occurred_at.tzinfo is None:
                            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
                        else:
                            occurred_at = occurred_at.astimezone(timezone.utc)
                    except (TypeError, ValueError):
                        # Old or malformed messages cannot be safely assigned
                        # to a requested period, so exclude them from that
                        # period while preserving all-time usage behavior.
                        continue
                    if normalized_start is not None:
                        if occurred_at < normalized_start:
                            continue
                    if normalized_end is not None:
                        if occurred_at >= normalized_end:
                            continue
                result.message_count += 1
                session_has_usage = True
                result.input_tokens += int(usage.get("input_tokens") or 0)
                result.output_tokens += int(usage.get("output_tokens") or 0)
                result.cache_input_tokens += int(usage.get("cache_input_tokens") or 0)
                result.cache_creation_input_tokens += int(
                    usage.get("cache_creation_input_tokens") or 0,
                )
            if normalized_start is not None or normalized_end is not None:
                if session_has_usage:
                    result.session_count += 1
        result.total_tokens = result.input_tokens + result.output_tokens
        return result

    @staticmethod
    def _login_response(user: AuthUser, token: str, expires_in: int) -> LoginResponse:
        return LoginResponse(access_token=token, expires_in=expires_in, user=user)

    def _build_router(self) -> APIRouter:
        router = APIRouter(prefix="/auth", tags=["auth"])

        @router.get("/health", status_code=status.HTTP_204_NO_CONTENT)
        async def auth_health() -> Response:
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        @router.post("/login", response_model=LoginResponse)
        async def login(body: LoginRequest) -> LoginResponse:
            user = await self.authenticate(body.username, body.password)
            token, expires_in = self.issue_access_token(user)
            return self._login_response(user, token, expires_in)

        @router.post(
            "/register",
            response_model=LoginResponse,
            status_code=status.HTTP_201_CREATED,
        )
        async def register(body: RegisterRequest) -> LoginResponse:
            user = await self.register(body.username, body.password)
            token, expires_in = self.issue_access_token(user)
            return self._login_response(user, token, expires_in)

        @router.get("/me", response_model=AuthUser)
        async def me(user: AuthUser = Depends(self.get_current_user)) -> AuthUser:
            return user

        @router.get("/usage", response_model=TokenUsageResponse)
        async def usage(
            user: AuthUser = Depends(self.get_current_user),
        ) -> TokenUsageResponse:
            return await self.get_token_usage(user.id)

        @router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
        async def logout(
            _: AuthUser = Depends(self.get_current_user),
        ) -> Response:
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        return router


def _parse_users(raw: str) -> dict[str, tuple[str, str, str]]:
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("AGENTSCOPE_AUTH_USERS must be valid JSON.") from exc
    if not isinstance(data, dict):
        raise ValueError("AGENTSCOPE_AUTH_USERS must be a JSON object.")

    users: dict[str, tuple[str, str, str]] = {}
    for username, config in data.items():
        if not isinstance(username, str):
            raise ValueError("Authentication usernames must be strings.")
        if isinstance(config, str):
            role = "admin" if username.strip().lower() == "admin" else "user"
            users[username] = (username, config, role)
            continue
        if not isinstance(config, dict):
            raise ValueError(
                f"Authentication config for {username!r} must be a password "
                "string or an object.",
            )
        password = config.get("password")
        user_id = config.get("user_id", username)
        role = config.get(
            "role",
            "admin" if username.strip().lower() == "admin" else "user",
        )
        if (
            not isinstance(password, str)
            or not isinstance(user_id, str)
            or role not in {"user", "admin"}
        ):
            raise ValueError(
                f"Authentication config for {username!r} requires string "
                "password, user_id and a valid role.",
            )
        users[username] = (user_id, password, role)
    return users


def load_auth_from_env(
    *,
    storage: Any | None = None,
    on_registered: Callable[[str], Awaitable[None]] | None = None,
) -> JWTAuthService:
    raw_users = os.getenv("AGENTSCOPE_AUTH_USERS")
    if raw_users:
        users = _parse_users(raw_users)
    else:
        username = os.getenv("AGENTSCOPE_USERNAME", "admin")
        user_id = os.getenv("AGENTSCOPE_USER_ID", "local-user")
        password = os.getenv("AGENTSCOPE_PASSWORD", "change-me")
        users = {username: (user_id, password, "admin")}

    return JWTAuthService(
        users,
        os.getenv(
            "AGENTSCOPE_JWT_SECRET",
            "change-me-in-production-use-32-bytes",
        ),
        storage=storage,
        on_registered=on_registered,
        token_minutes=int(
            os.getenv("AGENTSCOPE_JWT_EXPIRE_MINUTES", str(_DEFAULT_TOKEN_MINUTES)),
        ),
    )
