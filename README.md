# trappy-`exp`lorer

Experiment exploration tool for the Trappy-Scopes framework.

<img src="./CR_the_sailor_man.png" alt="CR_the_sailor_man" style="zoom:10%;" />



> ### Reviews
>
> > "it's cute as shit"
> >
> > —Monica



Tool to explore a Trappy-Scopes experiment. The expeimentation framework is explained in more details here: [Trappy-Scopes expframework](https://trappy-scopes.github.io/trappyscopes/expframework/). This tool only exposes the generic experiment interface. For specifically exploring cell data -- the `CellView` object from `trappytv` or the `CellManager` and the `CellSet` object from `trackyscope` are more suitable.

## Install

```bash
pip install -e ".[dev]"
```

Runtime dependencies: `pyyaml`, `pandas`, `rich`.

## Layout

```
trappy-explorer/
├── pyproject.toml
├── explorer/
│   ├── __init__.py     # exports Explorer, ExpExplorer (= LegacyExplorer), ExpReport
│   ├── explorer.py     # current interface — under development
│   ├── legacy.py       # frozen original implementation (ExpExplorer)
│   └── report.py       # Markdown / HTML / PDF reports (ExpReport)
└── tests/
    ├── conftest.py     # synthetic experiment-directory fixtures
    ├── test_legacy.py  # smoke tests for the legacy explorer
    └── test_report.py  # tests for the report renderer
```

### Legacy explorer

`explorer/legacy.py` is a frozen snapshot of the original single-file
implementation, preserved so existing notebooks keep working and so the current
interface has a reference to be checked against. Only the minimal fixes needed to
make it import and run were applied — each is marked `# LEGACY-FIX` and listed in
the module docstring. It is not where new features go.

```python
from explorer import ExpExplorer

exp = ExpExplorer(["/path/to/experiment-directory"])
exp                       # summary of experiments and measurement streams
exp.df                    # all measurements, as a DataFrame
exp.df_events             # all events, as a DataFrame
exp.notebook()            # rich table of user notes
exp.all_events()          # rich table of every event

# whole folder of experiments at once
exp = ExpExplorer.create_superset("/path/to/parent-directory")
```

Each experiment directory must contain an `experiment.yaml`; directories without
one are reported and skipped. `safemode=True` (the default) parses with a
SafeLoader that maps unknown YAML tags to `None`, so complex serialized objects
never break parsing.

Per-experiment data lives under `exp.data[eid]` as `results`, `events`,
`filetree`, `metadata` and `m_streams`, and the subdirectory helpers
`get_parent_dir`, `get_analysis_dir`, `get_converted_dir` and
`get_postprocess_dir` resolve the conventional paths.

### Reports

`ExpReport` renders one experiment from an explorer — it takes the explorer
rather than being mixed into it, so anything the explorer can load, it can
report on.

```python
from explorer import ExpExplorer, ExpReport

exp = ExpExplorer(["/path/to/experiment-dir"], extended_parse=True)
rep = ExpReport(exp)              # first experiment; or ExpReport(exp, eid)

print(rep.markdown())             # the whole report as text
rep.to_markdown()                 # -> <experiment>/analysis/report.md
rep.to_html("/tmp/report.html")
rep.to_pdf("/tmp/report.pdf")

rep.add_paragraph("Colony A, second passage.")
rep.add_fig(fig, caption="Growth curve")   # matplotlib figure -> appendix
```

The report covers the preamble (name, eid, created, duration, host), a summary,
the measurement streams, the lab notebook, one table per stream, and the full
event list.

Results only identify themselves by `measureid`, so the stream name is joined on
from the `measurement_stream` events, and each stream's table drops the columns
that belong to other streams. A stream's payload columns are read from
`measurements` **+ `monitors` + `detections`** — all three are the same mechanism
in the framework — and any declared column with no values is called out rather
than silently left blank:

```
_Declared but empty: counts, density — the values were dropped by the parse._
```

That line is the one to watch. Under the default legacy safemode, serialised
numpy payloads read as empty, so a stream that clearly measured something shows
blank columns; reload with `extended_parse=True` and they come back. The report
states which mode produced it in the preamble.

The PDF keeps the visual theme of the original `report.py`: dark RGB(15, 20, 25)
bands across the top and foot of every page, with the generation timestamp, page
number and eid in white at the foot.

The header band carries the two logos — the wordmark left, the cell mark right —
and no text. The cell mark is the dark-background variant: flat green body, black
eyespot, **white** flagella, so it reads on the band and would look like a bare
ellipse on a white page. That band colour is the background `trappyscopes.png` is drawn on, and
`tsicon.png` is transparent, so neither shows an edge: the logos and the band are
one surface. Both ship in `explorer/assets`, flattened to plain RGB with their
colour metadata stripped (leave the PNG's `srgb` chunk in and the viewer colour-
manages the image but not the band behind it, so the wordmark shows up as a
lighter rectangle). Override with `logo=` / `icon=`, or pass `False` to drop one.

Markdown needs nothing beyond the runtime dependencies. HTML needs
`markdown-it-py` (`pip install -e ".[report]"`). PDF goes through the `pandoc`
binary and a LaTeX engine (`xelatex` or `pdflatex`) — system dependencies, not
pip ones; `to_pdf()` names whichever is missing.

## Tests

```bash
pytest
```

The fixtures build a synthetic experiment directory in a temp dir, so the suite
needs no real data.
