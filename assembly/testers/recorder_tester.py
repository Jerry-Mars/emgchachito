"""Standalone normalized-stream -> HDF5 recorder tester.

This tester intentionally uses fake normalized samples: it verifies the save
capability without requiring hardware, Plot, or DearPyGui.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import h5py

from assembly.acquisition.runtime.stream_store import RealtimeStreamStore, StreamSample, StreamSchema
from assembly.save.recorder import H5StreamRecorder
from assembly.save.store_tap import StreamStoreTap


def _default_output() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("captures") / f"recorder_tester_{stamp}.h5"


def run(output: Path) -> Path:
    schemas = (
        StreamSchema("demo.known", ("x", "y"), nominal_rate_hz=100.0),
        StreamSchema("demo.unknown", ("value",), nominal_rate_hz=None),
    )
    store = RealtimeStreamStore(schemas, retention_seconds=5.0)
    recorder = H5StreamRecorder()
    tap = StreamStoreTap(store, recorder)

    print("[1] Commit one row before Save Start (must not appear in file)")
    tap.append(
        "demo.known",
        host_monotonic_ns=1_000_000_000,
        host_unix_ns=10_000_000_000,
        values=(1.0, 2.0),
    )

    print("[2] Save Start ->", output)
    actual_path = recorder.start(output, store.schemas())

    tap.append_batch(
        "demo.known",
        (
            StreamSample(1_010_000_000, 10_010_000_000, (3.0, 4.0)),
            StreamSample(1_020_000_000, 10_020_000_000, (5.0, 6.0)),
        ),
    )
    tap.append(
        "demo.unknown",
        host_monotonic_ns=1_015_000_000,
        host_unix_ns=10_015_000_000,
        values=(0.25,),
    )

    print("[3] Save Stop; rows written =", recorder.rows_written)
    recorder.stop()

    print("[4] Read back")
    with h5py.File(actual_path, "r") as handle:
        print("format:", handle.attrs["format"], "version:", handle.attrs["format_version"])
        for group in handle["streams"].values():
            stream_id = str(group.attrs["stream_id"])
            fields = tuple(json.loads(group.attrs["field_keys_json"]))
            print(f"\n{stream_id}")
            print("  fields            :", fields)
            print("  nominal_rate_known:", bool(group.attrs["nominal_rate_known"]))
            print("  runtime_index     :", group["runtime_index"][:].tolist())
            print("  host_monotonic_ns :", group["host_monotonic_ns"][:].tolist())
            print("  host_unix_ns      :", group["host_unix_ns"][:].tolist())
            print("  values            :", group["values"][:].tolist())

    print("\nRealtime store rows (includes pre-record row):", store.row_count)
    print("Saved file:", actual_path.resolve())
    return actual_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None, help="Output .h5 file path.")
    args = parser.parse_args()
    run(_default_output() if args.output is None else args.output)


if __name__ == "__main__":
    main()
