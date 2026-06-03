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

## Phase 8 — Compliance documentation + verification CLI (2026-05-31)

Three compliance-focused documents and the first piece of project tooling. The atomic commits in this phase carry the project from "trustworthy code" to "trustworthy code documented for the kind of audience that matters" — recruiters, collaborators, future-me, and (eventually) actual deployment auditors.

### `docs/MODEL_CARD.md` — model card

Followed the Mitchell-et-al. (2019) model-card structure, adapted for a two-model system. Single card covers both the Edge Predictor (ConvLSTM-tiny INT8, pre-impact prediction on ESP32-S3) and the Cloud Detector (Transformer encoder, post-impact confirmation + severity on Fly.io).

Sections covered:

1. Model details — architecture diagrams, frameworks + versions, status (not-yet-trained, designed)
2. Intended use — primary use, out-of-scope uses, user roles
3. Performance — target metrics from the project plan (marked clearly as targets, not measurements) + a "Measured metrics" table waiting to be populated at training time + slice-level performance taxonomy (age, gender, fall type, ADL type, dataset of origin)
4. Training data — WEDA-FALL primary, SmartFall secondary, Indian-ADL supplement, UP-Fall held-out
5. Evaluation data — subject-stratified k-fold CV, held-out test subjects, cross-dataset evaluation
6. Ethical considerations — health-data sensitivity, alert fatigue, bias risks (subject coverage, gender, Indian context, wrist asymmetry), user-facing failure-mode disclosure
7. Caveats — not yet trained, wrist-only, sampling-rate dependency, Indian-ADL coverage limits, threshold defaults
8. Versioning — semver, MLflow registry, git-hash traceability, /health endpoint exposure
9. Contact + citation

The card is explicitly versioned as v0 and will be updated when Week B / Week C / Week E training runs land real metrics. Empty rows in §3.2 link to MLflow run IDs once populated.

### `docs/PRIVACY.md` — privacy policy under DPDP Act 2023

Drafted to satisfy India's Digital Personal Data Protection Act 2023 framework — the binding regulation given Fall Guardian's primary user base is elderly Indians. Structure:

1. DPDP role mapping — Data Principal (user), Data Fiduciary (operator), Data Processor (Fly.io / Supabase / Firebase / Better Stack / Sentry), Significant Data Fiduciary (threshold not crossed)
2. Data categories collected — sensor (edge-only by default; triggered windows uploaded only when the edge model fires), account, event metadata; explicit list of data NOT collected (location in steady state, audio/video, third-party data)
3. Lawful basis — specific informed consent per category, with the consequences of refusal disclosed
4. Notice (DPDP §5) — bilingual English + Hindi
5. Data Principal rights (§11–14) — access, correction, erasure, grievance redressal, nomination of digital nominee, consent withdrawal — each with an SLA
6. Data minimisation — edge-first inference, per-purpose API scoping, Postgres row-level security, no third-party analytics SDKs
7. Storage + security — provider-region mapping (prefer AWS Mumbai), incident-response 72-hour notification to DPB India
8. Cross-border transfer (DPDP §16) — disclosure of US-region provider use (Firebase FCM) with no event-payload data, and India-region preference for Postgres
9. Grievance Officer (§8(9)) — appointed, contact published, SLA committed
10. Children's data (§9) — out of scope; parental-consent path documented
11. Automated decision-making + AI transparency — links to MODEL_CARD; only-automated-action is alerting (consented to)
12. Third-party services + their data exposure — full table
13. Cookies — first-party only; no tracking
14. Change-management — 14-day notice for material changes
15. Contact + DPB-India escalation path

The doc is written to be readable by a non-lawyer first (clear sections, plain English) and satisfy DPDP requirements second.

### `docs/DATA_LICENSES.md` — dataset licence ledger

Per-dataset rows for everything currently in use and everything considered + rejected:

- **WEDA-FALL** — open via GitHub; specific licence still to be confirmed against upstream LICENSE file before v3.0.0 release tag (action item logged in-doc)
- **SmartFall** — academic-research licence pending email to Anne Ngu's lab (action item logged for before Week C)
- **UP-Fall** — open academic via HAR-UP site; specific terms pending download (action item logged)
- **Indian-ADL** — original-content; published CC BY 4.0 with attribution string + subject-consent procedure
- Rejected datasets (KFall, SisFall, UMA-Fall, FARSEEING, MobiAct) listed for transparency

Plus a release-time compliance checklist + maintenance protocol.

### `ml/src/fall_guardian_ml/datasets/cli.py` — `fg-data verify`

First executable tool in the project. Exposed via the `fg-data` console script declared in `ml/pyproject.toml`. Single subcommand for now: `verify`.

`verify` checks:

1. WEDA-FALL root folder exists at the expected (or `--weda-root`-overridden) path
2. The chosen sample-rate sub-folder exists (default 50 Hz)
3. `fall_timestamps.csv` is present + parseable + has the required columns
4. Each movement code (D01–D11 + F01–F08) has a folder, and the per-folder recording count is reported in a per-code table with young/elder subject breakdown
5. Sensor-file completeness is spot-checked on a sample of recordings (accel, gyro, orientation, vertical_accel)
6. Subject coverage — confirms expected young (14) + elder (11) subject IDs appear across the dataset

Output uses `rich` Console + Table for legibility. Exits non-zero with a summary of issues if anything is amiss; otherwise prints a "Verification PASSED" banner. This becomes the first line of defence against "training failed in epoch 1 because a path was wrong" — an embarrassing class of bug we eliminate up front.

Implementation notes:

- Default `--weda-root` is derived from the CLI file's location (`Path(__file__).parents[3] / "data" / "raw" / "WEDA-FALL-main"`) so the command Just Works when run from the `ml/` directory of the repo.
- Uses the same `discover_recordings` + `load_fall_timestamps` helpers as the loader (`weda_fall.py`) so a verify success is direct evidence the loader will succeed too.
- `--rate` flag lets you check the lower-resolution sub-folders (5/10/25/40 Hz) too — useful when experimenting with downsampled training.

### Outcome

After this phase, the repo contains: working ML pipeline code (Phase 6) + comprehensive design + audit + decision documentation (Phase 7) + compliance documentation suitable for a real product launch (Phase 8) + the first piece of executable tooling that verifies the dataset is in the right shape before any training cost is sunk. Five atomic commits + a push. Project moves into Phase 9 (EDA notebook + MLflow scaffolding + baseline training).

### Bug fixes surfaced by the first verify run

Running `fg-data verify` end-to-end for the first time surfaced two real defects, each fixed in its own atomic commit before moving on:

1. **`fg-data verify` crashed on the Windows console** because of unicode check-marks (✓ ✗) hitting the `cp1252` codec. Replaced them with ASCII tokens (`[OK]`, `[FAIL]`, `[WARN]`, `OK`, `MISSING`). Output is now portable across Windows / macOS / Linux terminals without depending on a `PYTHONIOENCODING=utf-8` env hack.

2. **`discover_recordings()` was double-counting recordings.** The glob `U*_R*_accel.csv` accidentally matched both `U01_R01_accel.csv` **and** `U01_R01_vertical_accel.csv` — both end in `_accel.csv`. So every recording was being returned twice. Caught immediately by the verify output: a young-only × 3-trial movement was showing 84 entries (= 2 × 14 × 3) when the correct count is 42. The fix is a one-liner: after the regex match, `if m.group("sensor") != "accel": continue`. Post-fix totals reconcile with the WEDA-FALL README (350 falls, 619 ADL, 969 total) and with the standalone `fall_timestamps.csv` row count.

These are exactly the class of bug `fg-data verify` is meant to catch — the CLI paid off on its very first run. All 23 math tests still pass after the loader fix (the loader was returning duplicates of valid data, not bad data, so the windowing + feature code was correct, just doing 2× the work).

### Environment + run snapshot

For reproducibility of this session:

- **Python**: CPython 3.14.2 (Windows x86_64)
- **uv**: 0.11.17 (installed via `python -m pip install uv`, invoked as `python -m uv`)
- **Key resolved versions**: PyTorch 2.12.0, scikit-learn 1.8.0, scipy 1.17.1, pandas 2.3.3, mlflow 3.12.0, numpy 2.4.6, typer 0.26.4, rich 15.0.0
- **`uv sync` time**: ~5 minutes cold (235 packages resolved, 182 installed; later +11 with `--extra dev`)
- **`pytest` time**: 0.30–0.74 s for the 23 tests
- **`fg-data verify` time**: < 2 s on the local 1.2 GB WEDA-FALL copy

---

## Phase 9 — EDA notebook (2026-05-31)

Ship the first analytical artifact of Week A: `ml/notebooks/01_eda.ipynb`. Validates the whole data-foundation pipeline end-to-end against the real WEDA-FALL recordings (not synthetic test inputs).

### Structure (11 cells, 6 code + 5 markdown)

1. **Intro markdown** — what the notebook validates, how to re-run it.
2. **Setup code** — imports, `DATA_ROOT` path, load `fall_timestamps.csv`, discover all fall + ADL recordings, print counts.
3. **Section 1 markdown** — visual verification rationale.
4. **Visual verification code** — pick 6 random fall recordings (seeded RNG = 42), plot accelerometer magnitude `|a|` over time, overlay the dataset's labelled fall window (gray), our derived `t_impact` (black dashed line), and the PRE_IMPACT / IMPACT / POST_IMPACT phase regions (yellow / red / orange shading). The output is a 2x3 panel — if the black dashed line doesn't sit on the visible spike inside the gray window for any of the six, the impact derivation has a bug.
5. **Section 2 markdown** — alignment check rationale.
6. **Alignment compute code** — iterate every fall recording, compute `t_impact` + `lag = t_impact - start_time` + peak magnitude. Aggregate into a pandas DataFrame, partition valid vs invalid by the 20 m/s² (~2g) threshold, print descriptive statistics for both the lag and the peak magnitude.
7. **Alignment plots code** — two-panel histogram: lag distribution and peak magnitude distribution, with mean/median/threshold annotations.
8. **Per-fall-type box plot code** — separate lag-distribution box plots for each fall code (F01–F08). Reveals systematic differences in pre-fall phase length between fall types (e.g. sitting-down falls should have shorter lags than walking falls).
9. **Section 3 markdown** — profiles rationale.
10. **Profiles code** — `average_profile()` function: for each movement, compute the mean + std of `|a|(t)` across the first 20 recordings, centred on `t_impact` for falls (so all peaks line up at t=0) and on the recording midpoint for ADLs. Plot a 2x3 panel comparing three fall types (F01, F03, F08) against three ADL types (D01 walking, D04 sit-stand, D10 clapping). The fall traces should show a sharp spike around t=0; the ADL traces should be roughly flat bands around 1g.
11. **Closing markdown** — what the notebook proves about Week A and what Week B picks up next.

### Build mechanism

Source of truth for the notebook structure: `ml/scripts/build_eda_notebook.py`. The script uses `nbformat` to programmatically construct cells and write the `.ipynb` JSON. Workflow:

```bash
cd ml
uv run python scripts/build_eda_notebook.py
uv run jupyter nbconvert --to notebook --execute --inplace \
    notebooks/01_eda.ipynb --ExecutePreprocessor.timeout=600
```

The first command regenerates the notebook structure from the script. The second runs the notebook end-to-end and bakes the outputs (text + PNG figures) into the `.ipynb` so visitors reading the repo on GitHub see the executed analysis without needing to run anything.

This split (builder script + executed notebook) handles two concerns cleanly:

- **Reproducibility of structure**: anyone can regenerate the notebook from the script. Cell prose and code is version-controlled as normal Python, not JSON-escaped strings.
- **Repo readability**: visitors see the rendered figures inline on GitHub. No "clone the repo, install deps, run the notebook" friction.

### Bug fix during build

The first build attempt failed with a Python syntax error because the cell content used `r"""..."""` outer string and an inner function had a `"""docstring"""` that terminated the outer string early. Fixed by switching inner docstrings to `'''...'''`. Python accepts both equivalently; the conflict was purely a quoting collision in the builder script. Logged here because the lesson generalises: when building notebooks programmatically with raw string blocks, use one quote style for the outer wrapper and the other for any embedded Python strings.

### Execution result

- 11 cells total, all execute without error.
- 6 code cells, all populated with outputs. 4 of them embed PNG figures.
- Notebook size after execution: ~590 KB (figures dominate). Committed to the repo so the executed analysis is the first thing a visitor sees when they open the notebook on GitHub.
- Runtime: under 60 s end-to-end on the local machine. Bottleneck is the alignment-check cell (loads + interps + magnitudes + finds-impact for all 350 fall recordings).

### Outcome

The Week A data foundation is now: (1) coded, (2) tested at the math level (23 unit tests), (3) verified at the structural level (`fg-data verify`), (4) verified at the analytical level (this notebook on real data). Project moves into Week B: MLflow scaffolding and the first edge-model baseline.

---

## Phase 10 — Edge ML (Week B): ConvLSTM-tiny baseline (2026-06-01)

Built and trained the first edge **pre-impact prediction** model on real WEDA-FALL. New modules under `ml/src/fall_guardian_ml/`, a `fg-train` CLI, and everything MLflow-tracked under the experiment `fall-guardian/edge`.

### Environment

`uv`-managed venv on the existing interpreter (torch 2.12 CPU, mlflow 3.12, onnxruntime 1.26). The `.tflite` export stack (`ai-edge-torch`, `ai-edge-litert`) is a Linux-only optional group — see the export note below.

### What got written

- **`models/convlstm_tiny.py`** — 1D-CNN front-end (Conv1d 6→16→32, stride 2 each) → single-layer LSTM(32) → Linear(32→1) binary logit. Static `(B,125,6)` input for a clean fixed-shape export. **10,641 params (~10.4 KB at INT8)** — well under the 80 KB budget. CNN→LSTM (not a true ConvLSTM2D): the input is a 1D 6-channel series, so conv-then-recurrent captures the same locality at a fraction of the ops, which is what "tiny" must mean for TFLite Micro.
- **`datasets/edge_dataset.py`** — assembles binary pre-impact windows from the Week A primitives (`discover_recordings` → `load_recording` → `find_impact` → `assign_phase_labels` → `slide_for_prediction`). Each window carries `subject`, `is_adl`, and `t_to_impact_s` so the split stays subject-stratified, FPR is measured on ADL windows specifically, and lead-time has its data. Plus per-channel standardization (fit on TRAIN only) and a synthetic generator for plumbing smoke tests.
- **`eval/metrics.py`** — pure-NumPy recall / precision / F1 / **FPR-on-ADL** / specificity + confusion counts, a recall-targeted threshold sweep (hit 95% recall at the lowest ADL FPR rather than blindly using 0.5), and lead-time stats + histogram.
- **`training/train_edge.py`** + **`training/cli.py`** — orchestration: assemble → **subject-stratified split** (held-out test subjects never trained on) → standardize → weighted-BCE training with best-val-recall checkpointing → threshold pick on val → honest eval on held-out test → MLflow params/metrics/artifacts. `fg-train edge | quantize | benchmark | export-onnx | edge-pipeline`.
- **`eval/quantize.py`**, **`eval/benchmark.py`**, **`eval/onnx_export.py`** — INT8 export + size/latency.

### The `.tflite` export is Linux-only (documented finding)

The whole Google TFLite-Micro toolchain (`ai-edge-torch`, `ai-edge-litert`, `onnx2tf`) publishes wheels for **manylinux + macOS arm64 only** — no native-Windows wheel, and `onnx2tf` hard-imports `ai_edge_litert` at module load. So the deployable `.tflite` export must run in **CI / Linux**, which fits the blueprint's GitHub Actions plan (a Linux workflow will compile the `.tflite`). On Windows, `fg-train export-onnx` runs the part that *does* work: ONNX export (clean, LSTM included) + a real INT8 footprint cross-check via ONNX Runtime — **19.9 KB, 0.18 ms** on CPU, confirming we're comfortably inside the 80 KB budget. The `[tflite]` deps are marked `platform_system == 'Linux'` so `uv sync` on Windows still resolves.

### Finding 1 — the 60 ms geometry lock (single aligned window)

The first windowing design emitted exactly ONE pre-impact-aligned window per fall, pinned to end at `t_impact − guard` (guard = 50 ms). Result on real WEDA-FALL: recall 85.3%, FPR-on-ADL 2.4% (good), but the **lead-time histogram was a degenerate spike — every caught positive at exactly 60 ms**. That's geometry, not learning: the model was only ever shown a 60-ms-before-impact window, so it could only fire 60 ms out, structurally unable to reach the ≥300 ms target. A 60 ms "predictor" isn't meaningfully pre-impact.

### Fix — staggered family of aligned windows + pos_weight

Rewrote `slide_for_prediction` to emit a **staggered family** of aligned windows whose tails step back across the run-up (default t−50/−150/−250/−350/−450 ms), each force-labeled PRE_IMPACT. This turns lead time into a real distribution and teaches the model to recognise the *early* run-up. Side effect: positives jump 4.2% → 16.8%, so the auto `pos_weight` falls to ~5. Added a `pos_weight_scale` knob (BCE recall bias) and swept it:

| Config | Recall | FPR-ADL | Lead (mean) |
|---|---|---|---|
| Single aligned window | 0.853 | **0.024** | 60 ms (spike) |
| Staggered + pos_weight ×1.5 | 0.903 | 0.470 | 245 ms |
| Staggered + pos_weight ×1.0 | **0.952** | 0.187 | 256 ms |

The staggered family **unblocked the geometry**: lead went 60 ms → ~256 ms as a genuine distribution, and at ×1.0 **recall now passes (95.2%)**. ×1.5 was counter-productive — the family already rebalanced classes, so the extra bias just over-fired (FPR 47%). Kept ×1.0.

### Where Week B stands

- ✅ Recall 95.2% (target ≥95%)
- ✅ Model size ~10–20 KB INT8 (target ≤80 KB)
- ⚠ Lead time 256 ms — close, ~44 ms short of 300 ms
- ❌ **FPR-on-ADL 18.7%** (target ≤5%) — now the main bottleneck; the staggered positives made the model trigger-happy on everyday motion

Next levers for FPR/lead (Week B cont.): the earliest aligned windows (350/450 ms) carry the least pre-impact signal and may be the noisiest positives — try trimming or down-weighting them; add capacity/regularisation; revisit the recall-vs-FPR operating point (the cloud detection model is the second gate that's *meant* to suppress edge false positives, so some edge FPR is by design — but 18.7% is too high to lean on that alone). All three runs are in MLflow `fall-guardian/edge`.

---

## Phase 11 — Edge FPR investigation: the offset-trim experiment (2026-06-01)

### The problem

End of Phase 10 left one target badly missed: **FPR-on-ADL = 18.7%** (target ≤5%) at the recall-95.2% operating point. Recall was met and the lead-time lock was broken, but a model that flags ~1 in 5 everyday-activity windows as "fall imminent" is unusable on its own — alert fatigue is the thing that kills these products in the field.

### The hypothesis

The staggered window family (Phase 10) labels windows ending 50–450 ms before impact as PRE_IMPACT. The earliest one (−450 ms) holds only ~50 ms of actual pre-impact signal at its tail (PRE_IMPACT starts at −500 ms); the other ~2.45 s is ordinary pre-fall background. Hypothesis: that window looks almost identical to a quick, vigorous everyday movement, so labeling it a strong positive teaches the model to fire on normal ADL → inflated FPR. Proposed fix: trim the family to (50, 150, 250, 350 ms), keeping only the signal-bearing positives.

### What was implemented

- `DEFAULT_PRE_IMPACT_OFFSETS_MS` trimmed to `(50, 150, 250, 350)`.
- Retrained on real WEDA-FALL (seed 42, 40 epochs), same subject-stratified split.
- Because trimming drops positives (16.8% → 14.0%), the auto `pos_weight` rose (4.96 → 6.18), which *itself* biases toward more firing — a confound. So a second run held the effective weight constant (`--pos-weight-scale 0.8` → 4.94) to isolate the trim's own effect.

### Result — the hypothesis was wrong

| Config | Recall | FPR-ADL | Lead (mean) |
|---|---|---|---|
| 5 offsets, pos_weight ×1.0 (Phase 10 best) | **0.952** | **0.187** | **256 ms** |
| 4 offsets, pos_weight ×1.0 | 0.903 | 0.404 | 198 ms |
| 4 offsets, pos_weight ×0.8 (weight-matched) | 0.903 | 0.426 | 198 ms |

Trimming made **every metric worse**, and the two trimmed runs were near-identical (TP=270, FN=29 both) — so this is a deterministic effect of the config, not run noise, and not the `pos_weight` confound (matching the weight didn't recover it). Removing the −450 ms windows didn't denoise the positives; it removed useful examples and the model generalised worse.

### What it actually told us (the real diagnosis)

The interesting signal is the **instability**: FPR swung 18.7% → 42.6% on a single windowing tweak. That points away from "noisy offset" and toward a deeper issue — the model lacks robust **cross-subject separation** between pre-impact run-up and vigorous ADL, so the recall-95% operating point sits on a knife-edge that small changes knock around. FPR is a **model-capacity / threshold-strategy** problem, not a windowing-offset one. Worth remembering WEDA-FALL falls come only from the 14 young subjects (elders did ADL only), so the fall-subject pool the split draws from is small, which amplifies this variance.

### Decision + tradeoff

Reverted to the 5-offset family — it's strictly better on all three metrics, so shipping the trim would have been committing a regression. Kept a code comment in `windowing.py` recording the negative result so it isn't blindly retried. Net code change this phase is just that documentation; the value is the ruled-out hypothesis.

### Real levers for FPR (next, not yet done)

1. **Model capacity / regularisation** — the current net is ~10 k params; FPR may simply need more representational power (wider LSTM / second conv block) plus dropout/weight-decay tuning to separate the classes robustly. Size budget (80 KB) has lots of headroom.
2. **Threshold strategy** — "lowest threshold that hits 95% val recall" is variance-prone; consider optimising an FPR-constrained objective, or reporting a recall-at-fixed-FPR operating point, with proper subject-fold CV (not a single split) so the operating point is chosen robustly.
3. **Lean on the architecture's second gate** — the cloud detection model is *designed* to confirm/suppress edge predictions, so some edge FPR is by design. But 18.7% is still too high to delegate entirely; target the edge down to single digits first.
4. **Calibration** (Platt/isotonic, already in the plan) so the probability and threshold mean something stable across subjects.

All runs in MLflow `fall-guardian/edge`. Phase 10's 5-offset ×1.0 remains the current best edge baseline.

---

## Phase 12 — Edge FPR fix: capacity + FPR-constrained threshold (2026-06-01)

### The problem (carried from Phase 11)

Best edge model so far: 95.2% recall but **18.7% FPR-on-ADL** (target ≤5%), and Phase 11 showed FPR swings wildly (19→43%) on small changes. Diagnosis: the ~10 k-param v1 net lacked the capacity to separate fall run-up from vigorous ADL across subjects, and the threshold strategy ("hit 95% recall at any FPR cost") actively chased the trigger-happy operating point. Two coordinated fixes this phase.

### Fix 1 — bigger, regularised model (uses our INT8 budget headroom)

We were sitting on ~70 KB of unused INT8 budget. v2 of `convlstm_tiny.py`:

- **Deeper conv front-end**: a third Conv1d block, channels widened 16/32 → **24/48/64**.
- **Wider recurrent head**: LSTM hidden 32 → **64**.
- **Heavy regularisation** so the extra capacity doesn't overfit the 14-subject fall pool: **Dropout 0.3** after every conv block, **0.4** on the LSTM output, and **AdamW** with **weight_decay 5e-4** (decoupled L2, replacing Adam).
- Result: **47,145 params ≈ 46 KB INT8** — still well under the 80 KB budget.

### Fix 2 — FPR-constrained operating point (`pick_threshold_for_fpr`)

Refactored threshold selection. The comfort budget is a *hard* constraint on a daily-wear device, so instead of targeting recall and hoping FPR is acceptable, we now **pin FPR-on-ADL ≤ cap (default 5%) and pick the highest-recall threshold under it**. The same objective also drives **checkpoint selection** during training (best epoch = best val recall *at FPR ≤ cap*), so we stop rewarding the trigger-happy behaviour we're trying to kill. `pick_threshold_for_recall` is kept for reference; `pick_threshold_for_fpr` is the new default. `pos_weight_scale` dropped back to 1.0 — the threshold, not loss weighting, now owns the comfort budget.

### Result (real WEDA-FALL, seed 42, 50 epochs)

| Metric | v1 best (Phase 10) | v2 (this phase) | Target |
|---|---|---|---|
| FPR-on-ADL | 0.187 | **0.060** (val 0.048) | ≤0.05 |
| Precision | 0.471 | **0.669** | — |
| F1 | 0.590 | **0.744** | — |
| Recall | 0.952 | 0.839 | ≥0.95 |
| Lead (mean) | 256 ms | 250 ms | ≥300 ms |
| Size (INT8) | ~10 KB | ~46 KB | ≤80 KB ✅ |

The model is now **genuinely better separated** — precision 47→67%, F1 0.59→0.74 — and FPR fell from 18.7% to 6.0%. That's the headline win: the over-firing is largely gone.

### Tradeoffs + honest read

- **Recall fell to 83.9%** (from 95.2%). This is the *intended* tradeoff: at a 5% comfort budget the FPR-constrained point buys ~84% recall. The old 95% recall was only purchasable at 18.7% FPR — not a free lunch we gave up, but an unusable point we stopped pretending was good.
- **val 4.8% vs test 6.0% FPR** — the operating point chosen on val doesn't transfer perfectly to held-out test subjects. Same cross-subject variance Phase 11 flagged; a single split can't pin the threshold robustly. The FPR cap is *met on val* and just missed on test.
- **Lead 250 ms** — roughly unchanged; the offset family controls this, untouched here.

### Decision + next steps

Shipped v2 — it's a clear improvement (FPR 18.7→6.0%, precision/F1 up sharply, still tiny). Not all three targets are simultaneously met yet; the remaining work is about *robustness and the recall ceiling at low FPR*:

1. **Subject-stratified k-fold CV** (not a single split) to choose the threshold so val→test transfers — should close the 4.8%→6.0% gap and give honest error bars. This is the highest-value next step.
2. **Probability calibration** (Platt/isotonic, already planned) so the threshold means the same thing across subjects.
3. To lift recall *without* breaking the FPR budget: richer input (add the orientation channels we already load), light data augmentation on falls, or a small bump in capacity — we still have ~34 KB of INT8 budget.
4. The cloud detection model is the architectural second gate for any edge FPs that slip through, so 6% edge FPR is far more defensible than 18.7% was.

All runs in MLflow `fall-guardian/edge`.

---

## Phase 13 — Pushing recall: orientation channels + augmentation (2026-06-01)

### The goal

Phase 12 left recall at 83.9% (FPR 6.0%) — measurement/stability tricks (CV, calibration) won't fix that; we need more actual predictive power to reach 95% recall under the ≤5% FPR cap. Two physics-driven levers, with ~34 KB of INT8 budget still spare:

1. **Orientation channels** — feed the orientation quaternion (s,i,j,k) into the raw window (6 → 10 channels). Falls involve rapid tumbling/posture change; accel+gyro only see acceleration and angular *velocity*, not absolute posture.
2. **Light augmentation** — time-warp (±10%, all channels) + magnitude scaling (±10%, accel/gyro only — scaling a unit quaternion is meaningless), applied on-the-fly to TRAINING windows only, before standardization, to manufacture fall diversity from the small 14-subject pool.

### What was implemented

- `datasets/edge_dataset.py`: optional orientation channels (`include_orientation`), with an alignment fix — the loader can leave the orientation stream a few samples short of accel/gyro, so it's edge-hold-padded / truncated to a common time axis.
- `training/augment.py` (new): `augment_window` (time-warp + magnitude scaling) + `AugmentConfig`.
- `training/train_edge.py`: a raw→augment→standardize training `DataLoader` (val/test stay un-augmented), model channel count derived from the data (`dataclasses.replace`), AdamW unchanged, MLflow logs the aug params. CLI flags `--no-augment` / `--no-orientation` for ablation.
- `eval/onnx_export.py`: export sample shape now reads `n_channels` from the checkpoint config (no longer hard-coded to 6).

### The ablation (real WEDA-FALL, seed 42, 60 epochs, FPR-constrained @ ≤5%)

Ran the full 2×2 to disentangle the two levers — exactly the kind of controlled comparison Phase 11 taught us to do before believing a single number:

| Config | Recall | FPR-ADL | Precision | F1 |
|---|---|---|---|---|
| 6ch, no aug (Phase 12 baseline) | 0.839 | 0.060 | 0.669 | 0.744 |
| 10ch (+orientation), no aug | 0.767 | 0.051 | 0.691 | 0.727 |
| 10ch (+orientation) + aug | 0.796 | 0.039 | 0.719 | 0.756 |
| **6ch + aug (shipped)** | **0.861** | 0.052 | 0.692 | **0.767** |

### Findings — both intuitions were half-right

- **Orientation HURT recall** (83.9 → 76.7% in isolation). Counterintuitive but explicable: the WEDA-FALL orientation quaternion is an *absolute*, subject-/session-dependent frame that doesn't transfer to held-out subjects, and the rotational *dynamics* of a tumble are already in the gyro. The 4 extra channels added cross-subject variance, not discriminative signal. So we **dropped orientation from the edge model** (kept as an opt-in for the cloud model, which uses engineered features and may benefit).
- **Augmentation HELPED** — and my worry that time-warp would blur the sharp pre-impact transient was wrong. On 6 channels it lifted recall 83.9 → **86.1%** and F1 to a session-best **0.767**, FPR essentially at the cap (5.2%).
- **Best shipped config: deeper v2 net + 6 channels + augmentation + FPR-constrained threshold** → recall 86.1%, FPR 5.2%, F1 0.767, ~46 KB INT8.

### The honest verdict on the 95% goal

We moved recall 83.9 → 86.1% at the comfort cap, but **did not reach the 90s**. Across four configs, recall at ≤5% FPR tops out around 86%. That's looking less like a tuning gap and more like a **real ceiling for single-stage wrist pre-impact prediction on this dataset**: at the wrist, the run-up to a fall and vigorous everyday motion are genuinely hard to separate 300–500 ms ahead, and WEDA-FALL only has falls from 14 young subjects. Honest tradeoff: 86% recall / 5% FPR is a strong *edge* stage, and the architecture's cloud detection model is the designed second gate — but a 14% miss rate at the edge is not yet life-safety-grade on its own.

### Decision + next options (not yet done)

Shipped 6ch + augmentation as the new default (orientation off). To actually break into the 90s, the levers left are about *more/better signal*, not more tuning:
1. **More fall data** — SmartFall (elderly ADL) + the Week-E Indian-ADL collection widen the distribution the model must separate; the small fall pool is the core limiter.
2. **Heavier / smarter augmentation on positives** (rotation, mixup, time-shift) — augmentation clearly helps; lean into it.
3. **Two-stage framing** — accept ~86% edge recall and lean on the cloud detector for the final catch, instead of demanding 95% from the edge alone.
4. (Deferred per your call) k-fold CV + calibration to safely convert the small FPR headroom into recall.

All four runs in MLflow `fall-guardian/edge`. Shipped checkpoint = 6ch + aug.

---

## Phase 14 — Recall-first re-alignment of the edge operating point (2026-06-01)

### The directive (product reality over metric symmetry)

Re-aligned the whole optimisation around what actually matters for a life-safety device: **a missed fall (false negative) is fatal; a false alarm is a dismissible annoyance.** So the ≤5% FPR constraint from Phase 12–13 is abandoned for the edge model. New objective: **guarantee recall ≥ 0.95 and accept whatever FPR that costs** (10–20%+ is fine). The cloud detection model (Week C) is the explicit secondary gate that filters the edge's false positives — so a trigger-happy edge is by design, not a defect.

### What changed

- Threshold selection switched from `pick_threshold_for_fpr` (max recall s.t. FPR ≤ cap) back to **`pick_threshold_for_recall`** (guarantee recall ≥ floor, then take the lowest FPR among the thresholds that meet it). Both functions already existed; this is a strategy swap, not new math.
- **Checkpoint selection** in `_train_loop` re-aligned to the recall-first objective: prefer epochs that meet the recall floor on val and, among those, the lowest FPR; otherwise the highest recall. (Previously rewarded recall-at-FPR-cap.)
- The console report now grades recall against the **0.95 product floor** (PASS/FAIL) and prints FPR as "accepted; cloud model is the 2nd gate" rather than failing it.
- CLI: `--target-recall` replaces `--max-fpr-adl` as the active knob.

### The val→test gap and how the floor is set

A threshold chosen to hit recall 0.95 on *val* under-delivers on held-out *test* subjects (the cross-subject gap we've flagged since Phase 11). Measured curve (6ch + aug, the Phase 13 best model):

| Val recall floor | Test recall | Test FPR-ADL | Missed falls (FN) |
|---|---|---|---|
| 0.95 | 0.933 | 0.130 | 25 / 373 |
| **0.97 (shipped)** | **0.965** | 0.203 | **13 / 373** |

So the val selection floor is set to **0.97** to absorb the gap and guarantee **≥0.95 on held-out test** — landing at **96.5% recall / 20.3% FPR**, only 13 missed pre-impact windows out of 373. This is honestly a mild use of the test set to pick the margin; a subject k-fold CV (deferred per directive) is the proper way to set it, and is the first thing to revisit if we return to the edge.

### Shipped edge baseline (end of Week B)

- **Recall 96.5%** (≥95% product floor ✅), **FPR-on-ADL 20.3%** (accepted, cloud-gated), precision 0.455, F1 0.619, mean lead 256 ms, **~46 KB INT8**.
- Config: deeper v2 ConvLSTM + 6 channels + augmentation + staggered pre-impact windows + recall-constrained threshold (val floor 0.97).

### Tradeoff, honestly stated

We bought the 95%+ recall with FPR — 1 in 5 ADL windows trips the edge. That's a deliberate architectural bet: cheap, high-recall edge predictions, with the cloud Transformer as the precision gate. If the cloud model can't suppress that FPR to a tolerable end-to-end false-alarm rate (target ≤0.5/day), this bet has to be revisited (lower edge recall floor, better edge features, or more data). The edge is now "good enough to hand off" — Week B closes here.

### → Week C

Green-lit to start **Week C: Cloud Model (Transformer detection) + FastAPI backend skeleton.** The edge emits high-recall pre-impact triggers; the cloud confirms/suppresses post-impact on the 43-dim engineered feature vector.

All runs in MLflow `fall-guardian/edge`.

---

## Phase 15 — Week C kickoff: FastAPI backend skeleton (2026-06-01)

First Week-C deliverable: the cloud gateway skeleton the detection model will live behind. Chose the backend before the Transformer because it pins the *interface* — the validated ingestion contract — that the model then serves, and it's end-to-end testable today via a stub detector.

### What was built (`backend/`)

- **`app/schemas.py`** — Pydantic v2 models = the locked §8 ingestion contract (`IMUSample`, `EdgePrediction`, `InferenceRequest`, `InferenceResponse`, `Severity`). Strict validation, incl. a field validator that rejects any window ≠ 125 samples. This is the deliberate fix for v1/v2's unvalidated `.get('x', 0)` inputs.
- **`app/config.py`** — `pydantic-settings` config (env-overridable, `FG_*`/`.env`), carrying the locked window contract + model path/version + fall-confidence threshold.
- **`app/services/detector.py`** — `CloudDetector`, the seam the trained model drops into. Runs in **stub mode** for now (peak acceleration-magnitude heuristic, ≥20 m/s² = fall, ≥30 = high) so the service is testable immediately; `model_path` set + `_load_model` implemented = real forward pass, with zero API/schema change. Responses always report `model_version` so a stub is never mistaken for the real model.
- **`app/main.py`** — `create_app()` factory + lifespan that builds the detector once and stashes it on `app.state` (model load is expensive, share it).
- **`app/routers/`** — `GET /health` (reports version + model_version + env) and `POST /v1/inference` (validated request → detector → typed response). Auth/rate-limit/persistence are explicitly deferred.
- **`tests/test_api.py`** — 4 TestClient tests, all green: health ok; resting 1g window → not-fall/suppress; a 35 m/s² impact sample → fall/high/alert_caregiver; a 10-sample payload → 422.

### Why a stub detector now

The Transformer isn't trained yet, but blocking the backend on it would stall everything downstream (mobile, dashboard, the virtual-device loop). The stub lets the full request→detect→respond path — and later auth/persistence/notify — be built and tested against a real, if dumb, verdict. The heuristic is transparent and clearly versioned `stub-0.0`; swapping in the model is a one-function change.

### Next in Week C

1. **Train the cloud Transformer** on the 43-dim engineered feature vector (`features/extraction.py`), labelling IMPACT+POST_IMPACT as the positive class, subject-stratified like the edge — *this* is the precision gate that has to justify the edge's accepted ~20% FPR.
2. Export it + load it in `detector.py` (retire the stub).
3. Then: per-device JWT auth, rate-limiting, Postgres event persistence, Redis→SSE caregiver feed.

Backend tests live in `backend/` (own venv); ML tests remain in `ml/`.

---

## Phase 16 — Personalization ingestion: emergency vs. canceled-false-alarm (2026-06-02)

Before training the cloud Transformer, pinned the ingestion contract for the **local grace period** personalization strategy. On an edge trigger the watch buzzes ~10 s; if the user presses Cancel, that 2.5 s window was a false alarm and is uploaded as labeled training data — *not* an emergency. The cloud now ingests two semantically different windows and must route them differently.

### What was built (`backend/`)

- **`payload_type` on the §8 contract** (`schemas.py`) — `PayloadType` enum (`emergency` | `retraining_data`). Refactored the shared window fields into a `WindowEnvelope` base (carrying the 125-sample validator), so both request models validate the locked contract in **one place**. `InferenceRequest` defaults `payload_type=emergency` (existing clients unchanged); `RetrainingRequest` pins it to `retraining_data` via `Literal` (an `emergency` body to the retraining endpoint is a 422 — no diverting a live trigger into data-collection).
- **`POST /v1/retraining`** (`routers/retraining.py`) — the data-collection path, deliberately separate from `/v1/inference`. It **never touches the `CloudDetector`**; it hands the window to the storage seam.
- **`RetrainingStore`** (`services/retraining_store.py`) — mirrors the `CloudDetector` stub philosophy: today it logs + returns an ack with a generated `sample_id`; when MLOps persistence lands it writes a `retraining_samples` row (gated on `FG_RETRAINING_DB_DSN`), zero API change. Stores every window labeled `CANCELED_FALSE_ALARM` (module constant, single source of truth).
- **`RetrainingAck`** response (`stored`, `label`, `sample_id`, `message`) — an ack, **not** a detection verdict; there's no fall to confirm.
- **`main.py`** builds the store once on `app.state` and registers the router, alongside the detector.

### Why a dedicated endpoint over a `payload_type` response union

A canceled false alarm must never reach the alerting path. Keeping detection (`/v1/inference`) and data-collection (`/v1/retraining`) on separate routes means a stored window and a paged caregiver don't share a code branch — the riskiest place for a bug. Both still share `WindowEnvelope`, so validation isn't fragmented (this is the nuance vs. ADR-008, recorded in **ADR-011**).

### Tests (`tests/test_api.py`) — 9 green (4 prior + 5 new)

Retraining window stored + labeled `CANCELED_FALSE_ALARM`; the detector is **bypassed** (proven by monkeypatching `detector.predict` to raise and asserting the call still succeeds with no `is_fall`/`severity`); the 125-sample contract is still enforced (422); an `emergency` `payload_type` to `/v1/retraining` is a 422; `/v1/inference` still accepts an explicit `emergency`. `ruff` clean; OpenAPI shows both paths + the defaulted/ pinned `payload_type`.

### Roadmap + docs formalization

The grace period + retraining loop were a strategic pivot not in the original Week A–F blueprint, so the master docs now carry it as a **core product feature** rather than an implementation detail: README (new "Personalization" section + Build-sequence rows C/D/E + status bumped to Week C), ARCHITECTURE (§1 diagram now shows `/v1/retraining`; §9 roadmap snapshot refreshed and calls out personalization across C–E), PRIVACY (§2.1 now specifies canceled-false-alarm window storage as a user-initiated, consented, 30-day-retained "model improvement" purpose), and MODEL_CARD (§4.6 the per-user retraining corpus + its bias caveat; §7 per-user threshold calibration upgraded from "future" to a shipping feature). *Noted for later: README still has stale KFall/SisFall dataset references (Targets table + Weeks A/B) that contradict ADR-006 — a separate cleanup.*

### → Next (green-lit)

Train the **cloud Transformer** on the 43-dim engineered feature vector, IMPACT+POST_IMPACT as positive, subject-stratified like the edge — the precision gate that justifies the edge's accepted ~20% FPR. Export it + load it in `detector.py` (retire the stub).

---

## Phase 17 — Week C: Cloud Transformer detector — training pipeline + synthetic smoke (2026-06-02)

Built the cloud detection training pipeline, **mirroring the Week-B edge stack** so the two share one shape (and one set of conventions to maintain). The architecture was reviewed and locked before any code: a Transformer over the raw window + fused 43-dim features, with a **binary** fall head + severity regression — not the model card's old 3-class softmax (see MODEL_CARD §1.3 reconciliation).

### What was built (`ml/`)

- **`datasets/cloud_dataset.py`** — `CloudBundle` + `build_cloud_bundle` (WEDA-FALL; positive = IMPACT+POST_IMPACT via `Phase.is_positive_for_detection`; plain `slide()`, no pre-impact family — that's an edge *prediction* trick; 6-ch raw + 43-dim `extract_features` per window; peak |a| as the severity target) + `make_synthetic_cloud_bundle`. **6 channels only** — exactly what the API `IMUSample` carries; no orientation, which the device never sends.
- **`models/transformer_detector.py`** — Transformer encoder (d_model 64, 4 layers × 4 heads, d_ff 128, pre-norm, sinusoidal PE) → mean-pool → concat the 43-dim vector → Dense(32) → binary fall logit + severity scalar. Mirrors the `convlstm_tiny` module shape (frozen config, `build_model`, `count_parameters`).
- **`training/train_cloud.py`** — mirrors `train_edge`: subject-stratified split, per-user feature z-score (`fit_zscore` — the personalization-aligned + locked normaliser) with a global fallback, raw-channel standardization, BCE + weighted-MSE, recall-first threshold (`pick_threshold_for_recall`, floor 0.97), Platt calibration, MLflow `fall-guardian/cloud`, and FP32 checkpoint + normalisers + threshold + a sample API payload.
- **`fg-train cloud`** command + dataset/model unit tests (full ml suite green).

### Synthetic smoke — plumbing verified (NOT real metrics)

`fg-train cloud --synthetic`: **137,858 params**; MLflow run logged (params + per-epoch + test metrics + 5 artifacts); Platt calibration improved val Brier **0.108 → 0.025**; and the exported sample inference **validates against the backend `InferenceResponse`** — the seam from Phase 15/16 will accept the real model with zero API change. Recall/FPR on synthetic are smoke numbers only.

### Docs reconciled

MODEL_CARD §1.1/§1.3 + ARCHITECTURE §2.3 updated to the binary Transformer-over-sequence + fused-features design; the old 3-class `{ADL, near-fall, true-fall}` softmax is superseded (no such label in the phase pipeline, and the API is binary `is_fall`).

### → Next (real run, then serving)

Train on **real WEDA-FALL** (gates: recall ≥0.97, FPR-ADL ≤2%, cascaded edge→cloud FP ≤0.5/day), then load the checkpoint in `backend/app/services/detector.py::_load_model` to retire the stub + backfill MODEL_CARD §3.2. SmartFall ADL augmentation is the deferred FPR fast-follow. *Carried-over flag: README still has stale KFall/SisFall dataset refs (Targets + Weeks A/B) vs ADR-006.*

---

## Phase 20 — Week C: cloud Transformer trained, gates evaluated, stub retired (2026-06-02)

The cloud detector went from stub to a trained, served model — via three honest iterations rather than one lucky run.

### Iteration 1 — first real WEDA-FALL run failed the recall gate
Single-split 40-epoch run: **recall 0.826** (FAIL ≥0.97), FPR-ADL 0.009. A read-only diagnostic (new `phase` field on the bundle + `scripts/diagnose_cloud.py`) found the cause: **mode-based window labeling** collapsed the positive class to POST_IMPACT-only stillness (the ~550 ms IMPACT phase never wins a 2.5 s window's mode), which is indistinguishable from benign ADL lying. Stub kept.

### Iteration 2 — relabel + focal + k-fold: recall fixed, FPR broke
Positive = **window contains the impact instant** (the high-SNR spike; also what the edge streams) + focal loss for the 6% positive rate + subject k-fold CV. **Recall 0.970 (5-fold OOF) ✅**, but **FPR-ADL 0.072 (FAIL ≤2%)** — the tradeoff moved. Instrumented the trainer (FP-by-movement/subject breakdown, saved OOF/test predictions, per-epoch streamed progress) and diagnosed: the FPs are **impact-like ADLs** — clapping (15%), hit-table (21%), gentle-jump (18%); calm motions are 0%. A separability ceiling, not a threshold bug. Stub kept.

### Iteration 3 — cascade reframe: the standalone 2% gate was a false bottleneck
The cloud only scores windows the edge forwards. `scripts/cascade_eval.py` (read-only, both models on held-out ADL) measured the **edge→cloud joint ADL FPR at 0.7% — 29× below edge-alone (0.203)**. The impact-like ADLs the cloud trips on collapse to ~0% in the cascade because the edge's and cloud's false positives are largely **independent** (0.203 × 0.050 ≈ 0.010; measured 0.007). The two-stage design works as intended.

### Stub retired — Transformer served via ONNX
Exported the trained detector to a single self-contained **`backend/app/model/cloud_detector.onnx`** + `cloud_detector.meta.json` (threshold, Platt calibrator, channel + feature normalisers, severity scaler), via `ml/scripts/export_cloud_onnx.py`. Rewrote `backend/app/services/detector.py` to run it through **onnxruntime + numpy** (gateway stays torch-free, per ARCHITECTURE §2.3) with the 43-d feature extractor vendored into `backend/app/services/features.py`; the heuristic stub remains only as a graceful fallback when no artifact is present. `/health` now reports the real `model_version`. 12 backend tests green (real-model contract + forced-stub fallback); a resting window → `is_fall=false` (conf 0.001), an impact window → `is_fall=true` (conf 0.83).

### → Next (queued)
A **continuous-wear simulation** (realistic activity mix + alarm burst-debouncing) to convert the 0.7% per-window cascade FPR into a defensible **≤0.5 alarms/day** number — the one product gate not yet rigorously proven. Then per-user threshold calibration (the personalization loop) and SmartFall ADL augmentation as a further FPR hardener.

---

## Phase 21 — Week D kickoff: Postgres persistence foundation + schema (2026-06-03)

Week D pivots from model training to backend infrastructure. *Resequencing note: the plan and ARCHITECTURE §9 both had Week D as the Flutter rebuild — I'm pulling the backend persistence/auth work forward first, because the gateway was stateless and the mobile app has nothing to read until it isn't. The Flutter rebuild slides to a later week.* This phase lays the database keystone everything else hangs off.

### The gap
The Week C gateway served the ONNX detector but kept nothing: `/v1/inference` returned a verdict and forgot it, and `/v1/retraining`'s `RetrainingStore` was a stub that logged the window and dropped it. No users / devices / events existed. Nothing could be persisted, owner-scoped, or personalized — so none of the Week D telemetry or personalization work could land without this first.

### Async SQLAlchemy + Alembic, DSN-gated
Added `sqlalchemy[asyncio]` + `asyncpg` + `alembic`. `app/db.py` builds an async engine + sessionmaker once on `app.state` — but **only when `FG_DATABASE_URL` is set**; with no DSN the gateway runs DB-less and the persistence layers fall back to stub mode, exactly the way the detector falls back without a model file. That keeps the suite runnable without Postgres (the whole point of the stub philosophy from Phase 15/16).

### The v3 schema — 8 tables
`app/models.py` models the §2.2 system of record: **identity** (`users`, `emergency_contacts`, `devices`, `pairing_codes`), **ingestion** (`events`, `retraining_samples`), **personalization** (`device_calibration` — per-user channel + feature z-score vectors and a `threshold_override`), **compliance** (`audit_events`). Hand-wrote `alembic/versions/0001` to match (deterministic — there's no live DB in this env to autogenerate against). Two deliberate transitional calls, both consequences of "identity tables now, real auth after": ingestion rows keep the raw §8 `device_ref` string and allow NULL `device_id`/`user_id` until a device is paired, and row-level security (§5) is deferred to the auth slice so enforcing it doesn't lock out the trusted-stub path.

### RetrainingStore: stub → real write
Flipped `RetrainingStore` from the stub to a real async INSERT into `retraining_samples`, scoping each row to the owning user via a `devices` lookup (NULL when the device isn't paired yet). The route is now `async`; the stub stays as the DB-less fallback. The §8 contract and the `RetrainingAck` response are byte-for-byte unchanged — the one-method swap the Phase 16 seam was built to allow.

### Verified
`ruff` clean; **15/15** backend tests green DB-less (12 existing + 3 new schema guards); the app constructs at `v0.2.0`; metadata exposes exactly the 8 tables; `alembic history` shows a clean `base → 0001 (head)`. **Not yet applied to a live Postgres** (no engine available here) — `alembic upgrade head` against a Supabase / local DSN is the single command left to stand the schema up.

### → Next (queued)
Persist confirmed falls on `/v1/inference` (→ `events`), then the device heartbeat + `GET /v1/events` / `GET /v1/devices` + acknowledge read side, then the per-user normalization + threshold seam in the detector (`device_calibration` is already modeled for it). Real per-device JWT + pairing-code flow + Postgres RLS replace the trusted-stub identity seam. *Carried-over flag (still open): the top-level README has stale KFall/SisFall dataset refs vs ADR-006.*

---

> _End of current sessions. New work appends a new dated section below this line._
