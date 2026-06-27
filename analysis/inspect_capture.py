"""Offline analysis for ADS1299 EMG capture CSV files.

The analysis is deliberately lightweight and standard-library only. It is meant
to answer first-pass hardware questions after each capture:

- Did the host receive continuous frames?
- Is the selected channel saturated?
- How large is the 50 Hz / harmonic component?
- Does the scheduled contraction window increase the residual EMG-like energy?
"""

from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


DEFAULT_FS_HZ = 1000.0
DEFAULT_VREF = 4.5
DEFAULT_GAIN = 2.0
FULL_SCALE_POS = 8388607
FULL_SCALE_NEG = -8388608
DEFAULT_LINE_HARMONICS = (50, 100, 150, 200, 250)


@dataclass(frozen=True)
class ChannelCapture:
    path: Path
    channel: int
    time_s: list[float]
    frame_counter: list[int]
    dropped_frames_before: list[int]
    values: list[int]


@dataclass(frozen=True)
class SignalSummary:
    count: int
    duration_s: float
    counter_nonunit_count: int
    counter_missing_sum: int
    dropped_nonzero_count: int
    dropped_positive_sum: int
    minimum: int
    maximum: int
    median: float
    rms_about_mean: float
    p95_p5: float
    p99_p1: float
    saturation_count: int
    near_80pct_fullscale_count: int


@dataclass(frozen=True)
class BlockMetric:
    start_s: float
    raw_rms: float
    line_rms: float
    residual_rms: float
    residual_rectified_mean: float
    median: float
    p95_p5: float


@dataclass(frozen=True)
class EpochMetric:
    cycle: int
    label: str
    start_s: float
    end_s: float
    raw_rms: float
    line_rms: float
    residual_rms: float
    residual_rectified_mean: float
    median: float
    p95_p5: float


@dataclass(frozen=True)
class CaptureAnalysis:
    capture: ChannelCapture
    fs_hz: float
    gain: float
    vref: float
    lsb_uv: float
    summary: SignalSummary
    dominant_frequencies: list[tuple[int, float]]
    selected_frequency_powers: dict[int, float]
    block_metrics: list[BlockMetric]
    epoch_metrics: list[EpochMetric]
    rest_residual_mean: float | None
    contraction_residual_mean: float | None
    contraction_rest_ratio: float | None


def load_capture(path: str | Path, channel: int = 8) -> ChannelCapture:
    capture_path = Path(path)
    column = f"ch{channel}_code"
    time_s: list[float] = []
    frame_counter: list[int] = []
    dropped: list[int] = []
    values: list[int] = []

    with capture_path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None or column not in reader.fieldnames:
            raise ValueError(f"CSV does not contain column {column!r}")

        for row in reader:
            time_s.append(float(row["time_s"]))
            frame_counter.append(int(row["frame_counter"]))
            dropped.append(int(row["dropped_frames_before"]))
            values.append(int(row[column]))

    if not values:
        raise ValueError(f"CSV contains no samples: {capture_path}")

    return ChannelCapture(
        path=capture_path,
        channel=channel,
        time_s=time_s,
        frame_counter=frame_counter,
        dropped_frames_before=dropped,
        values=values,
    )


def analyze_capture(
    path: str | Path,
    channel: int = 8,
    fs_hz: float = DEFAULT_FS_HZ,
    gain: float = DEFAULT_GAIN,
    vref: float = DEFAULT_VREF,
    rest_s: float | None = None,
    contract_s: float | None = None,
    cycles: int | None = None,
    block_s: float = 1.0,
    line_harmonics: Sequence[int] = DEFAULT_LINE_HARMONICS,
) -> CaptureAnalysis:
    capture = load_capture(path, channel)
    lsb_uv = code_to_microvolts(1, vref=vref, gain=gain)
    summary = summarize_capture(capture)
    dominant = scan_dominant_frequencies(capture.values, fs_hz=fs_hz)
    selected = {
        freq: goertzel_power(capture.values[: min(len(capture.values), 32768)], freq, fs_hz)
        for freq in (10, 20, 50, 60, 100, 150, 200, 250)
    }
    block_metrics = compute_block_metrics(
        capture.time_s,
        capture.values,
        fs_hz=fs_hz,
        block_s=block_s,
        line_harmonics=line_harmonics,
    )
    epoch_metrics = compute_epoch_metrics(
        capture.values,
        fs_hz=fs_hz,
        rest_s=rest_s,
        contract_s=contract_s,
        cycles=cycles,
        line_harmonics=line_harmonics,
    )
    rest_values = [epoch.residual_rms for epoch in epoch_metrics if epoch.label == "rest"]
    contraction_values = [
        epoch.residual_rms for epoch in epoch_metrics if epoch.label == "contraction"
    ]
    rest_mean = mean(rest_values) if rest_values else None
    contraction_mean = mean(contraction_values) if contraction_values else None
    ratio = None
    if rest_mean is not None and contraction_mean is not None and rest_mean > 0:
        ratio = contraction_mean / rest_mean

    return CaptureAnalysis(
        capture=capture,
        fs_hz=fs_hz,
        gain=gain,
        vref=vref,
        lsb_uv=lsb_uv,
        summary=summary,
        dominant_frequencies=dominant,
        selected_frequency_powers=selected,
        block_metrics=block_metrics,
        epoch_metrics=epoch_metrics,
        rest_residual_mean=rest_mean,
        contraction_residual_mean=contraction_mean,
        contraction_rest_ratio=ratio,
    )


def summarize_capture(capture: ChannelCapture) -> SignalSummary:
    values = capture.values
    diffs = [
        capture.frame_counter[index + 1] - capture.frame_counter[index]
        for index in range(len(capture.frame_counter) - 1)
    ]
    return SignalSummary(
        count=len(values),
        duration_s=capture.time_s[-1] - capture.time_s[0],
        counter_nonunit_count=sum(1 for diff in diffs if diff != 1),
        counter_missing_sum=sum(max(diff - 1, 0) for diff in diffs),
        dropped_nonzero_count=sum(1 for value in capture.dropped_frames_before if value != 0),
        dropped_positive_sum=sum(value for value in capture.dropped_frames_before if value > 0),
        minimum=min(values),
        maximum=max(values),
        median=quantile(values, 50),
        rms_about_mean=rms_about_mean(values),
        p95_p5=quantile(values, 95) - quantile(values, 5),
        p99_p1=quantile(values, 99) - quantile(values, 1),
        saturation_count=sum(
            1 for value in values if value == FULL_SCALE_POS or value == FULL_SCALE_NEG
        ),
        near_80pct_fullscale_count=sum(
            1 for value in values if abs(value) >= 0.8 * FULL_SCALE_POS
        ),
    )


def compute_block_metrics(
    time_s: list[float],
    values: list[int],
    fs_hz: float,
    block_s: float,
    line_harmonics: Sequence[int],
) -> list[BlockMetric]:
    block_len = max(1, int(round(block_s * fs_hz)))
    metrics: list[BlockMetric] = []
    for start in range(0, len(values) - block_len + 1, block_len):
        segment = values[start : start + block_len]
        raw_rms, line_rms, residual_rms, rectified, median, p95_p5 = decompose_line_harmonics(
            segment,
            fs_hz=fs_hz,
            line_harmonics=line_harmonics,
        )
        metrics.append(
            BlockMetric(
                start_s=time_s[start],
                raw_rms=raw_rms,
                line_rms=line_rms,
                residual_rms=residual_rms,
                residual_rectified_mean=rectified,
                median=median,
                p95_p5=p95_p5,
            )
        )
    return metrics


def compute_epoch_metrics(
    values: list[int],
    fs_hz: float,
    rest_s: float | None,
    contract_s: float | None,
    cycles: int | None,
    line_harmonics: Sequence[int],
) -> list[EpochMetric]:
    if rest_s is None or contract_s is None:
        return []

    cycle_s = rest_s + contract_s
    if cycle_s <= 0:
        return []

    inferred_cycles = int((len(values) / fs_hz) // cycle_s)
    cycle_count = inferred_cycles if cycles is None else min(cycles, inferred_cycles)
    metrics: list[EpochMetric] = []
    for cycle in range(cycle_count):
        cycle_start = cycle * cycle_s
        windows = (
            ("rest", cycle_start, cycle_start + rest_s),
            ("contraction", cycle_start + rest_s, cycle_start + cycle_s),
        )
        for label, start_s, end_s in windows:
            start = int(round(start_s * fs_hz))
            end = int(round(end_s * fs_hz))
            if end > len(values) or end <= start:
                continue
            raw_rms, line_rms, residual_rms, rectified, median, p95_p5 = (
                decompose_line_harmonics(
                    values[start:end],
                    fs_hz=fs_hz,
                    line_harmonics=line_harmonics,
                )
            )
            metrics.append(
                EpochMetric(
                    cycle=cycle + 1,
                    label=label,
                    start_s=start_s,
                    end_s=end_s,
                    raw_rms=raw_rms,
                    line_rms=line_rms,
                    residual_rms=residual_rms,
                    residual_rectified_mean=rectified,
                    median=median,
                    p95_p5=p95_p5,
                )
            )
    return metrics


def decompose_line_harmonics(
    values: list[int],
    fs_hz: float,
    line_harmonics: Sequence[int],
) -> tuple[float, float, float, float, float, float]:
    count = len(values)
    baseline = mean(values)
    residual = [value - baseline for value in values]
    removed = [0.0 for _ in values]

    for frequency in line_harmonics:
        cos_amp = 0.0
        sin_amp = 0.0
        for index, value in enumerate(residual):
            angle = 2.0 * math.pi * frequency * index / fs_hz
            cos_amp += value * math.cos(angle)
            sin_amp += value * math.sin(angle)
        cos_amp *= 2.0 / count
        sin_amp *= 2.0 / count

        for index in range(count):
            angle = 2.0 * math.pi * frequency * index / fs_hz
            component = cos_amp * math.cos(angle) + sin_amp * math.sin(angle)
            removed[index] += component
            residual[index] -= component

    raw_rms = math.sqrt(sum((value - baseline) ** 2 for value in values) / count)
    line_rms = math.sqrt(sum(value * value for value in removed) / count)
    residual_rms = math.sqrt(sum(value * value for value in residual) / count)
    rectified = sum(abs(value) for value in residual) / count
    return (
        raw_rms,
        line_rms,
        residual_rms,
        rectified,
        quantile(values, 50),
        quantile(values, 95) - quantile(values, 5),
    )


def scan_dominant_frequencies(
    values: list[int],
    fs_hz: float,
    max_samples: int = 32768,
    min_freq: int = 1,
    max_freq: int = 250,
    top_n: int = 12,
) -> list[tuple[int, float]]:
    segment = values[: min(len(values), max_samples)]
    powers = [
        (frequency, goertzel_power(segment, frequency, fs_hz))
        for frequency in range(min_freq, max_freq + 1)
    ]
    return sorted(powers, key=lambda item: item[1], reverse=True)[:top_n]


def goertzel_power(values: list[int], frequency: float, fs_hz: float) -> float:
    count = len(values)
    if count == 0:
        return float("nan")

    baseline = quantile(values, 50)
    k = round(count * frequency / fs_hz)
    omega = 2.0 * math.pi * k / count
    coefficient = 2.0 * math.cos(omega)
    s0 = s1 = s2 = 0.0
    for value in values:
        s0 = (value - baseline) + coefficient * s1 - s2
        s2 = s1
        s1 = s0
    return max(0.0, s1 * s1 + s2 * s2 - coefficient * s1 * s2)


def format_report(result: CaptureAnalysis) -> str:
    summary = result.summary
    lines = [
        f"Capture: {result.capture.path}",
        f"Channel: ch{result.capture.channel}",
        f"Rows: {summary.count}",
        f"Duration: {summary.duration_s:.3f} s",
        f"Scale: VREF={result.vref:g} V, gain={result.gain:g}, LSB={result.lsb_uv:.6f} uV/code",
        "",
        "Frame continuity:",
        f"  counter_nonunit_count: {summary.counter_nonunit_count}",
        f"  counter_missing_sum: {summary.counter_missing_sum}",
        f"  dropped_nonzero_count: {summary.dropped_nonzero_count}",
        f"  dropped_positive_sum: {summary.dropped_positive_sum}",
        "",
        "Amplitude:",
        f"  min/max: {summary.minimum} / {summary.maximum} code",
        f"  median: {summary.median:.1f} code ({summary.median * result.lsb_uv:.2f} uV)",
        f"  RMS about mean: {summary.rms_about_mean:.1f} code ({summary.rms_about_mean * result.lsb_uv:.2f} uV)",
        f"  p95-p5: {summary.p95_p5:.1f} code ({summary.p95_p5 * result.lsb_uv:.2f} uV)",
        f"  p99-p1: {summary.p99_p1:.1f} code ({summary.p99_p1 * result.lsb_uv:.2f} uV)",
        f"  saturation_count: {summary.saturation_count}",
        f"  near_80pct_fullscale_count: {summary.near_80pct_fullscale_count}",
        "",
        "Dominant frequencies from first window:",
        "  "
        + ", ".join(
            f"{frequency}Hz:{power:.2e}" for frequency, power in result.dominant_frequencies
        ),
        "",
        "Selected frequency powers:",
        "  "
        + ", ".join(
            f"{frequency}Hz:{power:.2e}"
            for frequency, power in sorted(result.selected_frequency_powers.items())
        ),
    ]

    if result.block_metrics:
        residuals = [metric.residual_rms for metric in result.block_metrics]
        line_values = [metric.line_rms for metric in result.block_metrics]
        lines.extend(
            [
                "",
                "1 s windows:",
                "  residual RMS q0/q25/q50/q75/q100 code: "
                + _format_numbers(quantiles(residuals, [0, 25, 50, 75, 100])),
                "  residual RMS q0/q25/q50/q75/q100 uV: "
                + _format_numbers(
                    [value * result.lsb_uv for value in quantiles(residuals, [0, 25, 50, 75, 100])]
                ),
                "  line RMS q0/q25/q50/q75/q100 code: "
                + _format_numbers(quantiles(line_values, [0, 25, 50, 75, 100])),
            ]
        )

    if result.epoch_metrics:
        lines.extend(["", "Scheduled epochs:"])
        lines.append(
            "  cycle label       start-end(s) raw_rms  line_rms residual_rms residual_uV p95-p5"
        )
        for metric in result.epoch_metrics:
            lines.append(
                "  "
                f"{metric.cycle:>2}    {metric.label:<11} "
                f"{metric.start_s:>5.1f}-{metric.end_s:<5.1f} "
                f"{metric.raw_rms:>8.1f} {metric.line_rms:>8.1f} "
                f"{metric.residual_rms:>12.1f} "
                f"{metric.residual_rms * result.lsb_uv:>10.2f} "
                f"{metric.p95_p5:>8.1f}"
            )
        lines.append("")
        lines.append(
            "  rest residual mean: "
            + _format_optional(result.rest_residual_mean, result.lsb_uv)
        )
        lines.append(
            "  contraction residual mean: "
            + _format_optional(result.contraction_residual_mean, result.lsb_uv)
        )
        ratio = result.contraction_rest_ratio
        if ratio is not None:
            lines.append(f"  contraction/rest residual ratio: {ratio:.3f}")

    lines.extend(["", "Heuristic interpretation:", *interpret_result(result)])
    return "\n".join(lines)


def print_report(result: CaptureAnalysis) -> None:
    print(format_report(result))


def interpret_result(result: CaptureAnalysis) -> list[str]:
    summary = result.summary
    messages: list[str] = []
    if summary.counter_nonunit_count or summary.dropped_nonzero_count:
        messages.append("- Frame continuity is not clean; inspect missing samples before analysis.")
    else:
        messages.append("- Frame continuity is clean.")

    if summary.saturation_count:
        messages.append("- The selected channel saturated; reduce gain or fix electrode/front-end conditions.")
    else:
        messages.append("- No full-scale saturation was detected.")

    if result.block_metrics:
        median_line = quantile([metric.line_rms for metric in result.block_metrics], 50)
        median_raw = quantile([metric.raw_rms for metric in result.block_metrics], 50)
        if median_raw > 0 and median_line / median_raw > 0.6:
            messages.append("- 50 Hz/harmonic content dominates the raw signal scale.")
        else:
            messages.append("- 50 Hz/harmonic content is not dominant in the raw scale.")

    ratio = result.contraction_rest_ratio
    if ratio is None:
        messages.append("- No rest/contraction schedule was provided.")
    elif ratio >= 1.3:
        messages.append("- Contraction windows show a clear residual-energy increase over rest.")
    elif ratio >= 1.1:
        messages.append("- Contraction windows show only a weak residual-energy increase.")
    else:
        messages.append(
            "- The scheduled contraction windows do not show an EMG-like residual increase over rest."
        )
    return messages


def plot_result(result: CaptureAnalysis, output_path: str | Path | None = None):
    """Plot raw signal and 1 s residual RMS. Requires matplotlib."""

    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=False)
    axes[0].plot(result.capture.time_s, result.capture.values, linewidth=0.7)
    axes[0].set_title(f"{result.capture.path.name} ch{result.capture.channel} raw code")
    axes[0].set_xlabel("Time (s)")
    axes[0].set_ylabel("Code")

    if result.block_metrics:
        x = [metric.start_s for metric in result.block_metrics]
        y = [metric.residual_rms * result.lsb_uv for metric in result.block_metrics]
        axes[1].plot(x, y, marker="o", linewidth=1.0)
        axes[1].set_title("1 s residual RMS after 50 Hz harmonics removal")
        axes[1].set_xlabel("Time (s)")
        axes[1].set_ylabel("uV")

    for metric in result.epoch_metrics:
        color = "#d8f0ff" if metric.label == "rest" else "#ffe2d1"
        for ax in axes:
            ax.axvspan(metric.start_s, metric.end_s, color=color, alpha=0.25)

    fig.tight_layout()
    if output_path is not None:
        fig.savefig(output_path, dpi=160)
    return fig


def code_to_microvolts(code: float, vref: float, gain: float) -> float:
    return code * (2.0 * vref / gain) / (2**24) * 1e6


def quantile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return float("nan")
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = (len(sorted_values) - 1) * percentile / 100.0
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return float(sorted_values[low])
    fraction = position - low
    return float(sorted_values[low] * (1.0 - fraction) + sorted_values[high] * fraction)


def quantiles(values: Sequence[float], percentiles: Sequence[float]) -> list[float]:
    return [quantile(values, percentile) for percentile in percentiles]


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def rms_about_mean(values: Sequence[float]) -> float:
    baseline = mean(values)
    return math.sqrt(sum((value - baseline) ** 2 for value in values) / len(values))


def _format_numbers(values: Sequence[float]) -> str:
    return " / ".join(f"{value:.1f}" for value in values)


def _format_optional(value: float | None, lsb_uv: float) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f} code ({value * lsb_uv:.2f} uV)"


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect an ADS1299 capture CSV.")
    parser.add_argument("csv_path", help="Capture CSV path.")
    parser.add_argument("--channel", type=int, default=8, help="Channel number to inspect.")
    parser.add_argument("--fs", type=float, default=DEFAULT_FS_HZ, help="Sampling rate in Hz.")
    parser.add_argument("--gain", type=float, default=DEFAULT_GAIN, help="ADS1299 gain.")
    parser.add_argument("--vref", type=float, default=DEFAULT_VREF, help="ADS1299 VREF in volts.")
    parser.add_argument("--rest", type=float, help="Rest duration per cycle in seconds.")
    parser.add_argument("--contract", type=float, help="Contraction duration per cycle in seconds.")
    parser.add_argument("--cycles", type=int, help="Number of complete cycles to analyze.")
    parser.add_argument("--plot", help="Optional output PNG path.")
    args = parser.parse_args()

    result = analyze_capture(
        args.csv_path,
        channel=args.channel,
        fs_hz=args.fs,
        gain=args.gain,
        vref=args.vref,
        rest_s=args.rest,
        contract_s=args.contract,
        cycles=args.cycles,
    )
    print_report(result)
    if args.plot:
        plot_result(result, args.plot)
        print(f"\nSaved plot to {args.plot}")


if __name__ == "__main__":
    main()
