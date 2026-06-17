#!/usr/bin/env python3
"""Virtual device — replay WEDA-FALL windows to the cloud gateway.

Stands in for the real ESP32-S3 + MPU6050 wristband when no hardware is on the
bench. It reads a 2.5 s slice of recorded IMU data straight from the WEDA-FALL
CSVs under ``ml/data/raw/``, packs it into the exact §8 ingestion envelope the
firmware sends, and POSTs it to the FastAPI backend:

  * a **fall** window  → ``POST /v1/inference``   (payload_type=emergency) → the
    CloudDetector confirms/suppresses and, on a confirmed fall, persists an event;
  * a **canceled false alarm** → ``POST /v1/retraining`` (payload_type=
    retraining_data) → skips the detector, stored as labeled training data.

The window contract (125 samples = 2.5 s @ 50 Hz, accel m/s² + gyro rad/s) and the
two-path routing mirror docs/ARCHITECTURE.md §8 and ADR-011 exactly, so anything
this script can drive, the real watch can drive too.

Device auth (a per-device JWT) is resolved in this order:
  1. ``--device-token <jwt>``  — paste a token minted elsewhere;
  2. ``--pair``                — run the real handshake (register/login → mint an
                                 8-char pairing code → redeem it). Needs a DB-backed
                                 server;
  3. otherwise                 — mint a device token locally with the shared
                                 ``--jwt-secret`` (defaults to the dev secret). This
                                 is the DB-less dev shortcut: ``get_current_device``
                                 only decodes the JWT, so a bare ``uvicorn`` with no
                                 Postgres still accepts it.

Examples
--------
    # DB-less: start `uvicorn app.main:app` in backend/, then replay 5 falls + 5 ADLs
    python virtual_device.py --kind both --count 5

    # Send one canceled false alarm (an ADL window) to the retraining path
    python virtual_device.py --kind adl --count 1 --false-alarm

    # Real pairing handshake against a DB-backed server
    python virtual_device.py --pair --email me@example.com --password supersecret

    # Inspect the payloads without hitting the network
    python virtual_device.py --kind fall --count 2 --dry-run

    # Continuous-wear demo, paced for a 10-15 s screen recording: a few seconds of
    # silent on-wrist monitoring (nothing uploaded), then a fall trips one upload.
    python virtual_device.py --pair --email me@example.com --password supersecret --wear
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import requests

# ─── Window contract (must match schemas.WINDOW_SAMPLES / ARCHITECTURE §8) ────
WINDOW_SAMPLES = 125          # 2.5 s @ 50 Hz — the locked window length
SAMPLE_RATE_HZ = 50
WINDOW_S = WINDOW_SAMPLES / SAMPLE_RATE_HZ
# How much of the window sits *before* the impact instant for a fall replay, so
# the slice carries the pre-impact lead the edge model is trained to predict.
FALL_PRE_IMPACT_S = 1.0
# Display-only: the edge model "fires" (and uploads) above this probability. The
# real firmware threshold lives in the ESP32 build; here it just labels the wear
# narration so the silent-vs-trigger split reads clearly on screen.
EDGE_FIRE_THRESHOLD = 0.5

# WEDA-FALL lives under the repo's ml/ data tree; resolve relative to this file so
# the script is location-independent inside virtual_device/.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_WEDA_ROOT = _REPO_ROOT / "ml" / "data" / "raw" / "WEDA-FALL-main" / "dataset"
DEFAULT_DATA_DIR = _WEDA_ROOT / "50Hz"
FALL_TIMESTAMPS = _WEDA_ROOT / "fall_timestamps.csv"

# The config.py dev-only default secret — the app refuses to boot with it outside
# `local`, so it is safe to hard-default here for the DB-less dev shortcut.
DEV_JWT_SECRET = "dev-insecure-change-me-not-for-production-use"


# ─── Trial discovery ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Trial:
    """One recorded WEDA-FALL trial (an accel CSV + its sibling gyro CSV)."""

    activity: str        # e.g. "F01" (fall) or "D04" (ADL)
    prefix: str          # e.g. "U01_R01"
    accel_csv: Path
    gyro_csv: Path

    @property
    def is_fall(self) -> bool:
        return self.activity.startswith("F")

    @property
    def key(self) -> str:
        """The fall_timestamps.csv key, e.g. "F01/U01_R01"."""
        return f"{self.activity}/{self.prefix}"


def discover_trials(data_dir: Path, kind: str) -> list[Trial]:
    """Find every (accel, gyro) trial pair under ``data_dir`` for the chosen kind.

    `kind` is "fall" (F*), "adl" (D*), or "both".
    """
    if not data_dir.is_dir():
        raise SystemExit(
            f"WEDA-FALL data not found at {data_dir}\n"
            "Download it per ml/DATA.md and extract into ml/data/raw/, or pass --data-dir."
        )
    trials: list[Trial] = []
    for activity_dir in sorted(p for p in data_dir.iterdir() if p.is_dir()):
        activity = activity_dir.name
        if kind == "fall" and not activity.startswith("F"):
            continue
        if kind == "adl" and not activity.startswith("D"):
            continue
        for accel_csv in sorted(activity_dir.glob("*_accel.csv")):
            prefix = accel_csv.name[: -len("_accel.csv")]
            gyro_csv = activity_dir / f"{prefix}_gyro.csv"
            if gyro_csv.exists():
                trials.append(Trial(activity, prefix, accel_csv, gyro_csv))
    return trials


# ─── CSV → uniform 50 Hz window ───────────────────────────────────────────────


def _read_sensor_csv(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Read a WEDA ``*_accel.csv`` / ``*_gyro.csv`` into (times, xyz).

    Columns are ``<sensor>_time_list, <sensor>_x_list, _y_list, _z_list``. The time
    column is non-uniform (Bluetooth-batched delivery from the Fitbit Sense), which
    is exactly why the caller resamples onto a true 50 Hz grid.
    """
    times: list[float] = []
    xyz: list[tuple[float, float, float]] = []
    with path.open(newline="") as fh:
        reader = csv.reader(fh)
        next(reader, None)  # header
        for row in reader:
            if len(row) < 4:
                continue
            times.append(float(row[0]))
            xyz.append((float(row[1]), float(row[2]), float(row[3])))
    if not times:
        raise ValueError(f"no samples in {path}")
    return np.asarray(times, dtype=np.float64), np.asarray(xyz, dtype=np.float64)


def _load_fall_timestamps() -> dict[str, tuple[float, float]]:
    """Map "F01/U01_R01" → (start_time, end_time) from fall_timestamps.csv."""
    if not FALL_TIMESTAMPS.exists():
        return {}
    out: dict[str, tuple[float, float]] = {}
    with FALL_TIMESTAMPS.open(newline="") as fh:
        reader = csv.reader(fh)
        next(reader, None)  # header: filename,start_time,end_time
        for row in reader:
            if len(row) >= 3:
                out[row[0]] = (float(row[1]), float(row[2]))
    return out


def _resample(times: np.ndarray, values: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """Linearly interpolate one channel onto a uniform time grid (np.interp clamps
    outside the recorded span, so a window never runs off the end of a trial)."""
    return np.interp(grid, times, values)


def build_window(
    trial: Trial, fall_ts: dict[str, tuple[float, float]]
) -> list[dict[str, float]]:
    """Return exactly 125 samples (2.5 s @ 50 Hz) for this trial.

    Falls are centered on the impact instant (argmax |a| inside the labeled fall
    window) so the slice carries ~1 s of pre-impact lead; ADLs are taken from the
    middle of the recording.
    """
    a_t, a_xyz = _read_sensor_csv(trial.accel_csv)
    g_t, g_xyz = _read_sensor_csv(trial.gyro_csv)
    total_s = float(min(a_t[-1], g_t[-1]))

    if trial.is_fall and trial.key in fall_ts:
        start, end = fall_ts[trial.key]
        # Find the impact peak within the labeled fall window on a dense grid.
        dense = np.arange(start, min(end, total_s), 1.0 / SAMPLE_RATE_HZ)
        if dense.size:
            mag = np.sqrt(
                _resample(a_t, a_xyz[:, 0], dense) ** 2
                + _resample(a_t, a_xyz[:, 1], dense) ** 2
                + _resample(a_t, a_xyz[:, 2], dense) ** 2
            )
            impact_t = float(dense[int(np.argmax(mag))])
        else:
            impact_t = start
        window_start = impact_t - FALL_PRE_IMPACT_S
    else:
        window_start = total_s / 2.0 - WINDOW_S / 2.0

    # Clamp so the 2.5 s window stays inside the recording.
    window_start = max(0.0, min(window_start, max(0.0, total_s - WINDOW_S)))
    grid = window_start + np.arange(WINDOW_SAMPLES) / SAMPLE_RATE_HZ

    ax, ay, az = (_resample(a_t, a_xyz[:, i], grid) for i in range(3))
    wx, wy, wz = (_resample(g_t, g_xyz[:, i], grid) for i in range(3))
    return [
        {
            "ax": round(float(ax[i]), 4),
            "ay": round(float(ay[i]), 4),
            "az": round(float(az[i]), 4),
            "wx": round(float(wx[i]), 4),
            "wy": round(float(wy[i]), 4),
            "wz": round(float(wz[i]), 4),
        }
        for i in range(WINDOW_SAMPLES)
    ]


def build_envelope(
    device_id: str,
    samples: list[dict[str, float]],
    *,
    false_alarm: bool,
    edge_prob: float | None,
) -> dict:
    """Assemble the §8 WindowEnvelope JSON the firmware and this device share."""
    envelope: dict = {
        "device_id": device_id,
        "ts_start_unix_ms": int(time.time() * 1000),
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "samples": samples,
        "payload_type": "retraining_data" if false_alarm else "emergency",
    }
    # On a real watch an emergency upload only happens *because* the edge fired, so
    # we attach the trigger that caused it. A canceled false alarm carries none.
    if not false_alarm and edge_prob is not None:
        envelope["edge_prediction"] = {
            "p_pre_impact": round(edge_prob, 4),
            "model_version": "edge-sim-0.1",
        }
    return envelope


# ─── Device authentication ────────────────────────────────────────────────────


def mint_device_token(secret: str, algorithm: str, device_id: str, ttl_days: int) -> str:
    """Locally sign a per-device JWT (the DB-less dev shortcut).

    Mirrors auth.create_device_token's claims so ``get_current_device`` accepts it.
    Uses fresh random ids for the device PK and owning user — fine for a DB-less
    server that only decodes the token and never looks them up.
    """
    try:
        import jwt  # PyJWT — only needed for this path
    except ImportError:  # pragma: no cover - environment guard
        raise SystemExit(
            "Local token minting needs PyJWT (pip install PyJWT), or pass "
            "--device-token / --pair instead."
        ) from None
    from datetime import datetime, timedelta, timezone

    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": str(uuid.uuid4()),       # device_pk
        "typ": "device",
        "did": device_id,
        "uid": str(uuid.uuid4()),       # owning user
        "iat": now,
        "exp": now + timedelta(days=ttl_days),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def pair_device(
    session: requests.Session, base_url: str, email: str, password: str, device_id: str, timeout: float
) -> str:
    """Run the real handshake: register-or-login → pairing code → redeem → token."""
    # 1. Register (or fall back to login if the account already exists).
    resp = session.post(
        f"{base_url}/v1/auth/register",
        json={"email": email, "password": password},
        timeout=timeout,
    )
    if resp.status_code == 409:
        resp = session.post(
            f"{base_url}/v1/auth/login",
            json={"email": email, "password": password},
            timeout=timeout,
        )
    resp.raise_for_status()
    access_token = resp.json()["access_token"]
    auth = {"Authorization": f"Bearer {access_token}"}

    # 2. The paired user mints a short-lived 8-char pairing code.
    resp = session.post(f"{base_url}/v1/devices/pairing-codes", headers=auth, timeout=timeout)
    resp.raise_for_status()
    code = resp.json()["code"]

    # 3. The device redeems the code (no auth — the code IS the credential).
    resp = session.post(
        f"{base_url}/v1/devices/pair",
        json={"code": code, "device_id": device_id},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["device_token"]


def resolve_device_token(args: argparse.Namespace, session: requests.Session) -> str:
    if args.device_token:
        return args.device_token
    if args.pair:
        if not (args.email and args.password):
            raise SystemExit("--pair needs --email and --password")
        print(f"Pairing device '{args.device_id}' against {args.base_url} …")
        token = pair_device(
            session, args.base_url, args.email, args.password, args.device_id, args.timeout
        )
        print("  paired — received a device token")
        return token
    print(
        "No --device-token/--pair given: minting a device token locally with the "
        "dev JWT secret (DB-less mode)."
    )
    return mint_device_token(args.jwt_secret, "HS256", args.device_id, args.token_ttl_days)


# ─── Sending ──────────────────────────────────────────────────────────────────


def send_window(
    session: requests.Session,
    base_url: str,
    token: str,
    envelope: dict,
    *,
    false_alarm: bool,
    timeout: float,
) -> tuple[int, dict | str]:
    path = "/v1/retraining" if false_alarm else "/v1/inference"
    resp = session.post(
        f"{base_url}{path}",
        json=envelope,
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    )
    try:
        body: dict | str = resp.json()
    except ValueError:
        body = resp.text
    return resp.status_code, body


def _summarize(trial: Trial, status: int, body: dict | str, false_alarm: bool) -> str:
    tag = f"[{trial.activity}/{trial.prefix}]"
    if not isinstance(body, dict):
        return f"{tag} HTTP {status}: {body}"
    if false_alarm:
        return f"{tag} HTTP {status} stored={body.get('stored')} label={body.get('label')}"
    return (
        f"{tag} HTTP {status} is_fall={body.get('is_fall')} "
        f"confidence={body.get('confidence')} severity={body.get('severity')} "
        f"action={body.get('action')}"
    )


# ─── Continuous-wear demo ─────────────────────────────────────────────────────


def run_wear(
    args: argparse.Namespace, session: requests.Session, fall_ts: dict
) -> int:
    """Continuous-wear DEMO: narrate the silent edge loop, then one real fall.

    This mirrors how the firmware behaves on the wrist. The watch runs the edge
    model locally on every window and stays **silent on normal motion**, opening a
    connection to the cloud ONLY when the edge model fires. So the monitoring lines
    below are local-only (nothing is uploaded); just the final fall window is
    POSTed — which is what trips the SSE/FCM alert to the phone.

    Paced for a 10–15 s screen recording: ``--wear-seconds`` of monitoring, then
    the fall. See docs/RUN.md §7.
    """
    adl_trials = discover_trials(args.data_dir, "adl")
    fall_trials = discover_trials(args.data_dir, "fall")
    if not adl_trials:
        raise SystemExit(f"wear mode needs ADL (D*) trials; none found under {args.data_dir}")
    if not fall_trials:
        raise SystemExit(f"wear mode needs fall (F*) trials; none found under {args.data_dir}")

    rng = random.Random(args.seed)
    token = None if args.dry_run else resolve_device_token(args, session)

    print(
        f"\n[wear] watch worn - edge model live @ {SAMPLE_RATE_HZ} Hz, "
        f"uploads only when p_pre_impact > {EDGE_FIRE_THRESHOLD:.2f}\n"
    )

    # Monitoring phase: normal daily motion, evaluated locally, nothing uploaded.
    start = time.monotonic()
    while (elapsed := time.monotonic() - start) < args.wear_seconds:
        trial = rng.choice(adl_trials)
        p = rng.uniform(0.01, 0.18)
        print(
            f"[wear] t={elapsed:4.1f}s  {trial.activity}/{trial.prefix:<10}  "
            f"edge p={p:4.2f}  -> normal motion, no upload"
        )
        time.sleep(1.0)

    # Trigger: a fall fires the edge model -> the one emergency upload.
    trial = rng.choice(fall_trials)
    samples = build_window(trial, fall_ts)
    envelope = build_envelope(
        args.device_id, samples, false_alarm=False, edge_prob=args.edge_prob
    )
    elapsed = time.monotonic() - start
    print(
        f"\n[wear] t={elapsed:4.1f}s  !! FALL IMMINENT  "
        f"{trial.activity}/{trial.prefix}  edge p={args.edge_prob:.2f}  "
        f"-> POST /v1/inference"
    )
    if args.dry_run:
        peak = max((s["ax"] ** 2 + s["ay"] ** 2 + s["az"] ** 2) ** 0.5 for s in samples)
        print(f"[wear] (dry-run) samples={len(samples)} peak|a|={peak:.1f} m/s^2 - not sent\n")
        return 0

    status, body = send_window(
        session, args.base_url, token, envelope, false_alarm=False, timeout=args.timeout
    )
    print(f"[wear] cloud {_summarize(trial, status, body, False)}")
    if status < 400 and isinstance(body, dict) and body.get("is_fall"):
        print("[wear] -> caregiver alerted over SSE/FCM\n")
    else:
        print("[wear] (no alert — see the response above)\n")
    return 0


# ─── CLI ──────────────────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Replay WEDA-FALL IMU windows to the Fall Guardian cloud gateway.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend base URL.")
    p.add_argument("--device-id", default="sim-watch-01", help="Logical device id (must match the token).")
    p.add_argument("--kind", choices=["fall", "adl", "both"], default="both", help="Which trials to replay.")
    p.add_argument("--count", type=int, default=1, help="How many windows to send.")
    p.add_argument(
        "--false-alarm",
        action="store_true",
        help="Route windows to /v1/retraining as canceled false alarms (no detection).",
    )
    p.add_argument("--edge-prob", type=float, default=0.9, help="Synthetic edge p_pre_impact on emergency uploads.")
    p.add_argument("--interval", type=float, default=1.0, help="Seconds to wait between sends.")
    p.add_argument(
        "--wear",
        action="store_true",
        help="Continuous-wear DEMO: narrate the silent on-wrist edge loop, then trigger "
             "one real fall upload. Paced for a 10-15 s screen recording (docs/RUN.md §7).",
    )
    p.add_argument(
        "--wear-seconds",
        type=float,
        default=8.0,
        help="Seconds of normal-motion monitoring before the fall triggers (wear mode).",
    )
    p.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="WEDA-FALL 50Hz dataset dir.")
    p.add_argument("--seed", type=int, default=None, help="Seed the trial picker for reproducible runs.")
    p.add_argument("--timeout", type=float, default=15.0, help="HTTP timeout (seconds).")
    p.add_argument("--dry-run", action="store_true", help="Build + print payload summaries; do not POST.")
    p.add_argument("--list", action="store_true", help="List discovered trials and exit.")

    auth = p.add_argument_group("device auth (pick at most one; default = local mint)")
    auth.add_argument("--device-token", help="Use this per-device JWT directly.")
    auth.add_argument("--pair", action="store_true", help="Run the real pairing handshake (needs a DB-backed server).")
    auth.add_argument("--email", help="Account email for --pair.")
    auth.add_argument("--password", help="Account password for --pair.")
    auth.add_argument("--jwt-secret", default=DEV_JWT_SECRET, help="Secret for the local-mint shortcut.")
    auth.add_argument("--token-ttl-days", type=int, default=365, help="Locally-minted device-token lifetime.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.wear:
        return run_wear(args, requests.Session(), _load_fall_timestamps())

    trials = discover_trials(args.data_dir, args.kind)
    if not trials:
        raise SystemExit(f"no '{args.kind}' trials found under {args.data_dir}")

    if args.list:
        print(f"{len(trials)} trials under {args.data_dir}:")
        for t in trials:
            print(f"  {t.activity}/{t.prefix}  ({'fall' if t.is_fall else 'adl'})")
        return 0

    rng = random.Random(args.seed)
    picks = [rng.choice(trials) for _ in range(args.count)]
    fall_ts = _load_fall_timestamps()

    session = requests.Session()
    token = None if (args.dry_run) else resolve_device_token(args, session)

    sent = ok = 0
    for i, trial in enumerate(picks, 1):
        samples = build_window(trial, fall_ts)
        envelope = build_envelope(
            args.device_id,
            samples,
            false_alarm=args.false_alarm,
            edge_prob=args.edge_prob,
        )
        if args.dry_run:
            peak = max(
                (s["ax"] ** 2 + s["ay"] ** 2 + s["az"] ** 2) ** 0.5 for s in samples
            )
            print(
                f"[{trial.activity}/{trial.prefix}] payload_type={envelope['payload_type']} "
                f"samples={len(samples)} peak|a|={peak:.1f} m/s² "
                f"edge={'edge_prediction' in envelope}"
            )
            continue

        status, body = send_window(
            session, args.base_url, token, envelope,
            false_alarm=args.false_alarm, timeout=args.timeout,
        )
        sent += 1
        if status < 400:
            ok += 1
        print(f"{i}/{args.count} {_summarize(trial, status, body, args.false_alarm)}")
        if i < args.count and args.interval > 0:
            time.sleep(args.interval)

    if not args.dry_run:
        print(f"\nDone: {ok}/{sent} accepted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
