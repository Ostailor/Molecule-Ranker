from __future__ import annotations

from collections.abc import Callable

from molecule_ranker.integrations.connectors.base import ExternalConnector
from molecule_ranker.integrations.schemas import ConnectorConfig
from molecule_ranker.integrations.worker import IntegrationWorker
from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.platform.schemas import PlatformJob
from molecule_ranker.workers.base import BaseWorker


class IntegrationQueueWorker(BaseWorker):
    """Queue adapter for external research-system integration jobs."""

    def __init__(
        self,
        *,
        database: PlatformDatabase,
        connector_factory: Callable[[ConnectorConfig], ExternalConnector] | None = None,
    ) -> None:
        self.integration_worker = IntegrationWorker(
            database=database,
            connector_factory=connector_factory,
        )
        super().__init__(database=database, handlers={}, job_types=set())

    def run_once(self) -> PlatformJob | None:
        return self.integration_worker.run_next()

    def run_job(self, job: PlatformJob) -> PlatformJob:
        return self.integration_worker.run_job(job)

