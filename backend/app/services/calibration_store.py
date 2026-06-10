"""Per-device calibration — read side (inference) + write side (fit-at-first).

Read: `get()` returns the device's CalibrationProfile for the detector to apply
per-request.  Returns None when uncalibrated so the detector falls back to the
model's global stats (ARCHITECTURE §4.6, §3.2).

Write (Phase 29):
  `accumulate_windows()` stores ADL windows from the 15-min fit-at-first session
  as retraining_samples with label=ADL_CALIBRATION.  `fit()` reads them back,
  computes per-channel and per-feature z-score normalisers, and upserts a
  device_calibration row.  No threshold_override is computed — the global recall-
  calibrated threshold from cloud_detector.meta.json is already the right prior.
  Threshold personalisation from accumulated canceled false-alarms is Phase 30+.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

import numpy as np
from sqlalchemy import select

from app.config import Settings
from app.models import Device, DeviceCalibration, RetrainingSample
from app.schemas import WindowEnvelope
from app.services.detector import CalibrationProfile
from app.services.features import extract_features

if TYPE_CHECKING:
    from app.db import Database

logger = logging.getLogger(__name__)

ADL_CALIBRATION = "ADL_CALIBRATION"
_MIN_WINDOWS = 10  # ~25 s of ADL data — enough for meaningful stats


class CalibrationStore:
    def __init__(self, settings: Settings, db: "Database | None") -> None:
        self.settings = settings
        self._db = db

    @property
    def is_stub(self) -> bool:
        return self._db is None

    # ── read side ────────────────────────────────────────────────────────────

    async def get(self, device_id: str, user_id: UUID) -> CalibrationProfile | None:
        """The device's calibration, or None (DB-less / unpaired / uncalibrated)."""
        if self._db is None:
            return None
        async with self._db.session_for(user_id) as session:
            row = (
                await session.execute(
                    select(DeviceCalibration)
                    .join(Device, DeviceCalibration.device_id == Device.id)
                    .where(Device.device_id == device_id)
                )
            ).scalar_one_or_none()
        if row is None:
            return None
        return CalibrationProfile(
            channel_mean=row.channel_mean,
            channel_std=row.channel_std,
            feature_mean=row.feature_mean,
            feature_std=row.feature_std,
            threshold_override=row.threshold_override,
        )

    # ── write side ───────────────────────────────────────────────────────────

    async def accumulate_windows(
        self,
        windows: list[WindowEnvelope],
        *,
        device_pk: UUID,
        device_id: str,
        user_id: UUID,
    ) -> int:
        """Store ADL windows from the fit-at-first session; returns total count so far."""
        if self._db is None:
            return 0
        async with self._db.session_for(user_id) as session:
            for w in windows:
                session.add(
                    RetrainingSample(
                        device_ref=device_id,
                        device_id=device_pk,
                        user_id=user_id,
                        ts_start_unix_ms=w.ts_start_unix_ms,
                        sample_rate_hz=w.sample_rate_hz,
                        window=[
                            {"ax": s.ax, "ay": s.ay, "az": s.az,
                             "wx": s.wx, "wy": s.wy, "wz": s.wz}
                            for s in w.samples
                        ],
                        label=ADL_CALIBRATION,
                    )
                )
            await session.commit()
            # Count total ADL windows for this device so the caller can report progress.
            total = (
                await session.execute(
                    select(RetrainingSample)
                    .where(
                        RetrainingSample.device_id == device_pk,
                        RetrainingSample.label == ADL_CALIBRATION,
                    )
                )
            ).scalars()
            return len(list(total))

    async def fit(
        self,
        *,
        device_pk: UUID,
        device_id: str,
        user_id: UUID,
        sample_rate: int = 50,
    ) -> tuple[int, datetime]:
        """Fit per-user z-score normalisers from accumulated ADL windows.

        Reads all ADL_CALIBRATION rows for `device_pk`, computes channel-level
        and feature-level statistics, and upserts into device_calibration.
        Returns (n_windows_used, fitted_at).

        Falls back gracefully: if there are < _MIN_WINDOWS rows the fit still
        runs on whatever data is available, because some personalisation is
        always better than none.
        """
        if self._db is None:
            return 0, datetime.now(tz=timezone.utc)

        async with self._db.session_for(user_id) as session:
            rows = (
                await session.execute(
                    select(RetrainingSample).where(
                        RetrainingSample.device_id == device_pk,
                        RetrainingSample.label == ADL_CALIBRATION,
                    )
                )
            ).scalars().all()

            n = len(rows)
            if n == 0:
                logger.warning("calibrate called with no ADL windows for device %s", device_id)
                now = datetime.now(tz=timezone.utc)
                return 0, now

            # Build (N, 125, 6) tensor from stored JSONB windows.
            all_windows = np.array(
                [
                    [[s["ax"], s["ay"], s["az"], s["wx"], s["wy"], s["wz"]] for s in row.window]
                    for row in rows
                ],
                dtype=np.float32,
            )  # (N, 125, 6)

            # Channel stats: mean/std over ALL samples across all windows.
            flat = all_windows.reshape(-1, 6)           # (N*125, 6)
            channel_mean = flat.mean(axis=0).tolist()   # len 6
            channel_std = np.maximum(flat.std(axis=0), 1e-6).tolist()  # len 6, no zero std

            # Feature stats: mean/std over per-window feature vectors.
            feat_vectors = np.array(
                [extract_features(all_windows[i], sample_rate=sample_rate) for i in range(n)],
                dtype=np.float32,
            )  # (N, 43)
            feature_mean = feat_vectors.mean(axis=0).tolist()  # len 43
            feature_std = np.maximum(feat_vectors.std(axis=0), 1e-6).tolist()  # len 43

            now = datetime.now(tz=timezone.utc)

            # Upsert: update if row exists (re-calibration), insert if not.
            existing = await session.get(DeviceCalibration, device_pk)
            if existing is not None:
                existing.channel_mean = channel_mean
                existing.channel_std = channel_std
                existing.feature_mean = feature_mean
                existing.feature_std = feature_std
                existing.n_adl_windows = n
                existing.fitted_at = now
            else:
                session.add(
                    DeviceCalibration(
                        device_id=device_pk,
                        channel_mean=channel_mean,
                        channel_std=channel_std,
                        feature_mean=feature_mean,
                        feature_std=feature_std,
                        n_adl_windows=n,
                        fitted_at=now,
                    )
                )
            await session.commit()

        logger.info("calibration fit: device=%s n_windows=%d", device_id, n)
        return n, now
