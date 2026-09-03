"""Minimal executable example showing how to use the independent MIIL module.

Run:
    python -m assembly.testers.miil_valid_simple

The example deliberately uses deterministic fake host boundaries so the result is
immediate and reproducible. In a real GUI callback, replace ``at(seconds)`` with
``capture_host_boundary()`` from ``assembly.experiment.miil``.
"""

from __future__ import annotations

from assembly.experiment.miil import MIILAction, MIILBoundary, MIILController

NANOSECONDS_PER_SECOND = 1_000_000_000
FAKE_UNIX_ORIGIN_NS = 1_800_000_000_000_000_000


def at(seconds: float) -> MIILBoundary:
    """Build one deterministic boundary in the same shape as a real host boundary."""

    offset_ns = int(seconds * NANOSECONDS_PER_SECOND)
    return MIILBoundary(
        host_monotonic_ns=offset_ns,
        host_unix_ns=FAKE_UNIX_ORIGIN_NS + offset_ns,
    )


def print_intervals(miil: MIILController) -> None:
    print("\nRecorded MIIL intervals")
    print("event  code  action           start_s  end_s   status")
    for interval in miil.intervals:
        start_s = interval.start_monotonic_ns / NANOSECONDS_PER_SECOND
        end_ns = interval.end_monotonic_ns
        end_s = "-" if end_ns is None else f"{end_ns / NANOSECONDS_PER_SECOND:.1f}"
        print(
            f"{interval.event_index:>5}  "
            f"{interval.effective_code:>4}  "
            f"{interval.action:<15}  "
            f"{start_s:>7.1f}  "
            f"{end_s:>5}   "
            f"{interval.status}"
        )


def main() -> None:
    # 1) The caller defines the experiment's selectable instruction codebook.
    actions = (
        MIILAction("rest", "Rest", 1),
        MIILAction("knee_flexion", "Knee Flexion", 2),
        MIILAction("knee_extension", "Knee Extension", 3),
    )
    miil = MIILController(actions)

    print("MIIL valid_simple")
    print("Configured actions:", [(a.code, a.action) for a in miil.actions])

    # 2) Start opens an implicit no_stimulus interval (code 0).
    print("\n0.0 s:", miil.start(at(0.0)))

    # 3) A normal operator button maps directly to select_action(code, boundary).
    print("2.0 s:", miil.select_action(1, at(2.0)))
    print("6.0 s:", miil.select_action(2, at(6.0)))

    # 4) drop_current invalidates the *whole current interval* using code -1.
    print("9.0 s:", miil.drop_current(at(9.0)))

    # 5) no_stimulus is an explicit neutral instruction interval.
    print("10.0 s:", miil.select_no_stimulus(at(10.0)))
    print("12.0 s:", miil.select_action(3, at(12.0)))

    # 6) Stop closes the final half-open interval [start, stop).
    print("18.0 s:", miil.stop(at(18.0)))

    print_intervals(miil)

    # 7) Future acquisition integration only needs this operation: resolve a
    # normalized row's host_monotonic_ns against the MIIL timeline.
    print("\nTime-based label lookup")
    for seconds in (1.0, 3.0, 7.0, 9.5, 11.0, 13.0, 18.0):
        code = miil.code_at(int(seconds * NANOSECONDS_PER_SECOND))
        print(f"{seconds:>4.1f} s -> stimulus_code {code}")

    # 8) metadata_snapshot is JSON-serializable and is the intended boundary for
    # later MIIL persistence/session integration.
    metadata = miil.metadata_snapshot()
    print("\nMetadata boundary method:", metadata["boundary_method"])
    print("Final state:", metadata["state"])

    print(
        "\nReal integration mapping:\n"
        "  GUI button callback -> capture_host_boundary() -> "
        "miil.select_action(code, boundary)\n"
        "  acquisition row.host_monotonic_ns -> miil.code_at(...)"
    )


if __name__ == "__main__":
    main()
