# ml/ — Fall Guardian ML pipeline

PyTorch training + MLflow tracking for the **edge** (ConvLSTM-tiny, pre-impact prediction) and **cloud** (Transformer encoder, post-impact detection) models.

## Quick start

```bash
# Install (uv is the recommended package manager — pip works too)
cd ml
uv sync                          # or: pip install -e .[dev]

# 1. Download datasets — see DATA.md for WEDA-FALL instructions
# 2. Verify everything is in place
uv run fg-data verify

# 3. Sanity-check loaders + windowing on a small sample
uv run python -m fall_guardian_ml.datasets.smoke_test

# 4. Open the EDA notebook
uv run jupyter lab notebooks/01_eda.ipynb
```

## Package layout

```text
ml/
├── src/fall_guardian_ml/
│   ├── datasets/        ← WEDA-FALL loader + edge/cloud dataset builders
│   ├── features/        ← sliding window, magnitude, jerk, FFT, normalization
│   ├── models/          ← ConvLSTM-tiny (edge) + Transformer encoder (cloud)
│   ├── training/        ← training loops, subject-stratified CV, calibration
│   └── eval/            ← metrics (precision/recall/F1/FPR/lead-time), plots
├── notebooks/           ← EDA + experiments
├── scripts/             ← one-off utilities
├── tests/               ← pytest
├── data/                ← gitignored; see DATA.md
├── DATA.md              ← dataset download instructions
└── pyproject.toml
```

## Two models, one pipeline (wrist-only datasets)

| Stage | Model | Datasets | Constraints |
|---|---|---|---|
| Edge | ConvLSTM-tiny (INT8) | **WEDA-FALL** (pre-impact labels re-derived from fall_timestamps.csv) | ≤ 100 KB · <80 ms inference · INT8 quantized → TFLite Micro |
| Cloud | Transformer encoder | **WEDA-FALL** + **SmartFall** (hard negatives) | FP32 → exported to **ONNX**, served in-process in the gateway (ADR-015) |
| Generalization test | both | **UP-Fall** wrist channel | held-out cross-device evaluation |

> **Indian-ADL was dropped** (ADR-013) in favour of per-user fit-at-first
> calibration — the model personalises to each user's own ADL distribution at
> onboarding rather than training on one collected corpus.

Both models share the same sliding-window feature extraction. See `features/` for the shared pipeline.

## Export + hardening (Phase 30/31)

```bash
# Cloud: 5-fold subject-stratified CV → retrain → export ONNX for the gateway
uv run python -m fall_guardian_ml.training.cross_validate     # fold-averaged PR threshold
uv run python scripts/export_cloud_onnx.py                    # → backend/app/model/cloud_detector.onnx (+ .meta.json)
uv run python scripts/cascade_eval.py                         # edge→cloud joint FPR on held-out
uv run python scripts/continuous_wear_sim.py                  # alarms-per-day simulation

# Edge: INT8 TFLite + C header for the firmware (needs a Linux toolchain — see edge/README.md)
python scripts/export_tflite.py                               # → convlstm_tiny_int8.tflite
python scripts/tflite_to_header.py                            # → edge/include/model.h
python scripts/validate_tflite.py                             # round-trip vs the FP32 checkpoint
```

> The active cloud model is the **5-fold CV** export; the prior Phase-20 baseline is
> preserved at `backend/app/model_old/` for diff / rollback (ADR-018).

> **Pre-impact label re-derivation** — WEDA-FALL labels fall *windows*, not the impact *instant*. We programmatically detect the impact peak (`argmax |a|` within the labeled window) and define PRE_IMPACT / IMPACT / POST_IMPACT phases around it. See [`DATA.md`](DATA.md) for the algorithm + validation methodology.

## Honest metrics — the validation contract

- **Subject-stratified k-fold CV** — implemented in `training/cross_validate.py` (5-fold, fold-averaged PR threshold). Never train and test on the same subject.
- **Held-out test subjects** — 20% of subject IDs reserved across the entire run.
- **Metrics published**: precision, recall, F1, **FPR on ADL**, confusion matrix, **lead-time histogram** (prediction model), ROC + AUC.
- **Calibration**: Platt-scaling / isotonic in `eval/calibration.py`.
- All experiments tracked in MLflow (`mlruns/`).

## Targets

| Metric | Target |
|---|---|
| Edge recall on WEDA-FALL held-out subjects | ≥ 95% |
| Edge FPR on ADL | ≤ 5% |
| Edge mean lead time | ≥ 300 ms |
| Edge model size (INT8) | ≤ 100 KB |
| Cloud recall on WEDA-FALL + SmartFall held-out subjects | ≥ 97% |
| Cloud FPR on ADL | ≤ 2% |
| Cross-dataset gen. (UP-Fall wrist) | recall drop ≤ 10 pp from primary |
| End-to-end pipeline FP rate | ≤ 0.5/day in continuous-wear simulation |
