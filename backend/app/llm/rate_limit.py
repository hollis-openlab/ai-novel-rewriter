from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Awaitable

from backend.app.core.errors import AppError, ErrorCode

Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]


@dataclass(slots=True)
class _TokenBucket:
    capacity: float
    refill_per_second: float
    tokens: float = field(init=False)
    updated_at: float = field(init=False)

    def __post_init__(self) -> None:
        self.tokens = float(self.capacity)
        self.updated_at = 0.0

    def reset(self, now: float) -> None:
        self.tokens = float(self.capacity)
        self.updated_at = now

    def refill(self, now: float) -> None:
        if now <= self.updated_at:
            return
        elapsed = now - self.updated_at
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
        self.updated_at = now

    def time_until(self, required: float, now: float) -> float:
        self.refill(now)
        if self.tokens >= required:
            return 0.0
        missing = required - self.tokens
        return missing / self.refill_per_second

    def consume(self, required: float) -> None:
        self.tokens -= required
        if self.tokens < 0:
            self.tokens = 0.0


@dataclass(slots=True)
class ProviderRateLimitPermit:
    provider_id: str
    request_tokens: float

    async def __aenter__(self) -> "ProviderRateLimitPermit":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class ProviderRateLimiter:
    def __init__(
        self,
        provider_id: str,
        rpm_limit: int,
        tpm_limit: int,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self.provider_id = provider_id
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._rpm_limit = 0
        self._tpm_limit = 0
        self._rpm_bucket = _TokenBucket(1.0, 1.0)
        self._tpm_bucket = _TokenBucket(1.0, 1.0)
        self.configure(rpm_limit, tpm_limit)

    @property
    def rpm_limit(self) -> int:
        return self._rpm_limit

    @property
    def tpm_limit(self) -> int:
        return self._tpm_limit

    def configure(self, rpm_limit: int, tpm_limit: int) -> None:
        if rpm_limit <= 0:
            raise AppError(ErrorCode.VALIDATION_ERROR, "rpm_limit must be greater than 0")
        if tpm_limit <= 0:
            raise AppError(ErrorCode.VALIDATION_ERROR, "tpm_limit must be greater than 0")

        now = self._clock()
        self._rpm_limit = int(rpm_limit)
        self._tpm_limit = int(tpm_limit)
        self._rpm_bucket = _TokenBucket(float(rpm_limit), float(rpm_limit) / 60.0)
        self._tpm_bucket = _TokenBucket(float(tpm_limit), float(tpm_limit) / 60.0)
        self._rpm_bucket.reset(now)
        self._tpm_bucket.reset(now)

    async def acquire(self, request_tokens: int = 1) -> ProviderRateLimitPermit:
        required_tokens = float(request_tokens)
        if not math.isfinite(required_tokens) or required_tokens <= 0:
            raise AppError(ErrorCode.VALIDATION_ERROR, "request_tokens must be greater than 0")
        if required_tokens > self._tpm_limit:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "request_tokens cannot exceed the provider TPM limit",
                details={"request_tokens": request_tokens, "tpm_limit": self._tpm_limit},
            )

        while True:
            async with self._lock:
                now = self._clock()
                rpm_wait = self._rpm_bucket.time_until(1.0, now)
                tpm_wait = self._tpm_bucket.time_until(required_tokens, now)
                wait_for = max(rpm_wait, tpm_wait)
                if wait_for <= 0:
                    self._rpm_bucket.consume(1.0)
                    self._tpm_bucket.consume(required_tokens)
                    return ProviderRateLimitPermit(self.provider_id, required_tokens)

            await self._sleep(wait_for)

    async def __aenter__(self) -> "ProviderRateLimiter":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class ProviderRateLimitManager:
    def __init__(
        self,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._limiters: dict[str, ProviderRateLimiter] = {}
        self._lock = asyncio.Lock()

    async def acquire(
        self,
        provider_id: str,
        rpm_limit: int,
        tpm_limit: int,
        *,
        request_tokens: int = 1,
    ) -> ProviderRateLimitPermit:
        limiter = self.get_limiter(provider_id, rpm_limit, tpm_limit)
        return await limiter.acquire(request_tokens=request_tokens)

    def get_limiter(self, provider_id: str, rpm_limit: int, tpm_limit: int) -> ProviderRateLimiter:
        limiter = self._limiters.get(provider_id)
        if limiter is None:
            limiter = ProviderRateLimiter(
                provider_id,
                rpm_limit,
                tpm_limit,
                clock=self._clock,
                sleep=self._sleep,
            )
            self._limiters[provider_id] = limiter
            return limiter

        if limiter.rpm_limit != rpm_limit or limiter.tpm_limit != tpm_limit:
            limiter.configure(rpm_limit, tpm_limit)
        return limiter

    async def clear(self) -> None:
        async with self._lock:
            self._limiters.clear()
