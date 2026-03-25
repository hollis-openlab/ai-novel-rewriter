from __future__ import annotations

import asyncio

import pytest

from backend.app.core.errors import AppError
from backend.app.llm.rate_limit import ProviderRateLimitManager, ProviderRateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_rate_limiter_waits_for_tokens() -> None:
    async def _run() -> None:
        clock = FakeClock()
        limiter = ProviderRateLimiter(
            "provider-a",
            rpm_limit=2,
            tpm_limit=60,
            clock=clock.monotonic,
            sleep=clock.sleep,
        )

        await limiter.acquire(request_tokens=30)
        await limiter.acquire(request_tokens=30)
        await limiter.acquire(request_tokens=30)

        assert clock.sleeps == [30.0]

    asyncio.run(_run())


def test_rate_limit_manager_keeps_providers_isolated() -> None:
    async def _run() -> None:
        clock = FakeClock()
        manager = ProviderRateLimitManager(clock=clock.monotonic, sleep=clock.sleep)

        limiter_a = manager.get_limiter("provider-a", rpm_limit=1, tpm_limit=10)
        limiter_b = manager.get_limiter("provider-b", rpm_limit=1, tpm_limit=10)

        assert limiter_a is manager.get_limiter("provider-a", rpm_limit=1, tpm_limit=10)
        assert limiter_a is not limiter_b

        await limiter_a.acquire(request_tokens=10)
        await limiter_a.acquire(request_tokens=10)

        sleep_count = len(clock.sleeps)
        await limiter_b.acquire(request_tokens=10)

        assert len(clock.sleeps) == sleep_count

    asyncio.run(_run())


def test_rate_limiter_rejects_oversized_request() -> None:
    async def _run() -> None:
        limiter = ProviderRateLimiter("provider-a", rpm_limit=1, tpm_limit=10)
        with pytest.raises(AppError):
            await limiter.acquire(request_tokens=11)

    asyncio.run(_run())
