"""Logto organization-token authentication for the lxScope service."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx
import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status

from auth import AuthUser, TokenUsageResponse, _unauthorized
from .context import (
    current_tenant_id,
    reset_identity,
    scoped_user_id,
    set_identity,
    split_scoped_user_id,
)


_DEFAULT_ACCESS_SCOPE = "agent:use"
_DEFAULT_ADMIN_SCOPE = "tenant:manage"
_IDENTITY_PREFIX = "longxin:identity:v1"


class LogtoAuthService:
    """Validate organization-scoped Logto access tokens and expose app identity."""

    def __init__(
        self,
        *,
        endpoint: str,
        internal_endpoint: str | None = None,
        api_resource: str,
        storage: Any,
        jwks_uri: str | None = None,
        management_client_id: str | None = None,
        management_client_secret: str | None = None,
        management_api_resource: str | None = None,
        access_scope: str = _DEFAULT_ACCESS_SCOPE,
        admin_scope: str = _DEFAULT_ADMIN_SCOPE,
        on_authenticated: Any | None = None,
    ) -> None:
        if not endpoint.strip() or not api_resource.strip():
            raise ValueError("Logto endpoint and API resource are required.")
        self._issuer = endpoint.rstrip("/")
        if not self._issuer.endswith("/oidc"):
            self._issuer += "/oidc"
        self._internal_issuer = (internal_endpoint or endpoint).rstrip("/")
        if not self._internal_issuer.endswith("/oidc"):
            self._internal_issuer += "/oidc"
        self._jwks_uri = (jwks_uri or f"{self._issuer}/jwks").strip()
        self._api_resource = api_resource.strip()
        self._storage = storage
        self._management_client_id = (management_client_id or "").strip()
        self._management_client_secret = (management_client_secret or "").strip()
        self._management_api_resource = (management_api_resource or "").strip()
        self._access_scope = access_scope.strip()
        self._admin_scope = admin_scope.strip()
        self._on_authenticated = on_authenticated
        self._jwks: dict[str, Any] = {}
        self._jwks_expires_at = 0.0
        self._jwks_lock = asyncio.Lock()
        self._management_token = ""
        self._management_token_expires_at = 0.0
        self._management_token_lock = asyncio.Lock()
        self._profile_cache: dict[str, tuple[float, str | None]] = {}
        self.router = self._build_router()

    async def _get_management_token(self) -> str | None:
        if not (
            self._management_client_id
            and self._management_client_secret
            and self._management_api_resource
        ):
            return None
        if self._management_token and time.monotonic() < self._management_token_expires_at:
            return self._management_token
        async with self._management_token_lock:
            if self._management_token and time.monotonic() < self._management_token_expires_at:
                return self._management_token
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.post(
                        f"{self._internal_issuer}/token",
                        data={
                            "grant_type": "client_credentials",
                            "client_id": self._management_client_id,
                            "client_secret": self._management_client_secret,
                            "resource": self._management_api_resource,
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
            except (httpx.HTTPError, ValueError):
                return None
            token = payload.get("access_token") if isinstance(payload, dict) else None
            if not isinstance(token, str) or not token:
                return None
            try:
                expires_in = max(1, int(payload.get("expires_in", 300)))
            except (TypeError, ValueError):
                expires_in = 300
            self._management_token = token
            self._management_token_expires_at = time.monotonic() + max(1, expires_in - 30)
            return token

    async def _logto_username(self, subject_id: str) -> str | None:
        """Resolve a Logto user profile without making identity lookup mandatory."""
        if not (
            self._management_client_id
            and self._management_client_secret
            and self._management_api_resource
        ):
            return None
        cached = self._profile_cache.get(subject_id)
        if cached and time.monotonic() < cached[0]:
            return cached[1]

        token = await self._get_management_token()
        if token is None:
            return None
        management_api = f"{self._internal_issuer.removesuffix('/oidc')}/api"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"{management_api}/users/{quote(subject_id, safe='')}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()
                profile = response.json()
        except (httpx.HTTPError, ValueError):
            return None

        username = None
        if isinstance(profile, dict):
            username = next(
                (
                    value.strip()
                    for value in (
                        profile.get("username"),
                        profile.get("name"),
                        profile.get("primaryEmail"),
                    )
                    if isinstance(value, str) and value.strip()
                ),
                None,
            )
        self._profile_cache[subject_id] = (time.monotonic() + 300, username)
        return username

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _tenant_index_key(self, tenant_id: str) -> str:
        return f"{_IDENTITY_PREFIX}:tenant:{self._digest(tenant_id)}:members"

    def _member_key(self, tenant_id: str, subject_id: str) -> str:
        return (
            f"{_IDENTITY_PREFIX}:tenant:{self._digest(tenant_id)}:"
            f"member:{self._digest(subject_id)}"
        )

    def _member_status_key(self, tenant_id: str, subject_id: str) -> str:
        return f"{self._member_key(tenant_id, subject_id)}:status"

    def _client(self) -> Any:
        get_base_client = getattr(self._storage, "get_base_client", None)
        client = (
            get_base_client()
            if callable(get_base_client)
            else self._storage.get_client()
        )
        if client is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Identity storage is not ready.",
            )
        return client

    async def _load_jwks(self) -> dict[str, Any]:
        if self._jwks and time.monotonic() < self._jwks_expires_at:
            return self._jwks
        async with self._jwks_lock:
            if self._jwks and time.monotonic() < self._jwks_expires_at:
                return self._jwks
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.get(self._jwks_uri)
                    response.raise_for_status()
                    payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Unable to load Logto signing keys.",
                ) from exc
            keys = payload.get("keys") if isinstance(payload, dict) else None
            if not isinstance(keys, list):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Logto returned an invalid signing-key set.",
                )
            self._jwks = {
                item["kid"]: item
                for item in keys
                if isinstance(item, dict) and isinstance(item.get("kid"), str)
            }
            self._jwks_expires_at = time.monotonic() + 900
            return self._jwks

    async def _decode(self, token: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
            algorithm = header.get("alg")
            algorithm_types = {
                "RS256": jwt.algorithms.RSAAlgorithm,
                "ES384": jwt.algorithms.ECAlgorithm,
            }
            algorithm_type = algorithm_types.get(algorithm)
            if algorithm_type is None or not isinstance(header.get("kid"), str):
                raise jwt.InvalidTokenError("Unsupported signing key.")
            jwk = (await self._load_jwks()).get(header["kid"])
            if jwk is None:
                self._jwks_expires_at = 0
                jwk = (await self._load_jwks()).get(header["kid"])
            if jwk is None:
                raise jwt.InvalidTokenError("Unknown signing key.")
            if jwk.get("alg") not in (None, algorithm):
                raise jwt.InvalidTokenError("Token algorithm does not match its signing key.")
            key = algorithm_type.from_jwk(json.dumps(jwk))
            claims = jwt.decode(
                token,
                key=key,
                algorithms=[algorithm],
                issuer=self._issuer,
                audience=self._api_resource,
                options={
                    "require": ["sub", "iss", "aud", "iat", "exp", "organization_id"],
                },
            )
        except HTTPException:
            raise
        except (jwt.InvalidTokenError, TypeError, ValueError, KeyError) as exc:
            raise _unauthorized("Invalid or expired Logto organization token.") from exc
        if not isinstance(claims, dict):
            raise _unauthorized("Invalid Logto token claims.")
        return claims

    @staticmethod
    def _scopes(claims: dict[str, Any]) -> set[str]:
        raw = claims.get("scope", "")
        if isinstance(raw, str):
            return set(raw.split())
        if isinstance(raw, list):
            return {item for item in raw if isinstance(item, str)}
        return set()

    async def get_current_user(
        self,
        authorization: str | None = Header(default=None),
    ) -> AuthUser:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise _unauthorized("Bearer Logto access token is required.")
        claims = await self._decode(token)
        subject_id = claims.get("sub")
        tenant_id = claims.get("organization_id")
        if (
            not isinstance(subject_id, str)
            or not subject_id
            or not isinstance(tenant_id, str)
            or not tenant_id
        ):
            raise _unauthorized("The Logto token has no valid organization identity.")
        scopes = self._scopes(claims)
        if self._access_scope not in scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The organization role does not grant lxScope access.",
            )
        role = "admin" if self._admin_scope in scopes else "user"
        claim_username = next(
            (
                value.strip()
                for value in (claims.get("username"), claims.get("email"), claims.get("name"))
                if isinstance(value, str) and value.strip()
            ),
            None,
        )
        username = await self._logto_username(subject_id) or claim_username or subject_id
        user = AuthUser(
            id=scoped_user_id(tenant_id, subject_id),
            username=username,
            role=role,
            status="active",
            capabilities=(
                ["tenant.access", "tenant.manage"]
                if role == "admin"
                else ["tenant.access"]
            ),
            tenant_id=tenant_id,
            subject_id=subject_id,
            organization_scopes=sorted(scopes),
        )
        new_member, user = await self._remember_user(user)
        if user.status != "active":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This organization member is disabled in lxScope.",
            )
        if new_member and self._on_authenticated is not None:
            identity_token = set_identity(user)
            try:
                await self._on_authenticated(user)
            finally:
                reset_identity(identity_token)
        return user

    async def _remember_user(self, user: AuthUser) -> tuple[bool, AuthUser]:
        client = self._client()
        tenant_id = user.tenant_id or ""
        subject_id = user.subject_id or ""
        member_key = self._member_key(tenant_id, subject_id)
        raw_existing = await client.get(member_key)
        is_new = not bool(raw_existing)
        previous_status = "active"
        previous_username = ""
        if raw_existing:
            try:
                previous = AuthUser.model_validate_json(raw_existing)
                previous_status = previous.status
                previous_username = previous.username
            except ValueError:
                pass
        if user.username == subject_id and previous_username and previous_username != subject_id:
            user = user.model_copy(update={"username": previous_username})
        stored_status = await client.get(self._member_status_key(tenant_id, subject_id))
        if isinstance(stored_status, bytes):
            stored_status = stored_status.decode("utf-8", errors="ignore")
        status_value = (
            stored_status
            if stored_status in {"active", "locked", "banned", "deleted"}
            else previous_status
        )
        user = user.model_copy(update={"status": status_value})
        await client.set(member_key, user.model_dump_json())
        if status_value != "active" and not stored_status:
            await client.set(self._member_status_key(tenant_id, subject_id), status_value)
        await client.sadd(self._tenant_index_key(tenant_id), subject_id)
        return is_new, user

    async def get_current_user_id(
        self,
        authorization: str | None = Header(default=None),
    ) -> str:
        return (await self.get_current_user(authorization)).id

    async def list_accounts(self, tenant_id: str | None = None) -> list[AuthUser]:
        tenant = tenant_id or current_tenant_id()
        if not tenant:
            return []
        client = self._client()
        subjects = await client.smembers(self._tenant_index_key(tenant))
        users: list[AuthUser] = []
        for subject in subjects:
            raw = await client.get(self._member_key(tenant, str(subject)))
            if raw:
                try:
                    account = AuthUser.model_validate_json(raw)
                except ValueError:
                    continue
                stored_status = await client.get(
                    self._member_status_key(tenant, str(subject)),
                )
                if isinstance(stored_status, bytes):
                    stored_status = stored_status.decode("utf-8", errors="ignore")
                if stored_status in {"active", "locked", "banned", "deleted"}:
                    account = account.model_copy(update={"status": stored_status})
                users.append(account)
        semaphore = asyncio.Semaphore(10)

        async def resolve_username(account: AuthUser) -> AuthUser:
            if account.username != (account.subject_id or ""):
                return account
            async with semaphore:
                username = await self._logto_username(account.subject_id or "")
            if username:
                return account.model_copy(update={"username": username})
            return account

        users = list(await asyncio.gather(*(resolve_username(account) for account in users)))
        return sorted(users, key=lambda account: (account.username.lower(), account.id))

    async def _account_by_id(self, user_id: str) -> AuthUser | None:
        parts = split_scoped_user_id(user_id)
        if parts is None:
            return None
        tenant_id, subject_id = parts
        active_tenant_id = current_tenant_id()
        # Background chat resumes carry the authenticated composite user id
        # but no request-local identity ContextVar. The id still scopes this
        # lookup to one tenant; reject only when an explicit request context
        # names a different tenant.
        if active_tenant_id is not None and active_tenant_id != tenant_id:
            return None
        client = self._client()
        raw = await client.get(self._member_key(tenant_id, subject_id))
        if not raw:
            return None
        try:
            account = AuthUser.model_validate_json(raw)
        except ValueError:
            return None
        stored_status = await client.get(self._member_status_key(tenant_id, subject_id))
        if isinstance(stored_status, bytes):
            stored_status = stored_status.decode("utf-8", errors="ignore")
        if stored_status in {"active", "locked", "banned", "deleted"}:
            account = account.model_copy(update={"status": stored_status})
        return account

    async def _account_by_username(self, username: str) -> AuthUser | None:
        for account in await self.list_accounts():
            if account.username.casefold() == username.casefold():
                return account
        return None

    @staticmethod
    def _public_user(account: AuthUser) -> AuthUser:
        return account

    async def is_admin_user(self, user_id: str) -> bool:
        account = await self._account_by_id(user_id)
        return bool(account and account.status == "active" and account.role == "admin")

    async def get_token_usage(self, user_id: str, *, start: datetime | None = None, end: datetime | None = None) -> TokenUsageResponse:
        client = self._client()
        result = TokenUsageResponse()
        pattern = f"agentscope:user:{user_id}:session:*:messages"
        start_utc = start.astimezone(timezone.utc) if start and start.tzinfo else start
        end_utc = end.astimezone(timezone.utc) if end and end.tzinfo else end
        async for key in client.scan_iter(match=pattern, count=100):
            session_has_usage = False
            if start_utc is None and end_utc is None:
                result.session_count += 1
            for raw in await client.lrange(key, 0, -1):
                try:
                    message = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                usage = message.get("usage") if isinstance(message, dict) else None
                if not isinstance(usage, dict):
                    continue
                if start_utc is not None or end_utc is not None:
                    try:
                        occurred = datetime.fromisoformat(
                            str(message.get("created_at", "")).replace("Z", "+00:00"),
                        )
                        if occurred.tzinfo is None:
                            occurred = occurred.replace(tzinfo=timezone.utc)
                        else:
                            occurred = occurred.astimezone(timezone.utc)
                    except ValueError:
                        continue
                    if start_utc is not None and occurred < start_utc:
                        continue
                    if end_utc is not None and occurred >= end_utc:
                        continue
                result.message_count += 1
                session_has_usage = True
                result.input_tokens += int(usage.get("input_tokens") or 0)
                result.output_tokens += int(usage.get("output_tokens") or 0)
                result.cache_input_tokens += int(usage.get("cache_input_tokens") or 0)
                result.cache_creation_input_tokens += int(
                    usage.get("cache_creation_input_tokens") or 0,
                )
            if (start_utc is not None or end_utc is not None) and session_has_usage:
                result.session_count += 1
        result.total_tokens = result.input_tokens + result.output_tokens
        return result

    async def create_user(self, *_: Any, **__: Any) -> AuthUser:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User accounts and invitations are managed by Logto.",
        )

    async def set_status(
        self,
        user_id: str,
        account_status: str,
    ) -> AuthUser:
        if account_status not in {"active", "locked", "banned", "deleted"}:
            raise HTTPException(status_code=422, detail="Invalid member status.")
        account = await self._account_by_id(user_id)
        if account is None or not account.tenant_id or not account.subject_id:
            raise HTTPException(status_code=404, detail="User not found.")
        updated = account.model_copy(update={"status": account_status})
        client = self._client()
        await client.set(
            self._member_status_key(account.tenant_id, account.subject_id),
            account_status,
        )
        await client.set(
            self._member_key(account.tenant_id, account.subject_id),
            updated.model_dump_json(),
        )
        return updated

    async def reset_password(self, *_: Any, **__: Any) -> AuthUser:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Passwords are managed by Logto.",
        )

    async def revoke_sessions(self, user_id: str) -> AuthUser:
        account = await self._account_by_id(user_id)
        if account is None:
            raise HTTPException(status_code=404, detail="User not found.")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Session revocation is managed by Logto.",
        )

    async def verify_password(self, *_: Any, **__: Any) -> bool:
        return False

    def _build_router(self) -> APIRouter:
        router = APIRouter(prefix="/auth", tags=["auth"])

        @router.get("/health", status_code=status.HTTP_204_NO_CONTENT)
        async def auth_health() -> Response:
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        @router.get("/me", response_model=AuthUser)
        async def me(user: AuthUser = Depends(self.get_current_user)) -> AuthUser:
            return user

        @router.get("/usage", response_model=TokenUsageResponse)
        async def usage(user: AuthUser = Depends(self.get_current_user)) -> TokenUsageResponse:
            return await self.get_token_usage(user.id)

        @router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
        async def logout(_: AuthUser = Depends(self.get_current_user)) -> Response:
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        return router
