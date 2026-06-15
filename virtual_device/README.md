# virtual_device

A software stand-in for the ESP32-S3 + MPU6050 wristband. It replays recorded
WEDA-FALL trials as **uniform 50 Hz** IMU windows to the cloud gateway exactly the
way the real firmware would — same 2.5 s × 50 Hz (125-sample) window, same §8
ingestion envelope, same two-path routing — so the whole gateway → SSE/FCM → phone
path can be exercised **end-to-end with no hardware** on the bench. See
[`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) §2.6 and §8, and ADR-011.

## What it does

1. Reads a trial's `*_accel.csv` + `*_gyro.csv` straight from
   `ml/data/raw/WEDA-FALL-main/dataset/50Hz/` (download per
   [`../ml/DATA.md`](../ml/DATA.md)).
2. Resamples the non-uniform Fitbit timestamps onto a true 50 Hz grid and slices
   one 125-sample window — **falls** centered on the impact instant (so the slice
   carries ~1 s of pre-impact lead), **ADLs** taken from the middle of the
   recording.
3. Packs it into the `WindowEnvelope` JSON and POSTs it:
   - default → `POST /v1/inference` (`payload_type=emergency`) → the CloudDetector
     confirms/suppresses;
   - `--false-alarm` → `POST /v1/retraining` (`payload_type=retraining_data`) → a
     canceled false alarm, stored as labeled training data, never detected.

## Setup

```bash
cd virtual_device
pip install -r requirements.txt
```

## Run it

Start the backend first (DB-less is fine — see [`../backend/README.md`](../backend/README.md)):

```bash
cd ../backend && uv run uvicorn app.main:app   # http://127.0.0.1:8000
```

Then drive it:

```bash
# Replay 5 falls + 5 ADLs (10 windows). With no DB the stub detector confirms on
# peak |a| ≥ 20 m/s², so falls alert and most ADLs suppress.
python virtual_device.py --kind both --count 10

# One canceled false alarm (an ADL window) → the retraining path
python virtual_device.py --kind adl --count 1 --false-alarm

# Build + inspect payloads without touching the network
python virtual_device.py --kind fall --count 3 --dry-run

# List the trials the dataset exposes, then exit
python virtual_device.py --kind fall --list
```

## Device authentication

`/v1/inference` and `/v1/retraining` require a **per-device JWT**. Resolution order:

| Flag | Mode | Needs a DB? |
| --- | --- | --- |
| `--device-token <jwt>` | Use a token minted elsewhere | — |
| `--pair --email … --password …` | Real handshake: register/login → pairing code → redeem | **yes** |
| *(none)* | Local-mint with `--jwt-secret` (defaults to the dev secret) | no |

The local-mint default works against a bare `uvicorn` because
`get_current_device` only *decodes* the token — it never looks the ids up. For a
DB-backed server, use the real handshake:

```bash
python virtual_device.py --pair --email me@example.com --password supersecret
```

## Useful flags

| Flag | Default | Meaning |
| --- | --- | --- |
| `--base-url` | `http://127.0.0.1:8000` | Gateway URL |
| `--device-id` | `sim-watch-01` | Logical device id (must match the token) |
| `--kind` | `both` | `fall` / `adl` / `both` |
| `--count` | `1` | Number of windows to send |
| `--interval` | `1.0` | Seconds between sends |
| `--edge-prob` | `0.9` | Synthetic `edge_prediction.p_pre_impact` on emergency uploads |
| `--seed` | — | Seed the trial picker for reproducible runs |
| `--data-dir` | `…/50Hz` | Override the WEDA-FALL dataset dir |

> Note: the gyro columns are sent through unchanged from WEDA-FALL — the same
> values the model was trained on — so what this device streams matches the
> training distribution.
