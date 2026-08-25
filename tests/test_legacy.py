"""Smoke tests for the frozen legacy explorer."""

import os

import pytest

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


def test_aborted_experiment_does_not_kill_the_batch(experiment_dir, aborted_dir):
    """A run with no results/events keys must not take the whole folder down."""
    exp = ExpExplorer([aborted_dir, experiment_dir])

    assert set(exp.eidlist) == {"EXP-ABORTED", "EXP-0001"}
    assert exp.data["EXP-ABORTED"]["results"] == []
    assert exp.data["EXP-ABORTED"]["events"] == []
    assert exp.data["EXP-ABORTED"]["m_streams"] == []
    assert len(exp.df) == 2  # only the good experiment contributes rows
    assert "EXP-ABORTED" in repr(exp)


def test_damaged_yaml_is_skipped_not_fatal(experiment_dir, damaged_dir):
    """A zero-byte experiment.yaml is reported and skipped."""
    exp = ExpExplorer([damaged_dir, experiment_dir])

    assert exp.eidlist == ["EXP-0001"]
    assert exp.datadir_map["EXP-0001"] == experiment_dir


class TestExtendedParse:
    """extended_parse recovers payloads that legacy safemode drops."""

    def test_legacy_mode_drops_numpy_payloads(self, tagged_dir):
        exp = ExpExplorer([tagged_dir])

        assert exp.extended_parse is False
        row = exp.data["EXP-TAGGED"]["results"][0]
        assert row["counts"] is None
        assert row["density"] is None

    def test_extended_parse_recovers_them(self, tagged_dir):
        exp = ExpExplorer([tagged_dir], extended_parse=True)

        assert exp.extended_parse is True
        row = exp.data["EXP-TAGGED"]["results"][0]
        assert row["counts"] == (54, 49, 36)
        assert row["density"] == pytest.approx(359636.125)

    def test_extended_parse_is_visible_in_repr(self, tagged_dir):
        assert "[extended_parse]" in repr(ExpExplorer([tagged_dir], extended_parse=True))
        assert "[extended_parse]" not in repr(ExpExplorer([tagged_dir]))

    def test_unresolvable_tag_degrades_to_none(self, unknown_tag_dir, capsys):
        """A tag we cannot import must not take the experiment down with it."""
        exp = ExpExplorer([unknown_tag_dir], extended_parse=True)

        assert exp.eidlist == ["EXP-UNKNOWN"]
        assert exp.data["EXP-UNKNOWN"]["results"][0]["gadget"] is None

    def test_create_superset_forwards_the_flag(self, tagged_dir):
        parent = os.path.dirname(tagged_dir)
        exp = ExpExplorer.create_superset(parent, extended_parse=True)

        assert exp.extended_parse is True
        assert exp.data["EXP-TAGGED"]["results"][0]["counts"] == (54, 49, 36)
