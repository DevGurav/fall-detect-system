# Fall Guardian v3

Industry-grade wrist-worn fall prediction & detection system for elderly users (Indian context).

> 🚧 **Status:** Active build — Week A (data foundation). See [Build sequence](#build-sequence) below.

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
| Edge model (prediction) | recall ≥ 95% on KFall held-out subjects, FPR ≤ 5% on ADL, mean lead time ≥ 300 ms |
| Cloud model (detection) | recall ≥ 97% on SisFall held-out subjects, FPR ≤ 2% on ADL |
| End-to-end pipeline | false-positive rate ≤ 0.5 per day in continuous-wear simulation |
| Edge model size | ≤ 100 KB INT8 |
| Edge inference latency | < 80 ms on ESP32-S3 |
| Wearable battery | ≥ 24 h continuous wear |

## Build sequence

| Week | Focus | Deliverables |
|---|---|---|
| **A** | Data foundation | KFall + SisFall download · loaders · sliding-window + feature extraction · EDA · MLflow setup |
| **B** | Edge model | Train ConvLSTM-tiny on KFall · INT8 quantize · size + simulated latency report |
| **C** | Cloud model + backend skeleton | Transformer on SisFall · FastAPI + Postgres + JWT · `/v1/inference` returning both models · deploy to Fly.io |
| **D** | Mobile rebuild | Flutter — Riverpod + GoRouter + design system + auth + pairing + live status + **emergency button** + offline + bilingual |
| **E** | Indian-ADL collection + retraining | Collect 60–100 min of supplemental ADL data · retrain both models · re-measure |
| **F** | Edge deploy + dashboard + polish | TFLite Micro on ESP32-S3 · Next.js dashboard · observability stack · CI/CD · demo video |

## License

MIT — see [LICENSE](LICENSE).
