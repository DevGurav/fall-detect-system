"""Retraining-sample storage — the MLOps datastore seam (ADR-011).

During the local grace period the watch buzzes ~10 s on an edge trigger; if the
user presses Cancel, that 2.5 s window was a FALSE ALARM. The watch uploads it
here — NOT for detection, but as labeled data for future fine-tuning and per-user
threshold tuning. So this path deliberately bypasses the `CloudDetector`.

When a database is configured the window is written to the `retraining_samples`
table (scoped to the owning device + user when the device is paired). With no DB
the store runs in **stub mode** — it logs and acks with a generated id, so the
ingestion path stays end-to-end testable without Postgres. Mirrors the detector.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import uuid4

from app.config import Settings
from app.models import CANCELED_FALSE_ALARM, RetrainingSample
from app.schemas import RetrainingAck, RetrainingRequest
from app.security import get_device

if TYPE_CHECKING:
    from app.db import Database

logger = logging.getLogger(__name__)


class RetrainingStore:
    def __init__(self, settings: Settings, db: Database | None) -> None:
        self.settings = settings
        self._db = db

    @property
    def is_stub(self) -> bool:
        return self._db is None

    async def store(self, req: RetrainingRequest) -> RetrainingAck:
        if self._db is None:
            return self._stub_store(req)
        return await self._persist(req)

    async def _persist(self, req: RetrainingRequest) -> RetrainingAck:
        """Write the canceled window to `retraining_samples`, scoped to its owner."""
        sample_id = uuid4()
        edge = req.edge_prediction
        async with self._db.sessionmaker() as session:
            device = await get_device(session, req.device_id)
            session.add(
                RetrainingSample(
                    id=sample_id,
                    device_ref=req.device_id,
                    device_id=device.id if device else None,
                    user_id=device.user_id if device else None,
                    ts_start_unix_ms=req.ts_start_unix_ms,
                    sample_rate_hz=req.sample_rate_hz,
                    window=[s.model_dump() for s in req.samples],
                    label=CANCELED_FALSE_ALARM,
                    edge_p_pre_impact=edge.p_pre_impact if edge else None,
                    edge_model_version=edge.model_version if edge else None,
                )
            )
            await session.commit()
        logger.info(
            "retraining sample %s persisted: device=%s paired=%s",
            sample_id.hex,
            req.device_id,
            device is not None,
        )
        return RetrainingAck(
            stored=True,
            label=CANCELED_FALSE_ALARM,
            sample_id=sample_id.hex,
            message="stored for retraining",
        )

    def _stub_store(self, req: RetrainingRequest) -> RetrainingAck:
        """No DB configured: log + ack so the ingestion path stays testable."""
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
