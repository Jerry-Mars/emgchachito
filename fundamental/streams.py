"""Schema-driven contracts for heterogeneous acquisition streams."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Mapping, TypeAlias


Scalar: TypeAlias = int | float
FieldRole = Literal["metadata", "signal"]
SignalKind = Literal["emg", "eeg", "quaternion", "acceleration", "angular_velocity", "generic"]


@dataclass(frozen=True)
class FieldSpec:
    """Describe one column carried by a stream."""

    key: str
    label: str
    unit: str = ""
    role: FieldRole = "signal"
    signal_kind: SignalKind = "generic"
    plottable: bool = True
    default_plot: bool = False
    fixed_range: tuple[float, float] | None = None
    csv_decimals: int | None = None

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("FieldSpec.key cannot be empty.")
        if self.role == "metadata" and self.plottable:
            raise ValueError("Metadata fields cannot be plottable.")
        if self.fixed_range is not None and self.fixed_range[0] >= self.fixed_range[1]:
            raise ValueError("FieldSpec.fixed_range must be increasing.")


@dataclass(frozen=True)
class StreamSpec:
    """Describe one independently sampled tabular data stream."""

    stream_id: str
    display_name: str
    nominal_rate_hz: float | None
    fields: tuple[FieldSpec, ...]
    active_signal_count_key: str | None = None
    time_source: str = "capture_relative"

    def __post_init__(self) -> None:
        if not self.stream_id.strip():
            raise ValueError("StreamSpec.stream_id cannot be empty.")
        if not self.fields:
            raise ValueError("StreamSpec.fields cannot be empty.")
        keys = [field.key for field in self.fields]
        if len(set(keys)) != len(keys):
            raise ValueError(f"Stream {self.stream_id!r} contains duplicate field keys.")
        if self.active_signal_count_key is not None and self.active_signal_count_key not in keys:
            raise ValueError("active_signal_count_key must name one of the stream fields.")
        if self.nominal_rate_hz is not None and self.nominal_rate_hz <= 0:
            raise ValueError("nominal_rate_hz must be positive when provided.")

    @property
    def field_index(self) -> Mapping[str, int]:
        return MappingProxyType({field.key: index for index, field in enumerate(self.fields)})

    @property
    def signal_fields(self) -> tuple[FieldSpec, ...]:
        return tuple(field for field in self.fields if field.role == "signal")


@dataclass(frozen=True)
class StreamBlock:
    """A validated batch of rows from one stream."""

    spec: StreamSpec
    time_s: tuple[float, ...]
    rows: tuple[tuple[Scalar, ...], ...]

    def __post_init__(self) -> None:
        if len(self.time_s) != len(self.rows):
            raise ValueError("StreamBlock time and row counts must match.")
        width = len(self.spec.fields)
        if any(len(row) != width for row in self.rows):
            raise ValueError(f"Every {self.spec.stream_id!r} row must contain {width} values.")

    @property
    def row_count(self) -> int:
        return len(self.rows)


@dataclass(frozen=True)
class StreamSnapshot:
    """Immutable full-capture view used by persistence."""

    spec: StreamSpec
    time_s: tuple[float, ...]
    rows: tuple[tuple[Scalar, ...], ...]

    @property
    def row_count(self) -> int:
        return len(self.rows)


@dataclass(frozen=True)
class StreamCursor:
    """Last accepted row of a stream, passed to a resumed worker."""

    spec: StreamSpec
    row_count: int
    last_time_s: float
    last_row: tuple[Scalar, ...]

    def value(self, field_key: str) -> Scalar | None:
        index = self.spec.field_index.get(field_key)
        if index is None:
            return None
        return self.last_row[index]


@dataclass(frozen=True)
class CaptureResumeState:
    """Read-only cursors for all streams already stored in a capture."""

    latest_time_s: float = 0.0
    streams: Mapping[str, StreamCursor] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def cursor(self, stream_id: str) -> StreamCursor | None:
        return self.streams.get(stream_id)


@dataclass(frozen=True)
class SeriesSpec:
    """One plottable scalar field exposed by a CaptureStore."""

    series_id: str
    stream_id: str
    field_key: str
    label: str
    unit: str
    signal_kind: SignalKind
    default_plot: bool
    fixed_range: tuple[float, float] | None

    @property
    def view_options(self) -> tuple[str, ...]:
        if self.signal_kind == "emg":
            return ("Raw", "Rectified", "RMS", "Envelope")
        return ("Raw",)


@dataclass(frozen=True)
class SeriesWindow:
    """Recent values for one plot series."""

    spec: SeriesSpec
    time_s: list[float]
    values: list[float]


def freeze_cursors(cursors: dict[str, StreamCursor]) -> Mapping[str, StreamCursor]:
    return MappingProxyType(dict(cursors))
