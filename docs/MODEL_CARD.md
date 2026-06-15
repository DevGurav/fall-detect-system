# Model Card — Fall Guardian v3

A single card covering both ML components of the system: the on-device **Edge Predictor** (pre-impact fall prediction) and the cloud **Detector** (post-impact fall classification + severity). The card follows the structure popularised by Mitchell et al. (2019), adapted for a two-model system.

This card is a **living document**. Both models are now **trained and shipped** — the edge ConvLSTM-tiny (INT8) and the cloud Transformer (re-exported under 5-fold cross-validation, Phase 30). Measured metrics are in §3.2; the §3.1 targets are kept for comparison. A handful of slice-level breakdowns remain *"To be populated."*

---

## 1. Model details

### 1.1 Overview

| Aspect | Edge Predictor | Cloud Detector |
|---|---|---|
| Role | Pre-impact prediction (alert ~300–500 ms before ground impact) | Post-impact detection (confirm/suppress edge prediction + assign severity) |
| Where it runs | ESP32-S3 wearable, TFLite Micro INT8 | In-process **ONNX** in the FastAPI gateway (local); trained in PyTorch FP32 (ADR-015) |
| Architecture | ConvLSTM-tiny | Transformer encoder over the raw window + fused engineered features (alt: 1D-CNN → LSTM hybrid) |
| Input | Raw 6-channel window (125 samples × {ax, ay, az, wx, wy, wz}) at 50 Hz | Raw 125×6 window **and** the fused 43-dim engineered feature vector |
| Output | `P(pre-impact fall) ∈ [0, 1]` | `P(fall) ∈ [0, 1]` (binary: IMPACT+POST_IMPACT vs not) + severity scalar |
| Size budget | ≤100 KB INT8 | unconstrained (FP32) |
| Latency budget | <80 ms on ESP32-S3 | <500 ms end-to-end |
| Versioning | semver, MLflow-tracked | semver, MLflow-tracked; active export + preserved baseline in-repo (ADR-018, §8) |
| Status (this card) | **Trained + INT8-quantized** — WEDA-FALL, recall 0.965, 256 ms mean lead, ~46 KB; firmware integration in `edge/` (Phase 31) | **Trained + served** — WEDA-FALL, recall 0.970 (5-fold OOF); served in-process as ONNX (stub retired); cascade FPR 0.7%; re-exported under 5-fold CV (Phase 30); continuous-wear /day sim still owed |

### 1.2 Edge Predictor architecture (locked)

```text
Input:  [125, 6]            ← 2.5 s × 6 raw IMU channels
↓ Conv1D(16, kernel=7, stride=2) + ReLU + MaxPool(2)
↓ Conv1D(32, kernel=5, stride=1) + ReLU + MaxPool(2)
↓ LSTM(32 units, return_sequences=False)
↓ Dropout(0.3)
↓ Dense(16) + ReLU
↓ Dense(1) + Sigmoid       → P(pre-impact fall)
```

Quantisation: post-training INT8 via TensorFlow Lite converter. If the model overshoots the 100 KB budget after quantisation, fall back to MicroNAS (Roeschke 2025, arXiv:2504.07397) for a tighter search.

### 1.3 Cloud Detector architecture (locked)

A Transformer encoder over the **raw 125×6 window**, with the **43-d engineered
feature vector fused at the pooled head**. Two heads: a **binary** detection logit
(IMPACT+POST_IMPACT vs not) and a severity regression. This reconciles two earlier
drafts of this card — a "Transformer on the 43-d vector" (a single token has no
sequence for attention) and a 3-class `{ADL, near-fall, true-fall}` softmax (no
"near-fall" label exists in the phase pipeline, and the API is binary `is_fall`).
The binary head matches `pre_impact_labels.Phase.is_positive_for_detection`, the
`InferenceResponse` contract, and the backend stub. See ADR-011 and
`ml/src/fall_guardian_ml/models/transformer_detector.py`.

```text
raw window (125, 6)                ← ax,ay,az,wx,wy,wz @ 50 Hz (no orientation; API carries 6 ch)
↓ Linear projection (d_model = 64) + sinusoidal positional encoding
↓ Transformer encoder × 4 layers (4 heads, d_ff = 128, pre-norm, GELU)
↓ Mean pool over time              → (64,)
   ⊕ concat 43-d engineered feature vector  → (107,)
↓ Dense(32) + GELU + Dropout
├─ Dense(1)  → fall logit          P(fall) via sigmoid (BCE); Platt-calibrated
└─ Dense(1)  → severity            regressed (standardized) peak |a|; → none/low/medium/high
```

Both inputs come from the same window the backend already receives, so train and
serve agree (the backend computes `extract_features()` on the incoming window). A
1D-CNN→LSTM hybrid over the raw window remains the documented empirical alternative,
to be compared on held-out WEDA-FALL (+ later SmartFall) subjects.

### 1.4 Frameworks + versions

- PyTorch ≥ 2.4 (training, evaluation)
- TensorFlow ≥ 2.18 (TFLite conversion + Micro deployment only)
- MLflow ≥ 2.18 (experiment tracking, model registry)
- Python 3.11+

---

## 2. Intended use

### 2.1 Primary intended use

Real-time fall safety monitoring for **elderly users (Indian-context primary)** wearing the Fall Guardian wrist device in their home environment. When a fall is predicted/detected the system alerts a registered caregiver via the Fall Guardian mobile app — live over SSE while the app is open, and via an additive FCM push when it is backgrounded/killed (ADR-016). (The 60 s ack-escalation / SMS path from the original plan is not built; the caregiver acknowledges from the timeline.)

### 2.2 Out-of-scope uses

- **Medical diagnosis or treatment.** The model produces alerts, not clinical determinations. No part of Fall Guardian replaces medical assessment.
- **Safety-critical applications where the cost of a missed fall is loss of life with no other monitoring.** Fall Guardian is one layer of safety; never the only layer for a high-acuity patient.
- **Children, athletes, construction workers, military, automotive applications.** ADL distributions and fall signatures in these populations differ substantially from elderly home users and are not represented in the training data.
- **Pet activity monitoring.** Not designed or evaluated for non-human use.
- **Use by employers/insurers/government to surveil individuals.** The system is for elder-care, with explicit caregiver consent on both sides. Misuse for surveillance violates the privacy framework (see `PRIVACY.md`).

### 2.3 Users

- **Primary user (device wearer):** an elderly person who has consented to wear the Fall Guardian wrist device, typically living independently with periodic caregiver check-ins.
- **Secondary user (caregiver):** a family member or care professional receiving the fall alerts via the Fall Guardian mobile app.
- **Operator:** the entity deploying Fall Guardian (initially the project author Devendra Gurav; later potentially a care provider or self-deployment by families).

---

## 3. Performance

### 3.1 Target metrics (project plan — compare against the measured numbers in §3.2)

| Metric | Edge Predictor target | Cloud Detector target |
|---|---|---|
| Recall on WEDA-FALL held-out subjects | ≥ 95% | ≥ 97% |
| False-positive rate on ADL | ≤ 5% | ≤ 2% |
| Mean lead time | ≥ 300 ms | n/a (post-impact) |
| Model size (INT8) | ≤ 100 KB | n/a (FP32) |
| Inference latency | < 80 ms on ESP32-S3 | < 500 ms end-to-end |
| Cross-dataset generalization (UP-Fall wrist) | recall drop ≤ 10 pp | recall drop ≤ 10 pp |
| End-to-end FP rate (continuous-wear sim) | n/a (component) | ≤ 0.5 per user per day |

### 3.2 Measured metrics

Edge: Week-B run (MLflow `fall-guardian/edge`). Cloud: Week-C, MLflow
`fall-guardian/cloud` — 5-fold subject CV (out-of-fold over all subjects) plus a
single held-out split.

| Metric | Edge Predictor | Cloud Detector |
|---|---|---|
| Recall (overall) | 0.965 | 0.970 (5-fold OOF) · 0.987 (single-split) |
| Precision (overall) | 0.455 | 0.545 (OOF) |
| F1 (overall) | 0.619 | 0.698 (OOF) |
| FPR on ADL (standalone) | 0.203 (accepted; cloud-gated) | 0.072 (OOF) · 0.050 (single-split) |
| Mean lead time | 256 ms | n/a (post-impact) |
| Severity MAE | n/a | ~1.1–1.7 m/s² |
| Artifact size | ~46 KB INT8 | ~1 MB ONNX (FP32) |

**Cascade — the product metric.** The cloud's standalone ADL FPR (5–7%) *fails* the
≤2% gate in isolation, but that's a false bottleneck: the cloud only scores windows
the edge forwards, and the two models make largely **independent** false positives.
Measured on held-out WEDA-FALL ADL, the **edge→cloud joint FPR is 0.7%** (29× below
edge-alone 0.203). The impact-like ADLs the cloud trips on (clapping 15%, hit-table
21%, jump 18% standalone) collapse to ~0% in the cascade; the only residuals are
stumble (a genuine near-fall) and clapping. The cloud meets recall ≥0.97 and
delivers the precision the two-stage design depends on.

**Still owed (queued):** a rigorous **continuous-wear simulation** (realistic
activity mix + alarm burst-debouncing) to turn the 0.7% per-window cascade FPR into
a defensible **≤0.5 alarms/day** number. The per-window figure is on an
adversarial, impact-heavy ADL set (worst case) — encouraging, but not a literal
/day pass yet. The cloud serves in the gateway as an ONNX artifact (`backend/app/
model/`, retiring the heuristic stub).

### 3.3 Slice-level performance

*To be populated at training time.* Slices to evaluate (publish per-slice recall / FPR):

- **By age group**: young (U01–U14, ages 20–46) vs. elder (U21–U31, ages 77–95)
- **By gender**: male (13 subjects) vs. female (12 subjects)
- **By fall type** (F01–F08): forward-slip, lateral-slip, backward-slip, forward-trip, backward-sit, forward-sit-faint, backward-sit-faint, lateral-sit-faint
- **By ADL type** (D01–D11): walking, jogging, stairs, sit-stand, sit-collapse-chair, crouch-tie-shoes, stumble, gentle-jump, hit-table, clapping, door
- **By dataset of origin**: WEDA-FALL only, vs. WEDA-FALL + SmartFall combined, vs. + Indian-ADL

Per-slice gaps > 5 percentage points trigger a model card update + a documented mitigation in the EDA notebook.

### 3.4 Calibration

Probability outputs from both models will be calibrated using **Platt scaling** (sigmoid fit on a held-out validation set) or **isotonic regression** — whichever achieves better calibration error on the validation set. Goal: a stated 0.85 confidence should mean the model is correct ~85% of the time on similar inputs.

---

## 4. Training data

See `docs/AUDIT_v1_v2.md` for why v1/v2's synthetic-only training data was rejected and `DECISIONS.md` ADR-006 for why waist-mounted datasets were dropped.

### 4.1 Primary training corpus — WEDA-FALL

- **Source**: Marques et al. 2024 (Sensors), `github.com/joaojtmarques/WEDA-FALL`
- **Modality**: wrist-worn, Fitbit Sense smartwatch
- **Sample rate**: 50 Hz (resampled to true uniform 50 Hz via linear interpolation — original timestamps are non-uniform due to BLE batching)
- **Subjects**: 25 (14 young U01–U14, ages 20–46; **11 elderly U21–U31, ages 77–95**). 13 male, 12 female.
- **Activities**: 11 ADL types (D01–D11), 8 fall types (F01–F08)
- **Channels**: 3-axis accelerometer + 3-axis gyroscope + orientation quaternion + gravity-projected vertical accel
- **Recording counts**: ~507 fall recordings + ~1080 ADL recordings (per WEDA-FALL README)
- **Ethics note**: elderly subjects did ADL only — no falls — for participant safety. All fall recordings are from young subjects on a mattress.

### 4.2 Secondary corpus — SmartFall (Texas State)

- **Source**: Mauldin et al. 2018 (Sensors)
- **Modality**: wrist-worn, Android smartwatch
- **Sample rate**: ~31–32 Hz
- **Subjects**: 9 elderly (real-world continuous wear, 3 hrs/day × 7 days per subject)
- **Channels**: 3-axis accelerometer only (no gyro)
- **Use**: ADL augmentation for the cloud detector — hardens FPR on natural elderly activity
- **Gyro handling**: zero-padded when feeding SmartFall windows through the cloud feature extractor (acknowledged limitation; per-slice FPR for SmartFall-augmented runs reported separately)

### 4.3 Indian-ADL supplement — DROPPED (superseded by per-user calibration, ADR-013)

> **Not collected.** The planned Indian-ADL corpus (sukhasana, namaste, getting up
> from the floor, squat-toilet, intentional wrist motions) was **dropped at the
> mid-build audit** in favour of **per-user fit-at-first calibration** (ADR-013):
> a ~10–15 min onboarding session captures each user's *own* ADL distribution —
> including whatever Indian-specific motions they personally do — as z-score
> normalisers + a threshold override, applied at inference (§4.6). Personalising to
> each individual beats averaging one collected dataset. The Indian-context FPR is
> therefore addressed by calibration (ADR-011) plus the 5-fold/SmartFall hardening
> (ADR-018), not by a bespoke corpus. The original collection spec is retained below
> for provenance.
>
> *Original spec (not executed):* wrist-worn, 50 Hz, 5–10 subjects (ages 25–75),
> NEGATIVE examples only — sukhasana, namaste, floor rise, squat-toilet, eating/
> waving/brushing/door motions — to cover Indian-context activities absent from
> public datasets.

### 4.4 Cross-dataset evaluation corpus — UP-Fall wrist channel

- **Source**: Martínez-Villaseñor et al. 2019 (Sensors)
- **Modality**: wrist-worn (one of 5 positions; we use only the wrist channel)
- **Sample rate**: 18 Hz
- **Subjects**: 17 young
- **Use**: held-out cross-device generalization test ONLY — never used for training. Verifies the model isn't overfit to Fitbit Sense-specific signal characteristics.

### 4.5 Training data preprocessing

Documented in `ml/src/fall_guardian_ml/datasets/` and `ml/src/fall_guardian_ml/features/`:

1. Non-uniform → uniform 50 Hz resampling via linear interpolation (`weda_fall._resample_to_uniform_hz`)
2. Pre-impact label re-derivation via peak-magnitude detection within the manually-labelled fall window (`pre_impact_labels.find_impact`)
3. 4-phase labelling (`PRE_IMPACT`, `IMPACT`, `POST_IMPACT`, `BACKGROUND`) around the derived impact instant (`pre_impact_labels.assign_phase_labels`)
4. 2.5 s × 50 Hz = 125-sample sliding windows with 50% overlap (`features/windowing.slide`)
5. Pre-impact-aligned extra window per fall recording so the prediction model trains on positive PRE_IMPACT examples (`features/windowing.slide_for_prediction`)
6. 43-dimensional engineered feature vector per window (`features/extraction.extract_features`) — cloud only
7. Per-user Z-score normalization fit on the user's ADL windows (`features/normalization.fit_zscore`)

### 4.6 Per-user retraining data — canceled false alarms (personalization loop)

Once deployed, the product collects an additional, per-user corpus: windows that the edge model flagged but the **user canceled** during the local grace period (the watch buzzes ~10 s; the user presses Cancel). These are uploaded to `POST /v1/retraining`, stored labeled `CANCELED_FALSE_ALARM`, and used to **fine-tune the detector and tune that user's thresholds** — the user is ground truth for their own non-falls. Architecture in `docs/ARCHITECTURE.md` §3.2/§8; rationale in `DECISIONS.md` ADR-011. Properties relevant to this card:

- **Label provenance**: user-confirmed negatives (true ADL the model mistook for a possible fall) — the highest-signal hard negatives available, and exactly the FPR-driving motions worth hardening against.
- **Bias caveat**: this corpus is self-selected per user (only motions that tripped the edge model, only users who bother to cancel). It is suitable for **personalization / FPR reduction**, not as a general fall-recall training source. Any global retraining that incorporates it must guard against drift in the positive (fall) class, which this corpus never contains.
- **Status**: the ingestion + storage path is built (Week C); the storage backend (`RetrainingStore`) and the fine-tuning step itself are stubbed/scheduled (Week E).

---

## 5. Evaluation data

### 5.1 Validation methodology

- **Subject-stratified k-fold cross-validation**: each fold ensures no subject appears in both train and validation. Prevents the dataset-leakage trap that inflated v1/v2's "100% accuracy" claim.
- **Held-out test set**: 20% of subject IDs reserved across the entire experiment — used only for the final metrics published in this card.
- **Cross-dataset evaluation**: UP-Fall wrist channel, never seen during training.

### 5.2 Metrics published

Per validation/test split:

- Precision, Recall, F1 (overall and per slice from §3.3)
- False-positive rate on ADL specifically (the metric that matters for daily-wear comfort)
- ROC curve + AUC
- Confusion matrix
- For the Edge Predictor only: lead-time histogram (distribution of `t_impact - t_alert` across all true-positive predictions)
- Calibration curve (reliability diagram)

---

## 6. Ethical considerations

### 6.1 Health-data sensitivity

The model operates on physiological motion data that, in aggregate, can reveal health conditions (gait abnormalities, tremor, sleep patterns). The product treats this data under the framework in `docs/PRIVACY.md`, which builds on India's Digital Personal Data Protection Act 2023. Key commitments:

- Raw IMU data does not leave the device in steady state — only triggered windows reach the cloud.
- Stored fall events are scoped to the user and their consented caregivers via Postgres row-level security.
- Users can request access, correction, or deletion of their data; the operator must comply within statutory timeframes.

### 6.2 Alert fatigue

A fall-detection product fails in the field not when it misses falls but when it generates too many false alarms — caregivers stop trusting it. The cloud confirmation step exists specifically to suppress the edge model's false positives. The target FP rate of ≤ 0.5 per user per day in continuous-wear simulation is what makes the product usable; if real-world deployment shows higher rates, the model is rolled back via MLflow until tuned.

### 6.3 Bias risks

- **Subject coverage**: WEDA-FALL has 11 elderly but they did ADL only — fall examples come from young subjects. The pre-impact signature on a 23-year-old falling onto a mattress may differ from an 85-year-old falling onto a tile floor. SmartFall (real elderly continuous wear) partially mitigates this for ADL; the fall signature remains an unsolved evaluation gap.
- **Gender**: roughly balanced (13 M / 12 F) in WEDA-FALL.
- **Indian-context**: actively addressed via the Indian-ADL supplement, but the supplement covers ADL only — no Indian-specific *fall* data is collected for safety reasons.
- **Wrist asymmetry**: WEDA-FALL records on one wrist (dominant). Performance on the non-dominant wrist is not evaluated and may degrade.

### 6.4 Failure modes documented in user-facing material

The mobile app must communicate to the user + caregiver that:

- The system can miss falls (recall < 100%).
- The system can raise false alarms (FPR > 0%).
- The system requires the device to be worn correctly + charged.
- The system requires WiFi (for cloud confirmation) — though the edge model still vibrates the user even when offline.

---

## 7. Caveats and limitations

- **Continuous-wear /day metric still owed.** Both models are trained and the measured numbers are in §3.2, but the headline ≤0.5 alarms/user/day figure is not yet a literal pass — the 0.7% cascade FPR is per-window on an adversarial impact-heavy ADL set; the realistic-mix `continuous_wear_sim.py` run is queued (ADR-018).
- **Wrist position dependency.** Performance is only evaluated on wrist-mounted data. The model will perform poorly on waist/chest/ankle deployment.
- **Sampling-rate dependency.** Resampling to 50 Hz is a hard assumption in the pipeline. Devices that sample at substantially different rates will need a different feature pipeline (UP-Fall at 18 Hz is the test case for low-rate degradation).
- **Indian-ADL coverage is project-author's curation.** The original-content nature of the Indian-ADL supplement is a strength (originality) and a limitation (small sample, limited subject diversity). Future work should expand subject pool.
- **Threshold defaults.** PRE_IMPACT lead = 500 ms, guard = 50 ms, post-tail = 500 ms, impact magnitude threshold = 20 m/s². These are literature-informed defaults, not user-specific. Per-user threshold calibration is now a core feature (the canceled-false-alarm personalization loop, §4.6 / ADR-011): the ingestion path that feeds it ships in Week C, and the calibration step itself lands in Week E.

---

## 8. Versioning + provenance

- Model versions follow **semantic versioning** (`edge-vMAJOR.MINOR.PATCH`, `cloud-vMAJOR.MINOR.PATCH`); the served `model_version` is read from the ONNX `.meta.json`.
- Every trained model is logged to the MLflow registry with: training script git hash, hyperparameters, evaluation metrics, confusion matrix + ROC artifacts.
- **In-repo serving + rollback (ADR-018).** The active export lives at `backend/app/model/cloud_detector.onnx` (+ `.meta.json`); the prior **Phase-20 baseline is preserved verbatim** at `backend/app/model_old/`. Rollback / A-B is a one-line `FG_MODEL_PATH` override — no registry round-trip. The active model is the **5-fold cross-validated** re-export (Phase 30).
- The served model's version is exposed on the FastAPI `/health` (and `/health/ready`) endpoint for traceability; a missing artifact falls back to the labelled `stub-0.0` heuristic.

---

## 9. Contact

For questions, bug reports, or responsible-disclosure of issues with the model:

- Project author: Devendra Gurav (`prasad.gurav09@gmail.com`)
- Public repository: `github.com/DevGurav/fall-detect-system`
- Security/privacy issues: see `PRIVACY.md` §9 (Grievance Officer)

---

## 10. Citation

If this work is referenced in academic or industry publications, please cite the project plus the upstream datasets it builds on. A `CITATION.cff` file will be added at the v3.0.0 release tag.

---

*Model Card — drafted 2026-05-31 (pre-training); updated through Phase 30 with measured metrics for both shipped models (edge ConvLSTM-tiny INT8; cloud Transformer served in-process as ONNX, 5-fold cross-validated, with the baseline preserved in `backend/app/model_old/`). Remaining work: per-slice breakdowns (§3.3) and the continuous-wear /day pass (§3.2).*
