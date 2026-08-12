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
