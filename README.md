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
│   ├── __init__.py     # exports Explorer, ExpExplorer (= LegacyExplorer)
│   ├── explorer.py     # current interface — under development
│   └── legacy.py       # frozen original implementation (ExpExplorer)
└── tests/
    ├── conftest.py     # synthetic experiment-directory fixtures
    └── test_legacy.py  # smoke tests for the legacy explorer
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

## Tests

```bash
pytest
```

The fixtures build a synthetic experiment directory in a temp dir, so the suite
needs no real data.
