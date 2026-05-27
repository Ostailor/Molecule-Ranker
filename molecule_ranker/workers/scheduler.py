from __future__ import annotations

import time
from collections.abc import Iterable

from molecule_ranker.platform.schemas import PlatformJob
from molecule_ranker.workers.base import BaseWorker


class WorkerScheduler:
    def __init__(
        self,
        workers: Iterable[BaseWorker],
        *,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self.workers = list(workers)
        self.poll_interval_seconds = poll_interval_seconds

    def run_once(self) -> PlatformJob | None:
        for worker in self.workers:
            job = worker.run_once()
            if job is not None:
                return job
        return None

    def run_forever(self) -> None:
        while True:
            job = self.run_once()
            if job is None:
                time.sleep(self.poll_interval_seconds)
