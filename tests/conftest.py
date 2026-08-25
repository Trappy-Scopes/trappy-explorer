import os
import textwrap

import pytest

EXPERIMENT_YAML = textwrap.dedent(
    """
    eid: EXP-0001
    scopeid: scope-a
    operator: tester
    results:
      - eid: EXP-0001
        scopeid: scope-a
        exptime: 1.0
        name: temperature
        value: 21.5
      - eid: EXP-0001
        scopeid: scope-a
        exptime: 2.0
        name: temperature
        value: 21.7
    events:
      - eid: EXP-0001
        scopeid: scope-a
        exptime: 0.0
        type: user_note
        note: started the run
      - eid: EXP-0001
        scopeid: scope-a
        exptime: 0.5
        type: measurement_stream
        name: temperature
    """
).strip()


@pytest.fixture
def experiment_dir(tmp_path):
    """A minimal, valid Trappy-Scopes experiment directory."""
    d = tmp_path / "EXP-0001"
    for sub in ("analysis", "converted", "postprocess"):
        (d / sub).mkdir(parents=True)
    (d / "experiment.yaml").write_text(EXPERIMENT_YAML)
    (d / "analysis" / "notes.txt").write_text("hello")
    return str(d)


@pytest.fixture
def empty_dir(tmp_path):
    """A directory with no experiment.yaml — should be skipped."""
    d = tmp_path / "not-an-experiment"
    d.mkdir()
    return str(d)


ABORTED_YAML = textwrap.dedent(
    """
    eid: EXP-ABORTED
    name: aborted-before-first-measurement
    created: 2026-01-01 00:00:00.000000
    syspermastate: {hostname: testbox}
    """
).strip()


@pytest.fixture
def aborted_dir(tmp_path):
    """A real-world shape: run aborted before any results/events were written."""
    d = tmp_path / "EXP-ABORTED"
    d.mkdir()
    (d / "experiment.yaml").write_text(ABORTED_YAML)
    return str(d)


@pytest.fixture
def damaged_dir(tmp_path):
    """A zero-byte experiment.yaml — five of these exist in the real corpus."""
    d = tmp_path / "EXP-DAMAGED"
    d.mkdir()
    (d / "experiment.yaml").write_text("")
    return str(d)


TAGGED_YAML = """
eid: EXP-TAGGED
name: numpy-payloads
created: 2026-01-01 00:00:00.000000
syspermastate: {hostname: testbox}
results:
- eid: EXP-TAGGED
  scopeid: scope-a
  exptime: 1.0
  measureid: stream-1
  measureidx: 0
  type: measurement
  counts: !!python/tuple [54, 49, 36]
  density: !!python/object/apply:numpy._core.multiarray.scalar
  - !!python/object/apply:numpy.dtype
    args: [f8, false]
    state: !!python/tuple [3, <, null, null, null, -1, -1, 0]
  - !!binary |
    AAAAgFDzFUE=
events:
- eid: EXP-TAGGED
  scopeid: scope-a
  exptime: 0.5
  type: measurement_stream
  name: cell_counts
  measureid: stream-1
  measurements: [counts, density]
""".strip()


UNKNOWN_TAG_YAML = """
eid: EXP-UNKNOWN
name: unresolvable-payload
created: 2026-01-01 00:00:00.000000
syspermastate: {hostname: testbox}
results:
- eid: EXP-UNKNOWN
  exptime: 1.0
  measureid: stream-1
  type: measurement
  gadget: !!python/object/apply:definitely_not_a_module.Thing [1, 2]
events: []
""".strip()


@pytest.fixture
def tagged_dir(tmp_path):
    """Payloads serialised through numpy — a tuple and a numpy float64."""
    d = tmp_path / "EXP-TAGGED"
    d.mkdir()
    (d / "experiment.yaml").write_text(TAGGED_YAML)
    return str(d)


@pytest.fixture
def unknown_tag_dir(tmp_path):
    """A tag whose module cannot be imported here."""
    d = tmp_path / "EXP-UNKNOWN"
    d.mkdir()
    (d / "experiment.yaml").write_text(UNKNOWN_TAG_YAML)
    return str(d)
