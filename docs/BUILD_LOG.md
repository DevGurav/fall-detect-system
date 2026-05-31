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

> _End of current sessions. New work appends a new dated section below this line._
