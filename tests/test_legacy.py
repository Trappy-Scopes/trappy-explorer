"""Smoke tests for the frozen legacy explorer."""

import os

from explorer.legacy import ExpExplorer


def test_parses_single_experiment(experiment_dir):
    exp = ExpExplorer([experiment_dir])

    assert exp.eidlist == ["EXP-0001"]
    assert exp.datadir_map["EXP-0001"] == experiment_dir
    assert exp.invexpmap["EXP-0001"].endswith("experiment.yaml")
    assert len(exp.df) == 2
    assert len(exp.df_events) == 2
    assert exp.data["EXP-0001"]["m_streams"] == ["temperature"]
    assert exp.data["EXP-0001"]["metadata"]["operator"] == "tester"
    assert "results" not in exp.data["EXP-0001"]["metadata"]


def test_skips_directories_without_logs(experiment_dir, empty_dir):
    exp = ExpExplorer([empty_dir, experiment_dir])

    assert exp.eidlist == ["EXP-0001"]
    # the eid must map to the real experiment, not to the skipped directory
    assert exp.datadir_map["EXP-0001"] == experiment_dir


def test_directory_helpers(experiment_dir):
    exp = ExpExplorer([experiment_dir])

    assert exp.get_parent_dir(0) == experiment_dir
    assert exp.get_analysis_dir(0) == os.path.join(experiment_dir, "analysis")
    assert exp.get_converted_dir("EXP-0001") == os.path.join(experiment_dir, "converted")
    assert exp.get_postprocess_dir(0) == os.path.join(experiment_dir, "postprocess")


def test_repr_and_tables_do_not_raise(experiment_dir):
    exp = ExpExplorer([experiment_dir])

    assert "EXP-0001" in repr(exp)
    exp.notebook()
    exp.all_events()


def test_filetree(experiment_dir):
    exp = ExpExplorer([experiment_dir])
    tree = exp.data["EXP-0001"]["filetree"]

    assert "experiment.yaml" in tree["."]["files"]
    assert "analysis" in tree["."]


def test_create_superset(experiment_dir, empty_dir):
    parent = os.path.dirname(experiment_dir)
    exp = ExpExplorer.create_superset(parent)

    assert exp.eidlist == ["EXP-0001"]
