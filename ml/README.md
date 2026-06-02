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
| Edge | ConvLSTM-tiny (INT8) | **WEDA-FALL** (pre-impact labels re-derived from fall_timestamps.csv) | ≤ 100 KB · <80 ms inference · INT8 quantized |
| Cloud | Transformer encoder (alt CNN-LSTM) | **WEDA-FALL** + **SmartFall** + **Indian-ADL** | FP32 · full FastAPI service |
| Generalization test | both | **UP-Fall** wrist channel | held-out cross-device evaluation |

Both models share the same sliding-window feature extraction. See `features/` for the shared pipeline.

> **Pre-impact label re-derivation** — WEDA-FALL labels fall *windows*, not the impact *instant*. We programmatically detect the impact peak (`argmax |a|` within the labeled window) and define PRE_IMPACT / IMPACT / POST_IMPACT phases around it. See [`DATA.md`](DATA.md) for the algorithm + validation methodology.

## Honest metrics — the validation contract

- **Subject-stratified k-fold CV** — implemented in `training/cv.py`. Never train and test on the same subject.
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
| Cloud FPR on ADL (incl. Indian-ADL) | ≤ 2% |
| Cross-dataset gen. (UP-Fall wrist) | recall drop ≤ 10 pp from primary |
| End-to-end pipeline FP rate | ≤ 0.5/day in continuous-wear simulation |
