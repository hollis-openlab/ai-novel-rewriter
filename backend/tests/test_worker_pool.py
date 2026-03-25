from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes.workers import router as workers_router
from backend.app.services.worker_pool import WorkerPool


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeRateLimitPermit:
    async def __aenter__(self) -> "FakeRateLimitPermit":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class FakeRateLimitManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int, int]] = []

    async def acquire(
        self,
        provider_id: str,
        rpm_limit: int,
        tpm_limit: int,
        *,
        request_tokens: int = 1,
    ) -> FakeRateLimitPermit:
        self.calls.append((provider_id, rpm_limit, tpm_limit, request_tokens))
        return FakeRateLimitPermit()


def test_worker_pool_executes_priority_jobs_in_fifo_order() -> None:
    async def _run() -> None:
        pool = WorkerPool(initial_workers=1)
        await pool.start()
        order: list[str] = []
        started = asyncio.Event()
        release = asyncio.Event()

        async def gate_job() -> str:
            order.append("gate-start")
            started.set()
            await release.wait()
            order.append("gate-end")
            return "gate"

        async def make_job(label: str) -> str:
            order.append(label)
            return label

        gate_task = asyncio.create_task(pool.submit(gate_job, priority=5))
        await started.wait()

        high_one_task = asyncio.create_task(pool.submit(lambda: make_job("high-1"), priority=0))
        high_two_task = asyncio.create_task(pool.submit(lambda: make_job("high-2"), priority=0))
        low_task = asyncio.create_task(pool.submit(lambda: make_job("low"), priority=10))

        release.set()

        assert await gate_task == "gate"
        assert await high_one_task == "high-1"
        assert await high_two_task == "high-2"
        assert await low_task == "low"
        assert order == ["gate-start", "gate-end", "high-1", "high-2", "low"]

        status = pool.status()
        assert status.completed_total == 4
        assert status.failed_total == 0

        await pool.close()

    asyncio.run(_run())


def test_worker_pool_retries_with_exponential_backoff() -> None:
    async def _run() -> None:
        clock = FakeClock()
        pool = WorkerPool(initial_workers=1, clock=clock.monotonic, sleep=clock.sleep)
        await pool.start()
        attempts = 0

        async def flaky_job() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise RuntimeError(f"boom-{attempts}")
            return "ok"

        result = await pool.submit(flaky_job)

        assert result == "ok"
        assert attempts == 3
        assert clock.sleeps == [1.0, 2.0]

        status = pool.status()
        assert status.retry_total == 2
        assert status.completed_total == 1
        assert status.failed_total == 0

        await pool.close()

    asyncio.run(_run())


def test_worker_pool_integrates_rate_limiter() -> None:
    async def _run() -> None:
        fake_manager = FakeRateLimitManager()
        pool = WorkerPool(initial_workers=1, rate_limit_manager=fake_manager)
        await pool.start()

        async def job() -> str:
            return "done"

        result = await pool.submit(
            job,
            provider_id="provider-a",
            rpm_limit=60,
            tpm_limit=1000,
            request_tokens=321,
        )

        assert result == "done"
        assert fake_manager.calls == [("provider-a", 60, 1000, 321)]

        await pool.close()

    asyncio.run(_run())


def test_worker_pool_can_scale_up_and_reclaim_idle_workers() -> None:
    async def _run() -> None:
        pool = WorkerPool(initial_workers=1)
        await pool.start()
        await pool.set_worker_count(2)

        started = asyncio.Event()
        release = asyncio.Event()

        async def blocking_job() -> str:
            started.set()
            await release.wait()
            return "done"

        job_task = asyncio.create_task(pool.submit(blocking_job))
        await started.wait()

        before_resize = pool.status()
        assert before_resize.running_workers == 2
        assert before_resize.active_workers == 1
        assert before_resize.idle_workers == 1

        resized = await pool.set_worker_count(1)
        assert resized.target_workers == 1
        assert resized.running_workers == 1

        release.set()
        assert await job_task == "done"

        after_resize = pool.status()
        assert after_resize.target_workers == 1
        assert after_resize.running_workers == 1
        assert after_resize.idle_workers == 1

        await pool.close()

    asyncio.run(_run())


def test_worker_routes_use_pool_state() -> None:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = WorkerPool(initial_workers=1)
        await pool.start()
        app.state.worker_pool = pool
        try:
            yield
        finally:
            await pool.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(workers_router)

    with TestClient(app) as client:
        status = client.get("/workers/status")
        assert status.status_code == 200
        assert status.json() == {"active": 0, "idle": 1, "queue_size": 0}

        resized = client.put("/workers/count", json={"count": 2})
        assert resized.status_code == 200
        assert resized.json() == {"count": 2}

        status_after_resize = client.get("/workers/status")
        assert status_after_resize.status_code == 200
        assert status_after_resize.json() == {"active": 0, "idle": 2, "queue_size": 0}

