"""fg-data — CLI for Fall Guardian dataset operations.

Exposed via the `fg-data` console script (declared in ml/pyproject.toml).
First-class subcommand: `verify` — checks that WEDA-FALL is laid out on disk
the way the loader + label-derivation code expects, before any training run
spends 20 minutes failing on a path issue.

Usage:
    uv run fg-data verify                        # default: ml/data/raw/WEDA-FALL-main
    uv run fg-data verify --weda-root <path>     # custom location
    uv run fg-data verify --rate 25              # check a non-default sample-rate folder
"""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from fall_guardian_ml.datasets.weda_fall import (
    ADL_CODES,
    ELDER_SUBJECTS,
    FALL_CODES,
    TARGET_HZ,
    YOUNG_SUBJECTS,
    discover_recordings,
    load_fall_timestamps,
)

app = typer.Typer(
    help="Fall Guardian dataset utilities.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@app.callback()
def _main() -> None:
    """Fall Guardian dataset utilities. Use a subcommand (e.g. `fg-data verify`)."""
    # No-op callback. Its presence forces Typer to expose subcommands rather
    # than collapsing a single-command app into top-level args.


# ─── helpers ─────────────────────────────────────────────────────────────────

def _default_data_root() -> Path:
    """Default ml/data/raw root, derived from this file's location.

    This file lives at:
        ml/src/fall_guardian_ml/datasets/cli.py
    so ml/ is parents[3] and data/raw/ sits below it.
    """
    here = Path(__file__).resolve()
    ml_root = here.parents[3]
    return ml_root / "data" / "raw"


def _ok(msg: str) -> None:
    console.print(f"[green][OK][/green] {msg}")


def _fail(msg: str) -> None:
    console.print(f"[red][FAIL][/red] {msg}")


def _warn(msg: str) -> None:
    console.print(f"[yellow][WARN][/yellow] {msg}")


# ─── verify ─────────────────────────────────────────────────────────────────

@app.command()
def verify(
    weda_root: Path = typer.Option(
        None,
        "--weda-root",
        help="Path to the extracted WEDA-FALL-main folder. "
             "Defaults to ml/data/raw/WEDA-FALL-main.",
    ),
    sample_rate: int = typer.Option(
        TARGET_HZ,
        "--rate",
        help=f"Sample-rate sub-folder to verify (default {TARGET_HZ} Hz).",
    ),
    sample_files: int = typer.Option(
        5,
        "--sample-files",
        help="How many recordings to spot-check for sensor-file completeness.",
    ),
) -> None:
    """Verify the WEDA-FALL dataset layout matches what the loader expects."""
    if weda_root is None:
        weda_root = _default_data_root() / "WEDA-FALL-main"

    console.rule("[bold]Fall Guardian -- WEDA-FALL verification")
    console.print(f"[bold]Looking in:[/bold] {weda_root}")
    console.print()

    issues: list[str] = []

    # 1. Root exists -------------------------------------------------------
    if not weda_root.exists():
        _fail(f"WEDA-FALL-main folder not found at {weda_root}")
        console.print(
            "\n[yellow]Download from:[/yellow] "
            "https://github.com/joaojtmarques/WEDA-FALL\n"
            "[yellow]Extract into:[/yellow] ml/data/raw/"
        )
        raise typer.Exit(code=1)
    _ok(f"Root folder exists")

    # 2. Sample-rate folder exists -----------------------------------------
    rate_dir = weda_root / "dataset" / f"{sample_rate}Hz"
    if not rate_dir.exists():
        _fail(f"{sample_rate}Hz folder missing at {rate_dir}")
        raise typer.Exit(code=1)
    _ok(f"{sample_rate}Hz folder exists")

    # 3. fall_timestamps.csv exists + parses --------------------------------
    fts_path = weda_root / "dataset" / "fall_timestamps.csv"
    if not fts_path.exists():
        _fail(f"fall_timestamps.csv missing at {fts_path}")
        issues.append("fall_timestamps.csv missing")
    else:
        try:
            fts = load_fall_timestamps(weda_root)
            _ok(f"fall_timestamps.csv loaded ({len(fts)} fall labels)")
            required_cols = {"filename", "start_time", "end_time"}
            missing_cols = required_cols - set(fts.columns)
            if missing_cols:
                _fail(f"fall_timestamps.csv missing columns: {missing_cols}")
                issues.append(f"fall_timestamps.csv missing columns: {missing_cols}")
        except Exception as exc:
            _fail(f"fall_timestamps.csv failed to load: {exc}")
            issues.append(f"fall_timestamps.csv parse error: {exc}")

    # 4. Movement folders + per-code recording counts -----------------------
    console.print()
    table = Table(title=f"Recordings per movement at {sample_rate} Hz")
    table.add_column("Code", style="cyan", no_wrap=True)
    table.add_column("Type", style="magenta", no_wrap=True)
    table.add_column("Recordings", justify="right")
    table.add_column("Young", justify="right")
    table.add_column("Elder", justify="right")

    total_recordings = 0
    total_fall = 0
    total_adl = 0

    for code in ADL_CODES + FALL_CODES:
        movement_dir = rate_dir / code
        if not movement_dir.exists():
            table.add_row(code, "?", "[red]missing[/red]", "-", "-")
            issues.append(f"{code} folder missing")
            continue
        recs = discover_recordings(weda_root, sample_rate=sample_rate, movements=[code])
        young_count = sum(1 for r in recs if r.user_id in YOUNG_SUBJECTS)
        elder_count = sum(1 for r in recs if r.user_id in ELDER_SUBJECTS)
        type_str = "Fall" if code.startswith("F") else "ADL"
        table.add_row(code, type_str, str(len(recs)), str(young_count), str(elder_count))
        total_recordings += len(recs)
        if code.startswith("F"):
            total_fall += len(recs)
        else:
            total_adl += len(recs)

    console.print(table)
    console.print()
    console.print(f"[bold]Totals:[/bold] {total_recordings} recordings = "
                  f"{total_fall} falls + {total_adl} ADL")

    # 5. Spot-check sensor-file completeness on a sample --------------------
    sample_recs = discover_recordings(weda_root, sample_rate=sample_rate)[:sample_files]
    if sample_recs:
        console.print()
        sample_table = Table(title=f"Sensor-file completeness (first {len(sample_recs)})")
        sample_table.add_column("Recording", style="cyan", no_wrap=True)
        sample_table.add_column("accel", justify="center")
        sample_table.add_column("gyro", justify="center")
        sample_table.add_column("orientation", justify="center")
        sample_table.add_column("vertical_accel", justify="center")

        for rec in sample_recs:
            base = rate_dir / rec.movement
            stem = rec.filename_stem
            marks = []
            for sensor in ("accel", "gyro", "orientation", "vertical_accel"):
                exists = (base / f"{stem}_{sensor}.csv").exists()
                marks.append("[green]OK[/green]" if exists else "[red]MISSING[/red]")
                if not exists:
                    issues.append(f"{rec.label_key}: missing {sensor}.csv")
            sample_table.add_row(rec.label_key, *marks)
        console.print(sample_table)

    # 6. Subject coverage check -------------------------------------------
    all_recs = discover_recordings(weda_root, sample_rate=sample_rate)
    seen_users = {r.user_id for r in all_recs}
    young_seen = seen_users & set(YOUNG_SUBJECTS)
    elder_seen = seen_users & set(ELDER_SUBJECTS)
    console.print()
    console.print(f"[bold]Subject coverage:[/bold] "
                  f"young {len(young_seen)}/{len(YOUNG_SUBJECTS)}, "
                  f"elder {len(elder_seen)}/{len(ELDER_SUBJECTS)}")

    # Final report ---------------------------------------------------------
    console.print()
    if issues:
        console.rule("[red bold]Verification FAILED")
        console.print(f"[red bold]{len(issues)} issue(s):[/red bold]")
        for i in issues:
            console.print(f"  - {i}")
        raise typer.Exit(code=1)

    console.rule("[green bold]Verification PASSED")
    console.print(
        "[green]Dataset layout matches what the loader + label-derivation "
        "code expect. Safe to proceed with EDA + training.[/green]"
    )


if __name__ == "__main__":
    app()
