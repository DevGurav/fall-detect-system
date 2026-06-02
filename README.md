# Fall Guardian v3

Industry-grade wrist-worn fall prediction & detection system for elderly users (Indian context).

> 🚧 **Status:** Active build — Week C (cloud model + backend). Edge baseline shipped (96.5% recall on held-out subjects). See [Build sequence](#build-sequence) below.

## The system in one diagram

```text
   ESP32-S3 wrist wearable                FastAPI cloud
   (MPU6050 IMU @ 50 Hz)                  (Fly.io)
   ┌──────────────────────┐               ┌──────────────────────┐
   │  Edge model          │               │  Cloud model         │
   │  ConvLSTM-tiny INT8  │ ─── alerts ─► │  Transformer encoder │
   │  ~80 KB · <80 ms     │               │  full sliding window │
   │  predicts PRE-impact │               │  confirms / cancels  │
   │  (~300 ms lead time) │               │  + assigns severity  │
   └──────────────────────┘               └──────────┬───────────┘
                                                     │
                                  ┌──────────────────┴──────────────────┐
                                  │                                     │
                          Flutter wrist app                   Next.js caregiver
                          (offline, bilingual,                 web dashboard
                           emergency button)
```

**The headline:** the wearable can alert in the **~300 ms window between fall initiation and ground impact** — not just after the person hits the floor.

## Personalization — the local grace period

A core product feature, not an afterthought: the system **learns each user's false alarms**. The edge model is recall-first and fires often by design, so when it triggers the watch first buzzes locally for a **~10 s grace period**. If the user presses **Cancel** (it wasn't a fall), no caregiver is alerted — instead the watch silently uploads that exact 2.5 s window to the cloud, where it is stored as labeled training data (`CANCELED_FALSE_ALARM`) for **per-user fine-tuning and threshold tuning**. The user is the ground truth for their own non-falls.

This splits ingestion into two paths that never cross: emergencies (`POST /v1/inference` → cloud detector → caregiver) and canceled false alarms (`POST /v1/retraining` → stored for MLOps, detector skipped). See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §3.2/§8 and [`docs/DECISIONS.md`](docs/DECISIONS.md) ADR-011.

## Why a rebuild

This project replaces two earlier prototypes (`fall-detect-system`, `fall-simulated`) — both functional proofs of concept, neither production-grade. A deep audit surfaced 20+ specific issues across ML (single-sample inference on synthetic data), security (no auth, world-writable Firestore rules), and UX (an "emergency button" claimed in README but missing from code). The full audit lives in [`docs/AUDIT_v1_v2.md`](docs/AUDIT_v1_v2.md). v3 is the ground-up rebuild that fixes them.

## Monorepo layout

| Folder | What it is |
|---|---|
| [`ml/`](ml/) | PyTorch training, MLflow experiment tracking, datasets, feature engineering, model export |
| [`backend/`](backend/) | FastAPI inference service + Postgres + JWT auth + rate limiting |
| [`mobile/`](mobile/) | Flutter wrist companion app — Riverpod 2 + GoRouter + Drift offline + emergency button |
| [`dashboard/`](dashboard/) | Next.js caregiver web dashboard — Tailwind v4 + SSE real-time |
| [`edge/`](edge/) | ESP32-S3 firmware — ESP-IDF + TFLite Micro |
| [`virtual_device/`](virtual_device/) | Python IMU simulator (lets us develop + demo without real hardware) |
| [`docs/`](docs/) | Architecture, audit of v1/v2, validation methodology, model cards |

## Datasets — wrist-worn only

We deliberately use only **wrist-worn** training data — domain adaptation from waist or chest data is not a credible production approach.

- **[WEDA-FALL](https://github.com/joaojtmarques/WEDA-FALL)** — primary training set. Wrist-worn Fitbit Sense; 25 subjects (14 young + 11 elderly aged 77–95); 11 ADL × 8 fall types; 50 Hz; accel + gyro + orientation; manually-labeled fall windows. We **re-derive pre-impact labels** programmatically from the fall windows and validate against the dataset's ground-truth labels — methodology documented in [`ml/DATA.md`](ml/DATA.md).
- **[SmartFall](https://www.mdpi.com/1424-8220/18/10/3363)** (Texas State) — secondary. 9 elderly subjects wearing a smartwatch 3 hrs/day × 7 days each. Real-world wear pattern; great ADL diversity. Accel-only (limitation).
- **[UP-Fall](https://pmc.ncbi.nlm.nih.gov/articles/PMC6539235/)** wrist channel — cross-dataset generalization testing only (different device, 18 Hz — proves the model isn't overfit to Fitbit-specific signal).
- **Indian-ADL supplement** — our own collection (Week E): sukhasana (cross-legged sit), namaste, getting up from floor, squat toilet, intentional wrist motions (eating, waving, brushing, doors). Public datasets miss these.

## Honest validation methodology

The defining feature of v3 vs v1/v2 is that the metrics are *trustworthy*:

- **Subject-stratified k-fold cross-validation** — never train and test on the same subject
- **Held-out test subjects** — 20% of subjects entirely out of training/validation
- Real-world ADL augmentation from the Indian-ADL set
- Honest metrics: precision, recall, F1, **FPR on ADL** (the metric that matters), **lead-time histogram** for the prediction model, ROC + AUC
- Platt-scaling / isotonic calibration so probability outputs are trustworthy
- Every experiment MLflow-tracked + reproducible

## Targets

| Component | Target |
|---|---|
| Edge model (prediction) | recall ≥ 95% on WEDA-FALL held-out subjects, FPR ≤ 5% on ADL, mean lead time ≥ 300 ms |
| Cloud model (detection) | recall ≥ 97% on WEDA-FALL held-out subjects, FPR ≤ 2% on ADL |
| End-to-end pipeline | false-positive rate ≤ 0.5 per day in continuous-wear simulation |
| Edge model size | ≤ 100 KB INT8 |
| Edge inference latency | < 80 ms on ESP32-S3 |
| Wearable battery | ≥ 24 h continuous wear |

## Build sequence

| Week | Focus | Deliverables |
|---|---|---|
| **A** | Data foundation | WEDA-FALL download · loaders · sliding-window + feature extraction · EDA · MLflow setup |
| **B** | Edge model | Train ConvLSTM-tiny on WEDA-FALL · INT8 quantize · size + simulated latency report |
| **C** | Cloud model + backend skeleton | Transformer detector · FastAPI + Postgres + JWT · `/v1/inference` (emergency) **+ `/v1/retraining` (canceled-false-alarm capture)** · deploy to Fly.io |
| **D** | Mobile rebuild | Flutter — Riverpod + GoRouter + design system + auth + pairing + live status + **emergency button** + **local grace period (10 s buzz + Cancel)** + offline + bilingual |
| **E** | Indian-ADL collection + retraining | Collect 60–100 min of supplemental ADL data · retrain both models · **fine-tune on collected `CANCELED_FALSE_ALARM` windows / per-user thresholds** · re-measure |
| **F** | Edge deploy + dashboard + polish | TFLite Micro on ESP32-S3 · Next.js dashboard · observability stack · CI/CD · demo video |

## License

MIT — see [LICENSE](LICENSE).
