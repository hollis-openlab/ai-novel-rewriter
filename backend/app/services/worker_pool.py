from __future__ import annotations

import asyncio
import inspect
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from backend.app.core.errors import AppError, ErrorCode
from backend.app.llm.rate_limit import ProviderRateLimitManager

Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]
JobCallable = Callable[[], Any | Awaitable[Any]]


@dataclass(slots=True)
class WorkerPoolStatus:
    """Snapshot of the current worker pool state.

    The pool keeps richer internal metrics, but this structure is intentionally
    compact so it can be reused by the API layer and tests without coupling to
    implementation details.
    """

    target_workers: int
    running_workers: int
    active_workers: int
    idle_workers: int
    queue_size: int
    completed_total: int
    failed_total: int
    retry_total: int
    tasks_per_minute: float


@dataclass(slots=True)
class _QueuedJob:
    priority: int
    sequence: int
    func: JobCallable
    future: asyncio.Future[Any]
    provider_id: str | None = None
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    request_tokens: int = 1


class WorkerPool:
    """Async queue based worker pool for LLM-bound jobs.

    The pool owns a priority queue, a set of background worker tasks, retry
    bookkeeping, and optional integration with the provider rate limiter.

    Jobs are submitted as callables and are executed in priority order. Lower
    priority values are processed first, and same-priority jobs retain FIFO
    order. Retries use exponential backoff with a default sequence of
    1s, 2s, 4s and stop after 3 retries.
    """

    def __init__(
        self,
        *,
        initial_workers: int = 1,
        max_workers: int = 50,
        rate_limit_manager: ProviderRateLimitManager | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleeper = asyncio.sleep,
        retry_delays: tuple[float, ...] = (1.0, 2.0, 4.0),
        max_retries: int = 3,
    ) -> None:
        if initial_workers < 1:
            raise AppError(ErrorCode.VALIDATION_ERROR, "initial_workers must be at least 1")
        if max_workers < 1:
            raise AppError(ErrorCode.VALIDATION_ERROR, "max_workers must be at least 1")
        if initial_workers > max_workers:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "initial_workers cannot exceed max_workers",
                details={"initial_workers": initial_workers, "max_workers": max_workers},
            )
        if max_retries < 0:
            raise AppError(ErrorCode.VALIDATION_ERROR, "max_retries cannot be negative")

        self._initial_workers = int(initial_workers)
        self._max_workers = int(max_workers)
        self._target_workers = int(initial_workers)
        self._rate_limit_manager = rate_limit_manager or ProviderRateLimitManager(clock=clock, sleep=sleep)
        self._clock = clock
        self._sleep = sleep
        self._retry_delays = retry_delays or (1.0, 2.0, 4.0)
        self._max_retries = int(max_retries)
        self._queue: asyncio.PriorityQueue[tuple[int, int, _QueuedJob]] = asyncio.PriorityQueue()
        self._sequence = 0
        self._next_worker_id = 0
        self._workers: dict[int, asyncio.Task[None]] = {}
        self._worker_busy: dict[int, bool] = {}
        self._completed_total = 0
        self._failed_total = 0
        self._retry_total = 0
        self._finished_timestamps: deque[float] = deque()
        self._started = False
        self._closing = False
        self._lock = asyncio.Lock()

    @property
    def max_workers(self) -> int:
        return self._max_workers

    @property
    def target_workers(self) -> int:
        return self._target_workers

    async def start(self) -> None:
        """Start the worker pool if it has not been started yet."""

        async with self._lock:
            if self._started:
                return
            if self._closing:
                raise AppError(ErrorCode.INTERNAL_ERROR, "Worker pool is closed")
            self._closing = False
            self._started = True
            await self._ensure_worker_count_locked(self._target_workers)

    async def close(self) -> None:
        """Shut down the worker pool and wait for background tasks to exit."""

        async with self._lock:
            if not self._started:
                self._closing = True
                return
            self._closing = True
            tasks = list(self._workers.values())
            for worker_id, task in list(self._workers.items()):
                if not self._worker_busy.get(worker_id, False):
                    task.cancel()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        async with self._lock:
            self._workers.clear()
            self._worker_busy.clear()
            self._started = False

    async def set_worker_count(self, count: int) -> WorkerPoolStatus:
        """Resize the pool to the requested worker count."""

        if count < 1 or count > self._max_workers:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "Worker count must be between 1 and max_workers",
                details={"count": count, "max_workers": self._max_workers},
            )

        cancelled_tasks: list[asyncio.Task[None]] = []
        async with self._lock:
            await self._ensure_started_locked()
            self._target_workers = int(count)
            await self._ensure_worker_count_locked(self._target_workers)
            cancelled_tasks = self._retire_idle_excess_workers_locked()

        if cancelled_tasks:
            await asyncio.gather(*cancelled_tasks, return_exceptions=True)

        async with self._lock:
            return self._build_status_locked()

    async def submit(
        self,
        func: JobCallable,
        *,
        priority: int = 100,
        provider_id: str | None = None,
        rpm_limit: int | None = None,
        tpm_limit: int | None = None,
        request_tokens: int = 1,
    ) -> Any:
        """Queue a job and wait for its result.

        If provider metadata is supplied, the job acquires a token from the
        provider rate limiter before each execution attempt.
        """

        if request_tokens < 1:
            raise AppError(ErrorCode.VALIDATION_ERROR, "request_tokens must be at least 1")
        if provider_id is None:
            if rpm_limit is not None or tpm_limit is not None:
                raise AppError(
                    ErrorCode.VALIDATION_ERROR,
                    "provider_id, rpm_limit, and tpm_limit must be supplied together",
                )
        elif rpm_limit is None or tpm_limit is None:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "provider_id, rpm_limit, and tpm_limit are required for rate-limited jobs",
            )

        await self.start()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        async with self._lock:
            if self._closing:
                raise AppError(ErrorCode.INTERNAL_ERROR, "Worker pool is shutting down")
            sequence = self._sequence
            self._sequence += 1
            job = _QueuedJob(
                priority=int(priority),
                sequence=sequence,
                func=func,
                future=future,
                provider_id=provider_id,
                rpm_limit=rpm_limit,
                tpm_limit=tpm_limit,
                request_tokens=request_tokens,
            )
            await self._queue.put((job.priority, job.sequence, job))

        return await future

    def status(self) -> WorkerPoolStatus:
        """Return a point-in-time status snapshot."""

        return self._build_status_locked()

    async def _ensure_started_locked(self) -> None:
        if not self._started:
            if self._closing:
                raise AppError(ErrorCode.INTERNAL_ERROR, "Worker pool is closed")
            self._closing = False
            self._started = True
            await self._ensure_worker_count_locked(self._target_workers)

    async def _ensure_worker_count_locked(self, desired_count: int) -> None:
        current_running = len(self._workers)
        if desired_count > current_running:
            for _ in range(desired_count - current_running):
                self._spawn_worker_locked()

    def _spawn_worker_locked(self) -> None:
        worker_id = self._next_worker_id
        self._next_worker_id += 1
        self._worker_busy[worker_id] = False
        task = asyncio.create_task(self._worker_loop(worker_id))
        self._workers[worker_id] = task
        task.add_done_callback(lambda done, worker_id=worker_id: self._cleanup_worker(worker_id, done))

    def _cleanup_worker(self, worker_id: int, task: asyncio.Task[None]) -> None:
        self._workers.pop(worker_id, None)
        self._worker_busy.pop(worker_id, None)
        try:
            task.exception()
        except (asyncio.CancelledError, asyncio.InvalidStateError):
            return

    def _retire_idle_excess_workers_locked(self) -> list[asyncio.Task[None]]:
        cancelled_tasks: list[asyncio.Task[None]] = []
        excess = [worker_id for worker_id in self._workers if worker_id >= self._target_workers]
        for worker_id in excess:
            if self._worker_busy.get(worker_id, False):
                continue
            task = self._workers.get(worker_id)
            if task is not None:
                task.cancel()
                cancelled_tasks.append(task)
        return cancelled_tasks

    async def _worker_loop(self, worker_id: int) -> None:
        while True:
            if self._closing:
                return
            if worker_id >= self._target_workers:
                return

            try:
                _, _, job = await self._queue.get()
            except asyncio.CancelledError:
                if self._closing or worker_id >= self._target_workers:
                    return
                raise

            self._worker_busy[worker_id] = True
            try:
                result = await self._run_job(job)
            except asyncio.CancelledError:
                self._worker_busy[worker_id] = False
                self._queue.task_done()
                raise
            except Exception as exc:
                self._failed_total += 1
                self._finished_timestamps.append(self._clock())
                if not job.future.done():
                    job.future.set_exception(exc)
            else:
                self._completed_total += 1
                self._finished_timestamps.append(self._clock())
                if not job.future.done():
                    job.future.set_result(result)
            finally:
                self._worker_busy[worker_id] = False
                self._queue.task_done()

            if worker_id >= self._target_workers and not self._closing:
                return

    async def _run_job(self, job: _QueuedJob) -> Any:
        attempt = 0
        while True:
            try:
                return await self._execute_once(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                if attempt >= self._max_retries:
                    raise
                delay = self._retry_delays[min(attempt, len(self._retry_delays) - 1)]
                self._retry_total += 1
                attempt += 1
                await self._sleep(delay)

    async def _execute_once(self, job: _QueuedJob) -> Any:
        if job.provider_id is not None:
            assert job.rpm_limit is not None
            assert job.tpm_limit is not None
            async with await self._rate_limit_manager.acquire(
                job.provider_id,
                job.rpm_limit,
                job.tpm_limit,
                request_tokens=job.request_tokens,
            ):
                return await self._call_job(job.func)
        return await self._call_job(job.func)

    async def _call_job(self, func: JobCallable) -> Any:
        result = func()
        if inspect.isawaitable(result):
            return await result
        return result

    def _build_status_locked(self) -> WorkerPoolStatus:
        now = self._clock()
        self._prune_finished_timestamps(now)
        running_workers = len(self._workers)
        active_workers = sum(1 for busy in self._worker_busy.values() if busy)
        idle_workers = max(0, running_workers - active_workers)
        return WorkerPoolStatus(
            target_workers=self._target_workers,
            running_workers=running_workers,
            active_workers=active_workers,
            idle_workers=idle_workers,
            queue_size=self._queue.qsize(),
            completed_total=self._completed_total,
            failed_total=self._failed_total,
            retry_total=self._retry_total,
            tasks_per_minute=float(len(self._finished_timestamps)),
        )

    def _prune_finished_timestamps(self, now: float) -> None:
        while self._finished_timestamps and now - self._finished_timestamps[0] > 60.0:
            self._finished_timestamps.popleft()
