# REMIND ME — How to Extend `assembly`

This file is a reminder for future development work in `assembly`.

When adding a new module, feature, device, recorder, stimulus component, GUI experiment, or composition, **do not start by designing a generic framework**. First recover the original reason `assembly` exists.

## 1. Why `assembly` exists

`assembly` was created because the first implementation in `fundamental` gradually became difficult to reason about as a whole.

The main problem was not simply that some classes were large. The deeper problem was that, once acquisition, device configuration, buffering, persistence, stimulus, metadata, health, GUI state, pause/resume, and lifecycle coordination became intertwined, it became hard to answer practical maintenance questions such as:

- If a device protocol changes, which layer should change?
- If saving changes, why should acquisition code change?
- If a GUI is redesigned, which runtime assumptions are actually required?
- If a new feature is added, where should it attach?
- If a bug appears, which module owns the violated semantic?

The goal of `assembly` is therefore not merely "more abstraction" or "more modularity".

The goal is to create **navigable complexity**:

- small capability boundaries that can be understood locally;
- modules whose responsibilities are explicit;
- modules that can be tested without reconstructing the whole application;
- executable examples that show how modules are actually composed;
- higher-level compositions that can be rewritten without destabilizing lower-level semantics.

A useful mental model is:

```text
small stable primitives
        +
device / feature implementations
        +
executable capability testers
        +
explicit composition experiments
        ↓
progressively larger applications
```

The composition may change many times. Stable lower-level semantics should not change merely because a new UI or feature is convenient to implement differently.

---

## 2. The basic development unit is a capability boundary, not necessarily a class

Do not assume every Python class needs its own tester or CLI.

A useful standalone capability is something that can answer a concrete question by being run or exercised independently.

Examples:

```text
W2 worker
→ Can one physical W2 start, emit valid raw observations, and stop cleanly?

W2 worker + ingestor
→ Can raw W2 observations enter the normalized acquisition core correctly?

Realtime plot
→ Can a PlotDataProvider drive the plotting UI without any hardware dependency?

Recorder
→ Can normalized observations be persisted completely and independently of Plot?
```

By contrast, pure data structures such as `StreamSchema` or small deterministic helpers are usually better covered by unit tests rather than hardware-style executable testers.

### Practical rule

Use an executable tester when a capability benefits from:

- real hardware;
- actual runtime lifecycle;
- manual observation;
- timing/concurrency behavior;
- inspecting realistic intermediate data;
- an interactive experiment.

Use ordinary unit tests when the contract is deterministic and local.

---

## 3. Tester form is deliberately open

Do **not** standardize tester form prematurely.

A tester may be:

- a small `.py` script;
- a Tyro CLI;
- a plain `argparse` script;
- a notebook;
- a script plus notebook;
- a tiny DearPyGui diagnostic window;
- a fake-data simulation;
- another form that best exposes the capability being studied.

Tyro is currently useful because it makes hardware parameters easy to change without editing source code, but **Tyro is not an architectural requirement**.

Choose the tester form based on what needs to be observed.

Examples:

- serial worker: CLI is usually sufficient;
- BLE discovery experiments: CLI or notebook may be useful;
- signal-processing behavior requiring exploratory plots: notebook may be better;
- GUI composition: a dedicated DearPyGui experiment may be the right tester;
- deterministic stream semantics: unit test is preferable to an interactive tester.

The tester should reveal the module, not force the module to conform to the tester.

---

## 4. Production code must never depend on tester code

The dependency direction is one-way:

```text
tester / composition harness
            ↓
production module
```

Never:

```text
production module
      ↓
tester helper / GUI test harness
```

Do not add production APIs solely because they make a tester easier to write unless the API also represents a real domain/runtime capability.

If a tester is difficult to build, first ask why:

1. Is a real capability boundary missing?
2. Or is the tester trying to inspect an internal implementation detail that should simply remain internal?

Do not automatically choose option 1.

---

## 5. Preserve real differences; do not unify merely for visual consistency

Different devices and functions may have genuinely different semantics.

Examples already discovered:

```text
Myo
caller-owned BLE discovery
one worker → EMG + IMU streams
callback may contain multiple EMG samples

W2
serial transport
one physical worker → one signal stream
packet may contain multiple samples
known nominal rate

BWT901
BLE scan/connect/notify
one physical worker → IMU stream
unknown nominal rate
```

Do not invent a generic abstraction merely because these modules all have superficially similar code.

For example, avoid forcing every device into a generic configuration object if that would hide meaningful differences between:

```text
BLEDevice discovery
serial COM port
BLE name/address resolution
```

Likewise, do not define a generic `Function` interface merely because Plot, Recorder, and Stimulus all appear to have `start/update/stop` behavior.

Their data semantics differ:

```text
Plot
pulls recent windows

Recorder
must receive complete committed data

Stimulus
belongs primarily to control/experiment coordination
```

A shared interface is justified only when the shared behavior is a real stable fact, not a syntactic coincidence.

---

## 6. Prefer explicit repetition while the architecture is still being discovered

During early composition work, some duplication is desirable.

For example:

```text
live_w2_plot.py
live_bwt901_plot.py
```

may both explicitly construct:

```text
worker
queue
pump
ingestor
store
provider
lifecycle
```

This repetition makes each vertical slice readable from top to bottom.

Do not immediately hide it behind:

```text
factory
registry
generic pipeline builder
plugin framework
```

Premature removal of repetition can make the system harder to understand than the duplicated code.

### Extraction rule

Before introducing a new abstraction, ask:

1. Does it remove repetition that already exists in at least two real compositions?
2. Is the repeated part semantically the same, not merely syntactically similar?
3. Will removing this abstraction leave lower-level domain semantics unchanged?
4. Can the abstraction be described in one precise sentence?

If not, defer it.

A useful principle:

> Extract after repetition, not before variation.

---

## 7. Add tests for new architectural facts, not every permutation

Avoid combinatorial testing merely for completeness.

Suppose devices are:

```text
Myo
W2
BWT901
```

and functions are:

```text
Plot
Recorder
```

Do not automatically create and deeply test every combination.

Instead ask:

> What new system property is this test proving?

Examples:

```text
BWT901 + Plot
```

was valuable because it proved an unknown-rate stream could use the existing realtime core and PlotProvider.

```text
2×W2 + Recorder
```

may be a good representative Recorder test because it stresses multi-worker, multi-stream, high-rate acquisition and packet-to-sample expansion.

Once the Recorder boundary is shown to consume generic normalized rows, repeating the same full validation for Myo and BWT901 is unnecessary unless those devices expose a genuinely new Recorder-specific semantic.

### Stop rule

When the new architectural property has been demonstrated by a representative case and protected by deterministic tests, stop expanding the matrix.

Do not keep adding devices or consumers merely to increase confidence in "genericity".

---

## 8. Develop a new capability with the cheapest evidence first

For a new feature, use an evidence ladder instead of immediately building the final system.

A practical sequence is:

### Step 1 — State the exact question

Example:

```text
Can normalized acquisition data be persisted completely without relying on the finite realtime Plot store?
```

Not:

```text
Build the recording subsystem.
```

### Step 2 — Build the smallest isolated implementation/tester

For example:

```text
fake normalized rows → Recorder → file
```

or:

```text
2×W2 → Recorder
```

### Step 3 — Observe where the current boundaries resist composition

If a new seam is genuinely necessary, extract only that seam.

Do not redesign surrounding modules preemptively.

### Step 4 — Add one composition test

For example:

```text
same acquisition
├→ realtime Plot
└→ Recorder
```

This verifies the seam under composition.

### Step 5 — Stop if the property is proven

Do not immediately generalize further.

### Step 6 — Convert stable semantics into deterministic tests

Interactive/tester evidence discovers the contract.
Unit tests protect the contract afterward.

---

## 9. Protect stable lower-level semantics from upper-level convenience

Current important acquisition semantics include:

```text
device-provided information != runtime-generated ordering

runtime_index
= RealtimeStreamStore-generated normalized order per stream

host_monotonic_ns
= canonical host observation timeline for realtime ordering/windowing

host_unix_ns
= wall-clock observation metadata/audit coordinate

nominal_rate_hz
= optional interpretation/display/processing metadata

window(seconds)
= host-time query

tail_samples(count)
= count/order query
```

A new Recorder, Stimulus system, GUI, or composition should not modify these merely because another representation would make the new feature easier.

Whenever a stable core change is proposed, ask:

> Did we discover a new real data/lifecycle fact, or is this only an upper-layer implementation convenience?

If it is only convenience, redesign the upper layer first.

---

## 10. Keep lifecycle plane and data plane conceptually separate

Current direction:

```text
LIFECYCLE
WorkerGroup
    ↓
workers

DATA
worker queue
    ↓
QueuePump
    ↓
device ingestor
    ↓
normalized stream/store
```

Do not make `WorkerGroup` understand StreamStore/Plot/Recorder.
Do not make StreamStore understand workers or hardware lifecycle.

A future application/runtime object may compose both planes, but composition is not permission to merge their semantics.

---

## 11. Treat executable composition files as laboratories, not sacred final architecture

Files such as:

```text
live_w2_plot.py
live_myo_plot.py
live_bwt901_plot.py
```

are valuable because they make an entire vertical slice visible.

They are **composition experiments / executable documentation**.

They do not need to survive forever.

A good lower-level module should survive a rewrite of the composition script.
The composition script itself may later shrink, move, or disappear when a stable higher-level runtime emerges.

Therefore, do not over-polish a temporary composition root into a framework before repeated compositions prove what should be extracted.

---

## 12. GUI experiments must remain replaceable

DearPyGui is intentionally flexible, and multiple GUI paradigms may be explored:

```text
conventional engineering dashboard
read-only topology
editable graph
minimal experiment-specific UI
debug/diagnostic UI
```

Do not assume there will be one final GUI architecture early in development.

The stable direction should be:

```text
runtime / capability modules
        ↑
        │ stable API/semantics
-------------------------------
        │
        ↓
replaceable GUI experiments
```

A DearPyGui callback should ideally invoke an application/runtime capability rather than directly manipulating BLE, serial handles, Worker internals, or queues.

For example, prefer conceptually:

```text
button
→ runtime.start()
```

rather than:

```text
button
→ serial open
→ worker.start
→ queue manipulation
```

A node editor, if introduced later, should initially describe/inspect or configure a runtime graph. DearPyGui node objects themselves should not become the runtime dataflow engine.

---

## 13. Learn from `fundamental`, but do not mechanically invert it

The lesson from `fundamental` is not simply:

```text
large class = bad
controller = bad
```

Some coordination object is genuinely necessary in a complete application.

The real failure mode to watch for is:

> A composition/controller object begins as wiring, then gradually becomes the owner of unrelated domain semantics.

Examples of concerns that accumulated together previously include:

```text
device configs
worker creation
queues
buffering
CaptureClock
health
metadata
CSV persistence
pause/resume
stimulus coordination
GUI-facing state
```

When a future `AcquisitionRuntime`, `ExperimentCoordinator`, or other composition object is proposed, keep asking:

- Is this object coordinating independent modules?
- Or is it absorbing their implementation and becoming the only place where the system can be understood?

The former may be useful.
The latter recreates the original problem.

---

## 14. A practical modification map should remain possible

A healthy project should let a developer approximately map a problem to its owner:

```text
BLE/serial connection failure
→ worker / transport boundary

packet parsing incorrect
→ protocol parser

raw record → normalized field semantics incorrect
→ ingestor/schema

recent realtime retention/windowing incorrect
→ RealtimeStreamStore

plot display/query incorrect
→ PlotProvider / Plot

persistent output incorrect
→ Recorder

stimulus state/annotation incorrect
→ Stimulus / experiment coordinator

GUI interaction incorrect
→ GUI adapter/composition
```

If a future change cannot be located without tracing the entire application, treat that as an architecture warning.

---

## 15. Before implementing any future module, answer these questions

When the user asks to add a new feature/module, first reread this file and answer internally:

### A. What exact capability is being added?

State it without mentioning the planned class name or framework.

### B. What is the smallest independently observable experiment?

Choose `.py`, notebook, GUI experiment, fake-data test, Tyro CLI, etc. based on the capability.

### C. Which existing stable modules should remain unchanged?

Write these down before coding.

### D. What new architectural fact does this work need to prove?

If no new fact exists, prefer reuse over another large validation phase.

### E. Is a new abstraction actually required?

Only add one after concrete composition pain/repetition demonstrates it.

### F. How will this module later compose with another capability?

Do not solve every future composition now; ensure the current design does not obviously prevent the next representative composition.

### G. What is the stop condition?

Define when enough evidence exists and stop testing/generalizing once it is met.

---

## 16. Preferred long-term shape

Do not aim for a universal framework by default.

Prefer a repository that remains understandable as:

```text
assembly/
├── acquisition/          # stable acquisition primitives + device implementations
├── plot/                 # independent presentation capability
├── testers/              # executable capability experiments
├── recorder/             # when a real recorder capability is introduced
├── stimulus/             # when rebuilt from real requirements
├── ...
└── composition/apps      # only when repeated executable compositions justify it
```

The exact directories may change as real patterns emerge. Do not create empty architecture merely to match this sketch.

The important invariant is conceptual:

```text
capabilities can be understood independently
           ↓
capabilities can be composed explicitly
           ↓
repeated composition reveals genuine abstractions
           ↓
only then are those abstractions stabilized
```

---

# Final reminder

When working on `assembly`, optimize first for:

1. **Can I understand this capability without loading the whole application into my head?**
2. **Can I run or test it independently when that is useful?**
3. **Can I identify which module owns a change or failure?**
4. **Can I compose it with existing capabilities without changing their semantics?**
5. **Am I introducing this abstraction because real code already demands it, or because I imagine it may be useful later?**
6. **Do I know when to stop testing/generalizing?**

If these questions remain easy to answer as the project grows, the refactor is serving its original purpose.
