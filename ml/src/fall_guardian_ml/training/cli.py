"""`fg-train` CLI — entry point for the training + edge-export workflow.

    fg-train edge                       # train ConvLSTM-tiny on WEDA-FALL
    fg-train edge --synthetic           # smoke-test the pipeline end-to-end
    fg-train quantize                   # PTQ the trained checkpoint → INT8 .tflite
    fg-train benchmark <model.tflite>   # size + latency of an INT8 model
    fg-train edge-pipeline --synthetic  # train → quantize → benchmark in one go

Heavy imports (torch, mlflow, tensorflow) are deferred into the command bodies
so `fg-train --help` stays instant and a missing optional dep only bites the
command that actually needs it.
"""
from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(help="Fall Guardian — training & edge-export CLI", no_args_is_help=True)


@app.command()
def edge(
    synthetic: bool = typer.Option(False, help="Use synthetic smoke-test data."),
    epochs: int = typer.Option(40),
    batch_size: int = typer.Option(128),
    lr: float = typer.Option(1e-3),
    seed: int = typer.Option(42),
    pos_weight_scale: float = typer.Option(1.0, help="Extra recall bias on BCE pos_weight."),
    max_fpr_adl: float = typer.Option(0.05, help="FPR-on-ADL cap; recall maximised under it."),
    dataset_root: Path | None = typer.Option(None, help="WEDA-FALL-main dir."),
) -> None:
    """Train the ConvLSTM-tiny edge prediction model."""
    from fall_guardian_ml.training.train_edge import (
        DEFAULT_DATASET_ROOT,
        TrainConfig,
        run_training,
    )

    cfg = TrainConfig(
        epochs=epochs, batch_size=batch_size, lr=lr, seed=seed,
        pos_weight_scale=pos_weight_scale, max_fpr_adl=max_fpr_adl, synthetic=synthetic,
    )
    run_training(cfg, dataset_root=dataset_root or DEFAULT_DATASET_ROOT)


@app.command()
def quantize(
    out: Path = typer.Option(Path("artifacts/edge/convlstm_tiny_int8.tflite")),
    n_calibration: int = typer.Option(200),
    synthetic: bool = typer.Option(False, help="Calibrate on synthetic windows."),
) -> None:
    """Post-training-quantize the trained FP32 checkpoint to an INT8 .tflite."""
    from fall_guardian_ml.datasets.edge_dataset import (
        build_edge_bundle,
        make_synthetic_bundle,
    )
    from fall_guardian_ml.eval.quantize import quantize_to_int8_tflite
    from fall_guardian_ml.training.train_edge import (
        DEFAULT_ARTIFACT_DIR,
        DEFAULT_DATASET_ROOT,
    )

    bundle = make_synthetic_bundle() if synthetic else build_edge_bundle(DEFAULT_DATASET_ROOT)
    res = quantize_to_int8_tflite(
        ckpt_path=DEFAULT_ARTIFACT_DIR / "convlstm_tiny_fp32.pt",
        channel_stats_path=DEFAULT_ARTIFACT_DIR / "channel_stats.json",
        representative_X=bundle.X,
        out_path=out,
        n_calibration=n_calibration,
    )
    typer.echo(f"INT8 .tflite written: {res.tflite_path}  ({res.size_kb:.1f} KB)")


@app.command()
def benchmark(model: Path, n_runs: int = typer.Option(200)) -> None:
    """Report size + latency for an INT8 .tflite model."""
    from fall_guardian_ml.eval.benchmark import benchmark_tflite, print_benchmark

    print_benchmark(benchmark_tflite(model, n_runs=n_runs))


@app.command("export-onnx")
def export_onnx(n_runs: int = typer.Option(200)) -> None:
    """Windows-runnable edge export: ONNX + INT8 cross-check (size + latency).

    The deployable .tflite needs the Linux-only ai-edge stack; this is the part
    of edge export that runs on Windows and confirms the INT8 footprint.
    """
    from fall_guardian_ml.eval.onnx_export import print_onnx_crosscheck, run_onnx_crosscheck
    from fall_guardian_ml.training.train_edge import DEFAULT_ARTIFACT_DIR

    res = run_onnx_crosscheck(
        ckpt_path=DEFAULT_ARTIFACT_DIR / "convlstm_tiny_fp32.pt",
        artifact_dir=DEFAULT_ARTIFACT_DIR,
        n_runs=n_runs,
    )
    print_onnx_crosscheck(res)


@app.command("edge-pipeline")
def edge_pipeline(
    synthetic: bool = typer.Option(False),
    epochs: int = typer.Option(40),
) -> None:
    """Train → quantize → benchmark in one command (the full Week-B run)."""
    from fall_guardian_ml.datasets.edge_dataset import (
        build_edge_bundle,
        make_synthetic_bundle,
    )
    from fall_guardian_ml.eval.benchmark import benchmark_tflite, print_benchmark
    from fall_guardian_ml.eval.quantize import quantize_to_int8_tflite
    from fall_guardian_ml.training.train_edge import (
        DEFAULT_ARTIFACT_DIR,
        DEFAULT_DATASET_ROOT,
        TrainConfig,
        run_training,
    )

    cfg = TrainConfig(epochs=epochs, synthetic=synthetic)
    run_training(cfg, dataset_root=DEFAULT_DATASET_ROOT)

    bundle = make_synthetic_bundle() if synthetic else build_edge_bundle(DEFAULT_DATASET_ROOT)
    tflite_out = DEFAULT_ARTIFACT_DIR / "convlstm_tiny_int8.tflite"
    quantize_to_int8_tflite(
        ckpt_path=DEFAULT_ARTIFACT_DIR / "convlstm_tiny_fp32.pt",
        channel_stats_path=DEFAULT_ARTIFACT_DIR / "channel_stats.json",
        representative_X=bundle.X,
        out_path=tflite_out,
    )
    print_benchmark(benchmark_tflite(tflite_out))


if __name__ == "__main__":
    app()
