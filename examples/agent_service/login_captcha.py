"""Redis-backed one-time image challenges for local password authentication."""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import secrets
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFont


_KEY_PREFIX = "longxin:auth:captcha"
_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_CODE_LENGTH = 5
_CREATE_CHALLENGE_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 or redis.call('EXISTS', KEYS[2]) == 1 then
  return 0
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', tonumber(ARGV[2]))
redis.call('SET', KEYS[2], 'active', 'EX', tonumber(ARGV[3]))
return 1
"""
_INCREMENT_LIMIT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
end
return count
"""
_CONSUME_CHALLENGE_SCRIPT = """
local digest = redis.call('GET', KEYS[1])
if digest then
  redis.call('DEL', KEYS[1])
  redis.call('SET', KEYS[2], 'used', 'EX', tonumber(ARGV[1]))
  return {1, digest}
end
local state = redis.call('GET', KEYS[2])
if state == 'active' then
  redis.call('SET', KEYS[2], 'expired', 'EX', tonumber(ARGV[1]))
  return {0, 'expired'}
end
if state == 'used' then
  return {0, 'used'}
end
if state == 'expired' then
  return {0, 'expired'}
end
return {0, 'missing'}
"""


class LoginCaptchaError(Exception):
    """A safe, user-facing CAPTCHA rejection with a stable API code."""

    def __init__(self, code: str, status_code: int) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class LoginCaptchaService:
    """Create and atomically consume Redis-backed image challenges."""

    def __init__(
        self,
        storage: Any | None,
        secret: str,
        *,
        ttl_seconds: int = 300,
        rate_window_seconds: int = 600,
        max_challenges_per_window: int = 20,
        max_attempts_per_window: int = 30,
        code_factory: Callable[[], str] | None = None,
        key_prefix: str = _KEY_PREFIX,
    ) -> None:
        if len(secret.encode("utf-8")) < 32:
            raise ValueError("The login CAPTCHA secret must be at least 32 bytes long.")
        if ttl_seconds <= 0 or rate_window_seconds <= 0:
            raise ValueError("CAPTCHA expiry and rate-limit windows must be positive.")
        if max_challenges_per_window <= 0 or max_attempts_per_window <= 0:
            raise ValueError("CAPTCHA rate limits must be positive.")

        self._storage = storage
        self._secret = hmac.new(
            secret.encode("utf-8"),
            b"longxin:auth:captcha:key:v1",
            hashlib.sha256,
        ).digest()
        self.ttl_seconds = ttl_seconds
        self._rate_window_seconds = rate_window_seconds
        self._max_challenges_per_window = max_challenges_per_window
        self._max_attempts_per_window = max_attempts_per_window
        self._code_factory = code_factory or self._new_code
        self._key_prefix = key_prefix.rstrip(":")

    @staticmethod
    def _new_code() -> str:
        return "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH))

    def _redis(self) -> Any:
        client = self._storage.get_client() if self._storage is not None else None
        if client is None:
            raise LoginCaptchaError("captcha_unavailable", 503)
        return client

    def _source_digest(self, client_ip: str) -> str:
        return hmac.new(
            self._secret,
            f"source\0{client_ip}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    async def _increment_limit(self, key: str, limit: int) -> None:
        count = await self._redis().eval(
            _INCREMENT_LIMIT_SCRIPT,
            1,
            key,
            self._rate_window_seconds,
        )
        if int(count) > limit:
            raise LoginCaptchaError("captcha_rate_limited", 429)

    def _challenge_digest(self, challenge_id: str, answer: str, client_ip: str) -> str:
        message = f"{challenge_id}\0{answer.strip().upper()}\0{client_ip}".encode("utf-8")
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    @staticmethod
    def _render_image(answer: str) -> bytes:
        width, height = 156, 52
        image = Image.new("RGBA", (width, height), "#0b1220")
        draw = ImageDraw.Draw(image, "RGBA")
        rng = secrets.SystemRandom()
        for x in range(0, width, 13):
            draw.line((x, 0, x, height), fill=(50, 75, 103, 35), width=1)
        for y in range(0, height, 13):
            draw.line((0, y, width, y), fill=(50, 75, 103, 35), width=1)
        for _ in range(6):
            draw.line(
                (
                    rng.randrange(width),
                    rng.randrange(height),
                    rng.randrange(width),
                    rng.randrange(height),
                ),
                fill=(54, 211, 190, 85),
                width=1,
            )
        for _ in range(36):
            x, y = rng.randrange(width), rng.randrange(height)
            draw.ellipse((x, y, x + 1, y + 1), fill=(115, 164, 204, 100))

        try:
            font = ImageFont.load_default(size=30)
        except TypeError:  # pragma: no cover - compatibility with older Pillow
            font = ImageFont.load_default()
        colors = ("#e5f2ff", "#66e3d1", "#a9c7ff", "#ffd08a", "#e5f2ff")
        for index, character in enumerate(answer):
            glyph = Image.new("RGBA", (30, 42), (0, 0, 0, 0))
            glyph_draw = ImageDraw.Draw(glyph)
            bounds = glyph_draw.textbbox((0, 0), character, font=font)
            glyph_width = bounds[2] - bounds[0]
            glyph_height = bounds[3] - bounds[1]
            glyph_draw.text(
                ((30 - glyph_width) / 2 - bounds[0], (42 - glyph_height) / 2 - bounds[1]),
                character,
                font=font,
                fill=colors[index % len(colors)],
            )
            rotated = glyph.rotate(rng.randint(-13, 13), resample=Image.Resampling.BICUBIC)
            image.alpha_composite(rotated, (7 + index * 29, 5 + rng.randint(0, 5)))

        output = io.BytesIO()
        image.convert("RGB").save(output, format="PNG", optimize=True)
        return output.getvalue()

    async def issue(self, client_ip: str) -> dict[str, Any]:
        source_digest = self._source_digest(client_ip)
        await self._increment_limit(
            f"{self._key_prefix}:issue:{source_digest}",
            self._max_challenges_per_window,
        )

        redis = self._redis()
        for _ in range(3):
            challenge_id = secrets.token_urlsafe(24)
            answer = self._code_factory().strip().upper()
            digest = self._challenge_digest(challenge_id, answer, client_ip)
            image_data = self._render_image(answer)
            created = await redis.eval(
                _CREATE_CHALLENGE_SCRIPT,
                2,
                f"{self._key_prefix}:challenge:{challenge_id}",
                f"{self._key_prefix}:state:{challenge_id}",
                digest,
                self.ttl_seconds,
                self.ttl_seconds * 2,
            )
            if int(created) == 1:
                image_uri = "data:image/png;base64," + base64.b64encode(image_data).decode("ascii")
                return {
                    "enabled": True,
                    "challengeId": challenge_id,
                    "imageDataUrl": image_uri,
                    "expiresIn": self.ttl_seconds,
                }
        raise LoginCaptchaError("captcha_unavailable", 503)

    async def verify(self, challenge_id: str | None, answer: str | None, client_ip: str) -> None:
        source_digest = self._source_digest(client_ip)
        await self._increment_limit(
            f"{self._key_prefix}:attempt:{source_digest}",
            self._max_attempts_per_window,
        )
        if not challenge_id or not answer or not answer.strip():
            raise LoginCaptchaError("captcha_required", 400)
        if len(challenge_id) > 64 or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for character in challenge_id
        ):
            raise LoginCaptchaError("captcha_invalid", 400)

        keys = (
            f"{self._key_prefix}:challenge:{challenge_id}",
            f"{self._key_prefix}:state:{challenge_id}",
        )
        consumed = await self._redis().eval(
            _CONSUME_CHALLENGE_SCRIPT,
            2,
            *keys,
            self.ttl_seconds * 2,
        )
        result = _decode(consumed[0])
        stored_digest = _decode(consumed[1]) if len(consumed) > 1 else ""
        if result != "1":
            if stored_digest == "expired":
                raise LoginCaptchaError("captcha_expired", 400)
            if stored_digest == "used":
                raise LoginCaptchaError("captcha_used", 400)
            raise LoginCaptchaError("captcha_invalid", 400)

        expected = self._challenge_digest(challenge_id, answer, client_ip)
        if not hmac.compare_digest(stored_digest, expected):
            raise LoginCaptchaError("captcha_invalid", 400)


def _decode(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
