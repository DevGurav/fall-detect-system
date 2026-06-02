"""Retraining-sample storage — the seam the MLOps datastore drops into.

Architecture role (docs/ARCHITECTURE.md §3, §8): during the local grace period the
watch buzzes for ~10 s on an edge trigger; if the user presses Cancel, that 2.5 s
window was a FALSE ALARM. The watch silently uploads it here — NOT for detection,
but as labeled data for future fine-tuning and per-user threshold tuning. So this
path deliberately bypasses the `CloudDetector`.

Right now there's no database, so `RetrainingStore` runs in **stub mode**: it logs
the sample and returns an ack with a generated `sample_id`, so the ingestion path is
end-to-end testable today. When persistence lands, `_connect` opens the pool from
`settings.retraining_db_dsn` and `store` writes a row to `retraining_samples` — no
API or schema change. Mirrors the `CloudDetector` stub philosophy.
"""
from __future__ import annotations

import logging
from uuid import uuid4

from app.config import Settings
from app.schemas import RetrainingAck, RetrainingRequest

logger = logging.getLogger(__name__)

# The only retraining label we collect today: a window the edge flagged but the
# user canceled during the grace period. Kept as a constant so the storage layer
# and any future MLOps query agree on the exact string.
CANCELED_FALSE_ALARM = "CANCELED_FALSE_ALARM"


class RetrainingStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db = self._connect()

    def _connect(self):
        """Open the datastore if configured; otherwise stay in stub mode."""
        if self.settings.retraining_db_dsn:
            # TODO(MLOps): open the Postgres pool and ensure the `retraining_samples`
            # table exists (window blob + label + device_id + edge_prediction + ts).
            raise NotImplementedError("real retraining persistence is future MLOps work")
        return None  # stub mode

    @property
    def is_stub(self) -> bool:
        return self.db is None

    def store(self, req: RetrainingRequest) -> RetrainingAck:
        if self.db is None:
            return self._stub_store(req)
        raise NotImplementedError  # real INSERT — future MLOps

    def _stub_store(self, req: RetrainingRequest) -> RetrainingAck:
        """Pretend to persist the window. Placeholder for the MLOps datastore."""
        sample_id = uuid4().hex
        logger.info(
            "retraining sample %s queued (stub): device=%s ts_start=%s samples=%d label=%s",
            sample_id,
            req.device_id,
            req.ts_start_unix_ms,
            len(req.samples),
            CANCELED_FALSE_ALARM,
        )
        return RetrainingAck(
            stored=True,
            label=CANCELED_FALSE_ALARM,
            sample_id=sample_id,
            message="stored for retraining (stub)",
        )
