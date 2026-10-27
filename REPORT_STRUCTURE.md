# Report structure

The two reports deliberately share a flow. A reader who knows one should not have
to learn the other: the same sections appear in the same order, and the combined
report adds exactly one section the single one has no need of.

Both are generated entirely in Python from the parsed experiment structures
(`explorer/report.py`, `explorer/combined.py`) and rendered by
`explorer/render_reportlab.py`. Nothing in either is written by hand per
experiment.

---

## Single experiment — `ExpReport`

| # | Section | Contents | Empty when |
|---|---------|----------|------------|
| 1 | *(preamble)* | `# Experiment Report`, the experiment name as a subtitle, then name, eid, created, duration, user, host, directory, parse mode | never |
| 2 | `## Experiment notebook` | `user_note` events: elapsed + note. Falls back to the day-level Metaexperiment's notes when the run has none of its own, labelled with where they came from | says so explicitly |
| 3 | `## Event summary` | Counts: measurements, events, streams, sessions, then one row per event type | never |
| 4 | `## Experiment attributes` | `### Attributes` (`attribs`), `### Scripts` (payload events; "only version" when unversioned), `### Protocols` (documents shipped with the run, plus protocol events) | no attribs, scripts or protocols |
| 5 | `## Measurement streams` | One `###` block per stream: definition (measureid, declared columns, started, rows), then statistics (n, min, mean, median, max), value counts for non-numeric columns, and a plot. Declared-but-empty columns are named | no streams declared |
| 6 | `## Data` | Files the run wrote, by type and by folder, excluding downstream output. `### Files with no event` lists what exists on disk that no event announced, placed by filename nanosecond stamp or mtime | directory unreachable |
| 7 | `## Analysis` | Downstream products (`postprocess/`, `analysis/`), counted apart from the run's own data | no downstream output |
| 8 | `## Appendix` | `### Device tree`, `### Protocol documents` (full text), `### Event log` (every event in order, with unlogged files merged in and recurring types folded), `### Event timeline`, `### Milestones`, `### Attachments` | — |

### Rules that shape it

- **Statistics live with their stream**, never in a separate section, and raw
  measurement rows are never tabulated — 256 rows say less than five numbers and
  a plot.
- **Recurring events are folded**, not hidden: a type spanning more than half the
  run collapses to one row stating its count, its last occurrence and its
  cadence. The first occurrence is always shown in place.
- **The log is not an inventory.** Files found on disk with no event are listed,
  with the clock each was placed by (`filename` stamp or `mtime`) named per row.
- **Parse mode is what happened**, not what was asked for.

---

## Many experiments — `CombinedReport`

Same flow, with **Experiments overview** added at position 2 — the one thing a
combined report must say that a single one need not.

| # | Section | Differs from the single report by |
|---|---------|-----------------------------------|
| 1 | *(preamble)* | `# Combined Experiment Report`; experiment count with every eid listed (truncated past 12), scopes, first/last start, hosts, parse modes |
| 2 | `## Experiments overview` | **New.** One row per experiment: scope, eid, started, duration, measurements, events, streams, notes. A nested experiment is marked `↳` |
| 3 | `## Experiment notebook` | Keyed on **wall clock**, not elapsed; adds Scope ID and eid columns; a date row wherever the day changes |
| 4 | `## Event summary` | A matrix: event types down, experiments across, with totals |
| 5 | `## Experiment attributes` | Identical values collapse onto one row with scopes as a range (`M1–M8`); exceptions split out. Same for scripts and protocols |
| 6 | `## Measurement streams` | One block per stream name, a row per experiment, and one overlay plot with a line per experiment |
| 7 | `## Data` | A row per experiment |
| 8 | `## Analysis` | A row per experiment |
| 9 | `## Appendix` | `### Devices` (one tree per distinct inventory, labelled by scope range; physical objects as a table with identical scopes merged), `### Event timeline` (lanes by event type, **colour by scope**, sessions as spans, wall-clock axis), `### Event log` (all experiments merged, scope and eid per row, date rows on change) |

### Rules that shape it

- **`exptime` is not comparable between experiments.** Two scopes that started
  eleven minutes apart both call their first second `0:00:01`. Anything merged
  across experiments is keyed on `dt`; per-experiment statistics stay on each
  run's own clock, where elapsed is the right reading.
- **Comparison, not repetition.** Eight scopes running one protocol share their
  devices, scripts and parameters, so those are collapsed to a scope range and
  only the differences get space.
- **Nested experiments are always combined.** A crashed run restarted into a new
  folder leaves the original inside it; `CombinedReport.discover()` walks
  recursively so the inner run is never silently dropped, and neither report
  counts its files twice.
- **No interpretation of intent.** The reports state what the data holds. Why a
  run stopped, or whether a restart followed a crash, is not recoverable from
  the logs and is not asserted.

---

## Shared conventions

- Floats print at two decimals everywhere values are formatted; numbers inside
  note prose are left exactly as the author wrote them.
- Quantities and clock times are tinted (muted, print-safe) the way `rich`
  highlights its output.
- OS bookkeeping files (`.DS_Store`, `._*`, `Thumbs.db`, …) are excluded from
  every file count.
- A section that has nothing to say is omitted, except where its absence is
  itself the finding — an empty notebook, or attributes no experiment recorded.
