# Capture Analysis

Run a text report from the project root:

```bash
python -m analysis.inspect_capture capture/capture_20260627_184725.csv --channel 8 --gain 2 --rest 5 --contract 5 --cycles 5
```

Notebook usage:

```python
from analysis.inspect_capture import analyze_capture, print_report, plot_result

result = analyze_capture(
    "capture/capture_20260627_184725.csv",
    channel=8,
    gain=2,
    rest_s=5,
    contract_s=5,
    cycles=5,
)
print_report(result)
plot_result(result)
```

The core analysis uses only the Python standard library. `plot_result` imports
matplotlib lazily, so text reports still work without plotting dependencies.
