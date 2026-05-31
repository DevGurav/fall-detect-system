# Build Log — Fall Guardian v3

A chronological record of every meaningful decision, design change, research finding, and code-writing session on the way to building Fall Guardian v3. The intent is to make the engineering reasoning visible to anyone reading the repo (recruiter, collaborator, future-me) and to give myself a learning record I can come back to.

> **Convention.** New work goes at the **bottom** of this file in a dated section. Sections never get rewritten — if a later decision overturns an earlier one, that's a new section that references the earlier one. This file is append-only history.

---

## Phase 0 — Origins (2nd year engineering project)

The Fall Guardian story starts as my 2nd-year engineering project at Vidyavardhini College of Engineering & Technology (Mumbai University, B.E. Artificial Intelligence & Data Science).

**The original v1 build (`fall-detect-system`)** — Flask backend + ESP32 + MPU6050 wristband + Firebase + Flutter mobile app. Deployed to Render at `fall-detect-system.onrender.com`. ML model was a sklearn `RandomForestClassifier` trained on 1000 synthetic samples (800 normal + 200 fall) of 6 raw IMU features. The hardware threshold (`totalAccel > 2.5g` OR `gyroRotation > 5.0 rad/s`) on the ESP32 gated when to call the cloud. Firebase Firestore stored events; FCM delivered notifications.

**The sibling v2 (`fall-simulated`)** — same problem, software-only path. Added a Python virtual device that simulates IMU patterns, a web dashboard with Socket.IO real-time updates, email/password auth, 6-digit device pairing, and battery monitoring. ML inference moved into a separate Flask service at `fall-simulated.onrender.com` so it could be scaled independently.

Both projects shipped, but neither was production-grade. The ML was on synthetic data with single-sample inference; security was effectively nonexistent (Firestore rules `allow read, write: if true;`); the "emergency button" the README boasted was never actually implemented in the Flutter code; tests were almost absent; the mobile UI was unstyled Material. They worked as proofs-of-concept but didn't deserve to be the headline of a placement portfolio.

That set up the rebuild.

---

## Phase 1 — Audit of v1 / v2 (May 2026)

Before drawing a new system I did a careful audit of both existing repos to make sure the rebuild fixed real problems and didn't just chase shiny tech.

Methodology: two parallel passes through `Repos/fall-detect-system/` and `Repos/fall-simulated/` reading every Python file in `backend/` + `ml_api/`, every Dart file in `mobile_app/lib/`, the ESP32 integration Markdown, the deployment status doc, and the README claims. For each component I asked: does the code do what the README says? Where are the security holes? Where is the ML cutting corners? Where do the metrics come from and are they honest?

The audit surfaced **20+ specific defects** spanning every layer. Full detail is in [`AUDIT_v1_v2.md`](AUDIT_v1_v2.md) with file:line citations; the executive summary:

- **ML:** single-sample inference (no temporal window); 1000 hardcoded synthetic samples; "100% accuracy" claim is a self-referential test split; README documents jerk + magnitude features that the code doesn't actually compute; no model versioning, no retraining pipeline.
- **Backend:** zero auth on any endpoint; CORS wide open; threshold inconsistency between code (70%) and config (85%); inputs not validated; only `print()` for logging.
- **Firestore:** `allow read, write: if true;` — world-writable.
- **Pairing:** 6-digit numeric code = 1M combinations, brute-forceable, no rate limit.
- **Mobile:** "Emergency button" claimed in README — **not in code anywhere**. Hardcoded `localhost:5000`. WebSocket imported but unused. No tests.
- **DevOps:** Render free tier (cold starts trigger a crude `magnitude > 20` rule-based fallback). No CI/CD, no monitoring, no staging.

That ended any temptation to "just patch" v1/v2. The right thing to do was rebuild.

---

## Phase 2 — Locking the v3 design (May 28, 2026)

This is where v3 stopped being a vague idea and became a concrete spec. The major decisions were made deliberately, one at a time, with the trade-offs surfaced. Each is captured as an ADR in [`DECISIONS.md`](DECISIONS.md); the short version of each:

| # | Decision | Why |
|---|---|---|
| ADR-001 | Rebuild from scratch (not patch v1/v2) | 20+ defects across every layer; patching means perpetuating a wrong baseline. |
| ADR-002 | Sequential pipeline: edge **predicts** pre-impact + cloud **confirms** post-impact | Best of both worlds. Edge predicts before the user hits the ground (300–500 ms lead); cloud verifies to suppress false positives. |
| ADR-003 | **Wrist-worn** form factor (not waist, not chest, not multi-position) | Smartwatch form is what real users actually wear. Forces us to pick wrist-specific datasets, but that's the right constraint. |
| ADR-004 | **Edge-first hybrid** — TFLite Micro INT8 on ESP32-S3 + cloud confirmation | Demonstrates TinyML (hot in 2026); reduces bandwidth + cloud cold-start dependency; keeps privacy-sensitive raw data on-device unless something interesting happens. |
| ADR-005 | Target user = **elderly living alone, Indian context** | Original product fit + a hard-to-replicate originality angle. Indian ADLs (sukhasana, namaste, floor-sit-and-rise, squat toilet) are absent from public datasets. |
| ADR-008 | Hardware-agnostic ingestion (virtual device + real ESP32, same API contract) | I only have ESP32 access at a friend's place. Need to develop against a virtual device locally and swap in the real hardware later with zero backend changes. |

The plan at the end of Phase 2 had **KFall** picked for the edge model (pre-impact prediction, elderly subjects) and **SisFall** for the cloud model (post-impact detection, real elderly trials). Both are excellent datasets. I locked that. Architecture, model shapes, security baseline, mobile + dashboard stack were also locked here. See the project's main plan file at `~/.claude/plans/i-am-3rd-year-dynamic-hamming.md` for the full content.

---

## Phase 3 — Wrist-data course correction (May 31, 2026)

> _This is where Phase 2 got partly overturned._

Reviewing the locked design before writing code, I realised both KFall and SisFall record at the **waist or thigh**. That's a sensor-position mismatch with the chosen wrist-worn form factor. Hoping that domain adaptation across body positions saves it is not a credible production approach — the wrist fall signature (rotation around the joint, arm-bracing reflex, much higher motion noise during ADLs) is qualitatively different from the waist signature, and the literature on cross-position transfer for IMU fall detection is unconvincing.

So I went looking for wrist-specific datasets. Five candidates surveyed:

| Dataset | Wrist? | Subjects | Elderly? | Sample rate | Sensors | Pick? |
|---|---|---|---|---|---|---|
| **WEDA-FALL** | Yes (Fitbit Sense) | 25 (14 young + **11 elder, ages 77–95**) | Yes | 50 Hz | Accel + gyro + orientation | **Primary** |
| **SmartFall** (Texas State) | Yes (Android smartwatch) | 9 elder | Yes | ~31 Hz | Accel only | **Secondary** (ADL diversity) |
| **UP-Fall** wrist channel | Yes (one of 5 positions) | 17 young | No | 18 Hz | Accel + gyro | **Cross-dataset eval** only |
| **UMA-Fall** wrist channel | Yes | Few young | No | ~20 Hz | Accel | Discarded |
| **Geriatric Wrist IMU (PMC10709028)** | Yes | 41 | Yes | ? | IMU + HR | Future supplement |

**Decision (ADR-006)**: WEDA-FALL is the primary training set for both edge and cloud models. SmartFall augments the ADL distribution with realistic continuous-wear elderly data. UP-Fall wrist channel becomes the held-out cross-dataset generalization test. KFall and SisFall are explicitly **dropped** as training data (kept as comparison references only).

**Pre-impact prediction handling**: KFall is purpose-built for pre-impact labelling; no wrist dataset matches that. Two options were considered:

- Option A — **re-derive pre-impact labels** programmatically from WEDA-FALL's manually-labelled fall windows by finding the impact peak inside each window and labelling the 300–500 ms preceding it.
- Option B — demote prediction to a v3.1 stretch goal; train the edge model purely as a fast wrist detector for now.

Chose **Option A** (ADR-007): the methodology is documented, reproducible, and becomes a strong portfolio talking point ("I validated derived labels against the dataset's ground-truth start/end timestamps and report the lag distribution").

Doc updates that followed: `ml/DATA.md`, top-level `README.md`, `ml/README.md`, the main plan file, the memory file for the project — all updated in a single batch so the spec is internally consistent everywhere.

---

## Phase 4 — Monorepo scaffolding (May 30–31, 2026)

Created the `fall-guardian/` monorepo skeleton:

```text
fall-guardian/
├── README.md, LICENSE (MIT), .gitignore
├── ml/                       PyTorch training, MLflow, datasets, features
│   ├── pyproject.toml        uv-managed, Python 3.11+
│   ├── DATA.md               dataset download + pre-impact methodology
│   ├── README.md
│   ├── src/fall_guardian_ml/
│   │   ├── datasets/   features/   models/   training/   eval/
│   ├── notebooks/  tests/  scripts/
│   └── data/{raw,processed,interim,external}/  (gitignored)
├── backend/                  FastAPI inference service (later)
├── mobile/                   Flutter wrist app (later)
├── dashboard/                Next.js caregiver web (later)
├── edge/                     ESP32-S3 firmware + TFLite Micro (later)
├── virtual_device/           Python IMU simulator (later)
├── docs/                     this folder
└── .github/workflows/        CI/CD (later)
```

Key choices:

- **uv** as the Python package manager (faster than pip/poetry; clean lockfile workflow; modern 2026 default).
- **Python 3.11+** for the ML sub-project (matches PyTorch 2.4's recommended runtime).
- **MIT licence** (standard for student portfolio projects, recruiter-friendly, doesn't tie hands).
- **Monorepo** rather than polyrepo — keeps the ML/backend/mobile/edge code in one place so the architecture story is visible at one URL.
- **`ml/data/raw/...gitignored`** — datasets live on disk, not in git. `DATA.md` documents how to download them.

---

## Phase 5 — Data foundation (Week A): inspection of WEDA-FALL

After downloading WEDA-FALL into `ml/data/raw/`, a careful directory inspection surfaced two non-obvious facts that shaped the loader design:

**1. The "50Hz" folder name is misleading about sample timing.** The Fitbit Sense API delivers IMU samples in **Bluetooth-batched bursts**, so the raw timestamps cluster (e.g. `0.000, 0.001, 0.003, 0.005` then nothing until `0.121`). The "50Hz" label describes the effective *average* rate, not actual uniform spacing. Any sliding-window feature extraction has to **resample to uniform 50 Hz first** — otherwise the window math (125 samples = 2.5 s) is wrong by an arbitrary amount per window.

**2. `fall_timestamps.csv` ships manually-labelled fall *windows* but not impact *instants*.** 350 fall recordings, each with a `(start_time, end_time)` that brackets the full 4-phase fall sequence (pre-fall → impact → body-adjustment → post-fall). The dataset author explicitly notes these timestamps "can have mistakes". So we have a ground-truth window we can use to constrain the search for the impact peak, AND we get a validation signal: our derived `t_impact` should fall a predictable distance after the labelled `start_time`. The lag distribution becomes a QA tool.

**File-format details captured**:

- Each recording = 4 sensor CSVs: `accel`, `gyro`, `orientation` (quaternion s+i+j+k), `vertical_accel` (gravity-projected; only at 50 Hz).
- Column naming: `<sensor>_time_list, <sensor>_x_list, ...`.
- `fall_timestamps.csv` header has a UTF-8 BOM (`﻿`) at the start of the first column name — the loader strips it.
- Folder structure: `dataset/50Hz/{D01..D11, F01..F08}/U<id>_R<trial>_<sensor>.csv`.
- Subject IDs: U01–U14 are young (ages 20–46), U21–U31 are elder (ages 77–95). **Elders performed ADL only, no falls** (researcher safety).

---

## Phase 6 — ML code (Week A): loader, labels, windowing, features

Wrote five Python modules + two test files. All under `ml/src/fall_guardian_ml/`. Locked design parameters: 2.5-second windows at 50 Hz = 125 samples per window, 50% overlap = stride 62 samples, 43-dimensional engineered feature vector per window.

### `datasets/weda_fall.py` — loader + resampling

Defines `RecordingId` (movement + user + trial), `Recording` (aligned 50 Hz accel + gyro + orientation arrays + fall window), `discover_recordings()` (walks the dataset folder), `load_recording()` (one recording, resampled), and `load_fall_timestamps()` (reads the BOM-prefixed CSV).

The non-uniform → uniform resampling is the load-bearing piece:

```python
t_raw = df["time"].to_numpy()
t_start, t_end = float(t_raw[0]), float(t_raw[-1])
dt = 1.0 / hz                                              # 0.02 s at 50 Hz
n_samples = int(np.floor((t_end - t_start) / dt)) + 1
t_uniform = t_start + np.arange(n_samples) * dt
out[col] = np.interp(t_uniform, t_raw, df[col].to_numpy())
```

Linear interpolation per channel onto a regularly-spaced time grid. After this, every downstream module can assume 50 Hz uniform sampling.

Accel and gyro streams are aligned on a common time window (Fitbit Sense doesn't emit them in lockstep — start/end can differ by a few ms), then truncated to a common length. Orientation is optional (some files may be missing).

### `datasets/pre_impact_labels.py` — peak-magnitude impact detection + phase labels

Two public functions:

- `find_impact(time_s, accel_xyz, label_window)` — computes `|a|(t) = sqrt(ax² + ay² + az²)`, masks samples outside `label_window` with `-inf` so they can't win the argmax, returns `t_impact = time_s[argmax(|a|_masked)]` + diagnostics (peak magnitude, lag from labelled `start_time`, validity flag, threshold check at 20 m/s² ≈ 2g).
- `assign_phase_labels(time_s, t_impact_s, fall_window)` — returns a per-sample `int8` array of `Phase` values. With defaults (lead=500 ms, guard=50 ms, tail=500 ms) and `t_impact=3.0 s` inside `fall_window=(2.5, 4.0)`:
  - `PRE_IMPACT` = [2.5, 2.95) — the edge model's prediction target
  - `IMPACT` = [2.95, 3.5)
  - `POST_IMPACT` = [3.5, 4.0]
  - `BACKGROUND` everywhere else

The `pre_start = max(t_impact_s - lead_s, fall_start)` clamp is critical: if a labelled fall_window is shorter than the 500 ms lead, we must not leak the PRE_IMPACT label into BACKGROUND time. Tested explicitly.

### `features/windowing.py` — sliding window + pre-impact-aligned variant

`slide(data, time_s, phase_labels, window_samples, stride_samples)` is the standard overlapping-window slicer. Each window's label = `np.bincount(window_label_arr).argmax()` (mode of the per-sample phase labels). Ties go to the smallest index = `BACKGROUND` (deliberate: conservative default for ambiguous windows).

`slide_for_prediction(...)` ALSO emits an extra window per fall whose end is aligned to `t_impact - guard_s`. Why: the PRE_IMPACT phase is only ~450 ms long, but the window is 2500 ms. A normal sliding window centred on the impact has its mode = IMPACT or POST_IMPACT, NOT PRE_IMPACT — so the prediction model would see ~zero positive PRE_IMPACT examples. The aligned window positions PRE_IMPACT at the tail of the window, which is exactly what the edge model sees at inference (it fires as the pre-impact phase ends).

### `features/extraction.py` — 43-dim feature vector

Per window, computes:

- **6 stats × 6 raw channels = 36**: mean, std, min, max, peak-to-peak, RMS for each of (ax, ay, az, wx, wy, wz).
- **`sma_accel`** (Signal Magnitude Area on accel) — `mean(|ax|+|ay|+|az|)`. Total movement intensity.
- **`mag_accel_peak/mean/std`** — statistics of `|a|_t = sqrt(ax²+ay²+az²)` across the window.
- **`jerk_accel_max_abs`** — peak `|d|a|/dt|` via `np.gradient` (central differences). Falls produce a brief huge jerk at impact; walking jerk is near-zero.
- **`freq_dominant_accel`** and **`spectral_entropy_accel`** — FFT on the demeaned magnitude signal. Walking has a clean dominant frequency (~1.5–2 Hz, low entropy); falls are broadband transients (no clear dominant freq, high entropy).

The FFT indexing skips the DC bin and maps back to the original spectrum index:

```python
spec = np.abs(np.fft.rfft(signal - signal.mean()))
freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)
dom_idx = int(np.argmax(spec[1:])) + 1     # slice off DC, then add 1 to remap
dominant = float(freqs[dom_idx])
```

`feature_names()` returns the names in the exact order the vector is built, for MLflow logging + downstream feature-importance reports.

This feature vector is the input to the *cloud* model. The *edge* model uses the raw 6-channel window directly (no engineered features) — it has to fit in ≤80 KB INT8.

### `features/normalization.py` — per-user z-score

`ZScoreParams(mean, std).transform(features)` applies `(x - mean) / std` with a `safe_std = where(std > 0, std, 1.0)` guard for constant features. `fit_zscore(features)` computes the per-feature mean + std from a `(N, F)` matrix.

The contract: fit ONLY on the user's ADL (BACKGROUND-phase) windows, not on fall windows. The product workflow: at device-pairing time the user wears the device for ~10–15 minutes of normal activity, we compute and store `(mean, std)` on-device, and apply at every inference. This compensates for per-subject baseline differences (resting wrist orientation, sensor calibration, build, walking style) that otherwise dominate the feature distributions.

### `tests/` — 21 tests, all math-correctness focused

- `test_pre_impact_labels.py` (8 tests): `find_impact` locates a known synthetic peak to within one sample; rejects below-threshold "impacts"; ignores out-of-window distractor spikes; handles empty windows; raises on shape mismatch. `assign_phase_labels` segments around `t_impact` correctly; clamps `pre_start` to the fall window when the window is shorter than the lead; returns all-BACKGROUND for ADL recordings. `Phase` enum polarity helpers behave as expected.
- `test_features.py` (13 tests): `magnitude` on the classic 3-4-5 triangle + the resting-wrist 1g case + batched (T,3) inputs. `jerk` on constant and linear signals. `per_channel_stats` shape + values (specifically: mean of 0..124 = 62.0, ptp = 124.0, rms = sqrt(mean(x²))). `signal_magnitude_area` formula sanity + fall > walking. `fft_features` picks 5 Hz from a pure 5 Hz sine; walking has lower spectral entropy than white noise. `extract_features` output shape + 43-dim + no NaNs + float32. `feature_names()` is unique + correct length + correctly ordered. Separability test: a synthetic fall has higher `mag_accel_peak`, `jerk_accel_max_abs`, AND `spectral_entropy_accel` than a synthetic walking signal — sanity check that the engineered features actually separate the two classes the way the math says they should.

All seven implementation files (5 source + 2 test) shipped in one work session. The next steps queued: EDA notebook validating the derived `t_impact` distribution against the dataset's manual `start_time` labels, MLflow project setup, baseline model training.

---

## Phase 7 — Documentation & GitHub migration (May 31, 2026)

This phase = the docs you're reading now (`BUILD_LOG.md`, `ARCHITECTURE.md`, `DECISIONS.md`, `AUDIT_v1_v2.md`) plus migrating the rebuilt code from the local `fall-guardian/` sandbox into the GitHub-tracked `fall-detect-system` repository.

Repo strategy: the GitHub repo keeps its original `fall-detect-system` name. The story this preserves — "started this in 2nd year, kept improving it across 3rd year into an industry-grade rebuild" — is a stronger portfolio narrative than starting fresh. Git history retains the v1/v2 commits; anyone can `git log --all` or `git checkout <previous>` to see the original implementation.

Commit strategy: **atomic per-feature commits**, never a single dump. Each commit has a focused human-language subject describing one logical change. Code authorship is mine — there is no AI/automation co-authorship marker on any commit in this project.

The full commit sequence for this migration: see `git log` after the migration commits land.

---

> _End of current sessions. New work appends a new dated section below this line._
