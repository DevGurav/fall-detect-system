# Data — WEDA-FALL, SmartFall, UP-Fall, Indian-ADL

This sub-project trains the edge **prediction** model and cloud **detection** model on **wrist-worn** IMU datasets. Building a wrist-worn product on waist-mounted data (KFall, SisFall) is a sensor-position domain mismatch we explicitly reject.

**Data is not committed to git** (see `.gitignore`).

Directory layout (all under `ml/data/`, gitignored):

```text
ml/data/
├── raw/                  ← original downloads
│   ├── WEDA-FALL-main/   ← primary training dataset
│   ├── smartfall/        ← secondary (ADL diversity, real elderly)
│   ├── upfall/           ← cross-dataset generalization testing only
│   └── indian_adl/       ← your own collection (Week E)
├── processed/            ← cleaned, resampled, sliding-windowed, feature-engineered
├── interim/              ← intermediate artifacts (per-recording numpy arrays)
└── external/             ← any additional supplementary data
```

---

## 1. WEDA-FALL — primary training dataset (wrist, elderly + young)

- **Citation:** Marques, J. *et al.* (2024). *Wrist-Based Fall Detection: Towards Generalization across Datasets*. Sensors.
- **Paper:** <https://www.mdpi.com/1424-8220/24/5/1679>
- **Dataset (GitHub):** <https://github.com/joaojtmarques/WEDA-FALL>
- **License:** Open (academic + commercial use; see repo)
- **What it has:**
  - **Wrist-worn** Fitbit Sense smartwatch
  - **25 subjects** — 14 young (U01–U14, ages 20–46) + **11 elderly (U21–U31, ages 77–95)**
  - **11 ADL types** (D01–D11): walking, jogging, stairs, sit-stand, crouch, stumble, hop, hit table, clapping, door
  - **8 fall types** (F01–F08): forward/lateral/backward × walking/sitting/slip/trip
  - **Accelerometer + gyroscope + orientation (quaternion)** — Note: elders did ADL only, no falls (safety reasons)
  - **Available sample rates**: 5, 10, 25, 40, **50 Hz** — we use **50 Hz** (matches our locked 2.5 s × 50 Hz window)
  - Manually-labeled `fall_timestamps.csv` with start/end of each fall (the 4 phases: pre-fall, impact, body-adjustment, post-fall)

### Download

1. Visit <https://github.com/joaojtmarques/WEDA-FALL> and download the repo as ZIP (Code → Download ZIP).
2. Extract into `ml/data/raw/`. Expected post-extraction:

   ```text
   ml/data/raw/WEDA-FALL-main/
   ├── README.md
   └── dataset/
       ├── fall_timestamps.csv          ← manually-labeled fall windows
       ├── 50Hz/                        ← we use this sample rate
       │   ├── D01/  U<id>_R<trial>_{accel,gyro,orientation,vertical_accel}.csv
       │   ├── ...
       │   ├── D11/
       │   ├── F01/
       │   ├── ...
       │   └── F08/
       ├── 40Hz/   25Hz/   10Hz/   5Hz/  ← unused (lower-rate downsamples)
   ```

### File-format notes

- Each recording has **4 sensor CSVs**: `accel`, `gyro`, `orientation` (quaternion), `vertical_accel` (gravity-projected). We use accel + gyro + orientation for v3.
- Column names follow `<sensor>_time_list, <sensor>_x_list, ...`. The orientation file has `orientation_s_list, orientation_i_list, orientation_j_list, orientation_k_list` (quaternion s+i+j+k components).
- **The `*_time_list` column is non-uniform** (Bluetooth-batched delivery from the Fitbit Sense). Our loader resamples to true uniform 50 Hz via linear interpolation before any windowing.

---

## 2. SmartFall — secondary dataset (wrist, real elderly ADL)

- **Citation:** Mauldin, T. *et al.* (2018). *SmartFall: A Smartwatch-Based Fall Detection System Using Deep Learning*. Sensors.
- **Paper:** <https://www.mdpi.com/1424-8220/18/10/3363>
- **Dataset request:** Texas State University, Anne H. Ngu's lab — userweb.cs.txstate.edu (request via email)
- **What it has:**
  - **9 elderly subjects** wearing an Android smartwatch ~3 hrs/day × 7 days each (real-world continuous wear, not scripted sessions)
  - **Accelerometer only** at ~31–32 Hz (limitation — no gyro)
  - High ADL diversity in *uncontrolled* settings — closer to deployment reality than scripted lab data
- **Why use it:** ADL augmentation for the cloud detection model, especially for the false-positive-rate target on natural elderly activity. The lack of gyro means we'll either use accel-only features for SmartFall windows or zero-pad the gyro channel — to be decided during EDA.

### Download

Email Anne Ngu at <a.ngu@txstate.edu> or the listed lab contact requesting the SmartFall dataset for academic research. Extract into `ml/data/raw/smartfall/`.

---

## 3. UP-Fall (wrist channel) — cross-dataset generalization test

- **Citation:** Martínez-Villaseñor, L. *et al.* (2019). *UP-Fall Detection Dataset: A Multimodal Approach*. Sensors.
- **Paper:** <https://pmc.ncbi.nlm.nih.gov/articles/PMC6539235/>
- **Dataset download:** <https://sites.google.com/up.edu.mx/har-up/>
- **What it has:**
  - 17 **young** subjects (ages 18–24)
  - 5 wearable positions (we use **only the left-wrist channel**)
  - 6 ADL + 5 fall types
  - Sample rate: **18 Hz** (much lower than WEDA-FALL — useful as a noisy generalization test)
- **Why use it:** Cross-dataset validation only. After training on WEDA-FALL, we evaluate on UP-Fall wrist windows to verify the model generalizes beyond a single device (Fitbit) and protocol. NOT used for primary training.

### Download

Follow the registration link on the HAR-UP site. Extract the wrist sensor subset into `ml/data/raw/upfall/`.

---

## 4. Indian-ADL supplement (collected in Week E)

Collected with the virtual device or real ESP32 wristband. Covers activities **no public dataset captures** for the Indian elderly context:

- Sitting cross-legged on floor (sukhasana / padmasana)
- Namaste / prayer poses (hands together at chest)
- Getting up from floor sitting
- Squat-toilet posture (deep squat + standing back up)
- Intentional wrist motions: eating with hand, waving, brushing teeth, opening doors

**Format:** CSV, normalized to the same schema as WEDA-FALL's `*_accel.csv` / `*_gyro.csv` so the loader sees a unified interface. Sample rate target: **50 Hz** (to match WEDA-FALL natively).

**Target collection:** ~60–100 minutes across these activities, multiple subjects (yourself + family / friends).

**Location:** `ml/data/raw/indian_adl/<subject_id>/<activity>_<session>_<sensor>.csv`

---

## Pre-impact label re-derivation methodology

WEDA-FALL's `fall_timestamps.csv` gives the **start/end of each fall sequence** but not the exact impact instant — which we need for pre-impact prediction. Our re-derivation algorithm:

1. Resample each fall recording's accelerometer to uniform 50 Hz (linear interpolation over non-uniform Fitbit timestamps).
2. Within the manually-labeled fall window `[start_time, end_time]`, compute the acceleration magnitude `|a|(t) = √(ax² + ay² + az²)`.
3. **`t_impact = argmax_t |a|(t)`** within the window — the body-to-ground (or chair) collision peak.
4. Sanity check: `peak |a| ≥ 20 m/s²` (~2g, conservative threshold).
5. Define temporal phase labels around `t_impact`:
   - `PRE_IMPACT`: `[t_impact − 500 ms, t_impact − 50 ms]` — the prediction target
   - `IMPACT`: `[t_impact − 50 ms, t_impact + 500 ms]`
   - `POST_IMPACT`: `[t_impact + 500 ms, end_time]`
   - `BACKGROUND`: everything outside the fall window (or all of any ADL recording)
6. **Validation against ground truth:** compute the lag `t_impact − start_time` across all fall recordings — expected distribution: 0.5–1.5 s (since `start_time` is roughly the pre-fall onset). We publish the histogram in the EDA notebook and flag outliers.

Reference implementation: [`src/fall_guardian_ml/datasets/pre_impact_labels.py`](src/fall_guardian_ml/datasets/pre_impact_labels.py).

Tests for the algorithm (synthetic + verified-against-ground-truth): [`tests/test_pre_impact_labels.py`](tests/test_pre_impact_labels.py).

---

## Verifying downloads

After downloading WEDA-FALL (minimum required to start), run:

```bash
cd ml
uv run fg-data verify
```

This script checks directory structure + file counts and prints what's present / missing.

## Citing the datasets

```bibtex
@article{weda_fall_2024,
  title={Wrist-Based Fall Detection: Towards Generalization across Datasets},
  author={Marques, Jo{\~a}o J. T. and others},
  journal={Sensors},
  volume={24},
  number={5},
  pages={1679},
  year={2024}
}

@article{smartfall_2018,
  title={SmartFall: A Smartwatch-Based Fall Detection System Using Deep Learning},
  author={Mauldin, Taylor and Canby, Marc and Metsis, Vangelis and Ngu, Anne and Rivera, Coralys},
  journal={Sensors},
  volume={18},
  number={10},
  pages={3363},
  year={2018}
}

@article{upfall_2019,
  title={UP-Fall Detection Dataset: A Multimodal Approach},
  author={Mart{\'\i}nez-Villase{\~n}or, Lourdes and others},
  journal={Sensors},
  volume={19},
  number={9},
  pages={1988},
  year={2019}
}
```
