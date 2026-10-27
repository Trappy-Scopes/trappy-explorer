"""Tests for the experiment report renderer."""

import os
import shutil
import textwrap

import pytest

from explorer import ExpExplorer, ExpReport
from explorer import report as report_module


HAS_PANDOC = shutil.which("pandoc") is not None
HAS_LATEX = any(shutil.which(e) for e in ("xelatex", "pdflatex"))


MONITOR_YAML = textwrap.dedent(
    """
    eid: EXP-MON
    name: monitors-and-pipes
    created: 2026-01-01 00:00:00.000000
    results:
      - eid: EXP-MON
        exptime: 10.0
        type: measurement
        measureid: m-1
        measureidx: 0
        counts: 12
        density: 1500.0
        label: raw
    events:
      - eid: EXP-MON
        exptime: 1.0
        type: measurement_stream
        name: cell_counts
        measureid: m-1
        measurements: [counts]
        monitors: [density, label]
        detections: [blob]
      - eid: EXP-MON
        exptime: 2.0
        type: user_note
        note: "density before | after separation"
    """
).strip()


@pytest.fixture
def monitor_dir(tmp_path):
    """A stream that declares payload columns via `monitors`/`detections` too."""
    d = tmp_path / "EXP-MON"
    d.mkdir()
    (d / "experiment.yaml").write_text(MONITOR_YAML)
    return str(d)


def _report(path, **kwargs):
    return ExpReport(ExpExplorer([path], **kwargs))


class TestMarkdown:
    def test_has_every_section(self, experiment_dir):
        md = _report(experiment_dir).markdown()

        for heading in ("# Experiment Report", "## Experiment notebook",
                        "## Event summary", "## Measurement streams",
                        "## Appendix", "### Event log"):
            assert heading in md

    def test_preamble_carries_metadata(self, experiment_dir):
        md = _report(experiment_dir).markdown()

        assert "**eid:** EXP-0001" in md
        assert experiment_dir in md
        assert "legacy safemode" in md

    def test_notes_are_the_notebook(self, experiment_dir):
        md = _report(experiment_dir).markdown()

        assert "started the run" in md.split("## Experiment notebook")[1]

    def test_stream_name_is_joined_onto_results(self, experiment_dir):
        """Results carry only a measureid; the name comes off the stream event."""
        rep = _report(experiment_dir)

        assert rep.stream_map() == {"m-temp": "temperature"}
        assert list(rep.results_frame()["stream"]) == ["temperature", "temperature"]

        table = rep.markdown().split("### temperature")[1].split("\n## ")[0]
        assert "21.5" in table and "21.7" in table

    def test_summary_counts_events_by_type(self, experiment_dir):
        md = _report(experiment_dir).markdown()

        summary = md.split("## Event summary")[1].split("\n## ")[0]
        assert "Measurements" in summary and "Sessions" in summary
        assert "user_note" in summary


class TestPayloadColumns:
    def test_monitors_and_detections_count_as_declared(self, monitor_dir):
        rep = _report(monitor_dir)
        stream = rep.stream_events()[0]

        assert rep.declared_columns(stream) == ["counts", "density", "label", "blob"]

    def test_undeclared_spine_columns_are_not_shown_as_payload(self, monitor_dir):
        md = _report(monitor_dir).markdown()
        table = md.split("### cell_counts")[1].split("\n## ")[0]

        assert "counts" in table and "density" in table
        assert "machinetime" not in table and "scriptid" not in table

    def test_declared_but_never_written_is_reported(self, monitor_dir):
        md = _report(monitor_dir).markdown()

        # `blob` is declared as a detection but no row carries it
        assert "Declared but empty: blob" in md
        assert "never wrote them" in md or "dropped by the parse" in md


class TestParseMode:
    def test_legacy_mode_says_values_were_dropped(self, tagged_dir):
        md = _report(tagged_dir).markdown()

        assert "legacy safemode" in md
        assert "Declared but empty: counts, density" in md
        assert "dropped by the parse" in md

    def test_extended_parse_recovers_the_values(self, tagged_dir):
        md = _report(tagged_dir, extended_parse=True).markdown()

        assert "extended_parse" in md
        assert "Declared but empty" not in md
        assert "(54, 49, 36)" in md
        assert "359636" in md


class TestRobustness:
    def test_aborted_experiment_renders(self, aborted_dir):
        """A run with no results/events must still produce a report."""
        md = _report(aborted_dir).markdown()

        assert "EXP-ABORTED" in md
        assert "## Measurement streams" not in md      # it declared none
        assert "_No notes were recorded._" in md

    def test_pipes_in_notes_do_not_break_the_table(self, monitor_dir):
        md = _report(monitor_dir).markdown()

        assert r"density before \| after separation" in md

    def test_selects_experiment_by_eid_and_by_index(self, experiment_dir, tagged_dir):
        exp = ExpExplorer([experiment_dir, tagged_dir])

        assert ExpReport(exp, 0).eid == "EXP-0001"
        assert ExpReport(exp, "EXP-TAGGED").eid == "EXP-TAGGED"

    def test_repr_reports_size_and_mode(self, experiment_dir):
        assert "2 measurements" in repr(_report(experiment_dir))
        assert "[legacy]" in repr(_report(experiment_dir))


class TestAttachments:
    def test_paragraph_and_image_land_in_the_appendix(self, experiment_dir, tmp_path):
        img = tmp_path / "plot.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")
        rep = _report(experiment_dir)
        rep.add_paragraph("An extra remark.")
        rep.add_image(str(img), caption="A plot")
        md = rep.markdown()

        assert "## Appendix" in md
        assert "An extra remark." in md
        assert "![A plot](" in md
        # pandoc numbers figures itself; a "Figure N:" prefix here double-captions
        assert "Figure 1: A plot" not in md

    def test_missing_image_raises(self, experiment_dir):
        with pytest.raises(FileNotFoundError):
            _report(experiment_dir).add_image("/definitely/not/here.png")

    def test_attachments_get_their_own_heading(self, experiment_dir):
        md = _report(experiment_dir).markdown()

        assert "## Appendix" in md            # the event log always lives there
        assert "### Attachments" not in md    # but only when something is attached


class TestOutputs:
    def test_writes_markdown(self, experiment_dir, tmp_path):
        out = _report(experiment_dir).to_markdown(str(tmp_path / "r.md"))

        assert os.path.exists(out)
        assert "# Experiment Report" in open(out).read()

    def test_defaults_to_the_analysis_dir(self, experiment_dir):
        out = _report(experiment_dir).to_markdown()

        assert out == os.path.join(experiment_dir, "analysis", "report.md")
        assert os.path.exists(out)

    def test_writes_html(self, experiment_dir, tmp_path):
        out = _report(experiment_dir).to_html(str(tmp_path / "r.html"))
        html = open(out).read()

        assert html.startswith("<!doctype html>")
        assert "<table>" in html and "started the run" in html

    def test_pdf_without_pandoc_fails_loudly(self, experiment_dir, tmp_path, monkeypatch):
        monkeypatch.setattr(report_module.shutil, "which", lambda name: None)

        with pytest.raises(RuntimeError, match="pandoc"):
            _report(experiment_dir).to_pdf(str(tmp_path / "r.pdf"), engine="pandoc")

    def test_pdf_without_latex_fails_loudly(self, experiment_dir, tmp_path, monkeypatch):
        monkeypatch.setattr(report_module.shutil, "which",
                            lambda name: "/bin/pandoc" if name == "pandoc" else None)

        with pytest.raises(RuntimeError, match="LaTeX"):
            _report(experiment_dir).to_pdf(str(tmp_path / "r.pdf"), engine="pandoc")

    @pytest.mark.skipif(not (HAS_PANDOC and HAS_LATEX),
                        reason="needs the pandoc binary and a LaTeX engine")
    def test_writes_pdf(self, experiment_dir, tmp_path):
        out = _report(experiment_dir).to_pdf(str(tmp_path / "r.pdf"), engine="pandoc")

        assert os.path.exists(out)
        assert open(out, "rb").read(5) == b"%PDF-"

    @pytest.mark.skipif(not (HAS_PANDOC and HAS_LATEX),
                        reason="needs the pandoc binary and a LaTeX engine")
    def test_latex_special_characters_survive(self, tmp_path):
        """An eid or title with `_` or `&` must not blow up the LaTeX run."""
        d = tmp_path / "EXP_ODD"
        d.mkdir()
        (d / "experiment.yaml").write_text(
            "eid: EXP_ODD&1\nname: 100% cells_a & b\nresults: []\nevents: []\n"
        )
        out = _report(str(d)).to_pdf(str(tmp_path / "odd.pdf"), engine="pandoc")

        assert open(out, "rb").read(5) == b"%PDF-"


class TestTheme:
    """The header/footer visuals carried over from the original report.py."""

    def test_uses_the_original_colour_and_band_heights(self):
        assert report_module.THEME_RGB == (15, 20, 25)
        assert report_module.HEADER_BAND_PTS == 75
        assert report_module.FOOTER_BAND_PTS == 15

    def test_header_carries_eid_and_stamp_but_not_the_name(self, experiment_dir):
        """The band shows the logos; the experiment name lives in the body."""
        tex = _report(experiment_dir)._latex_header()

        assert "{RGB}{15,20,25}" in tex
        assert r"\thepage" in tex
        assert "Autogenerated:" in tex
        assert "eid: EXP-0001" in tex
        assert "Metaexperiment" not in tex

    def test_class_page_number_is_suppressed(self, experiment_dir):
        """The band prints the page number, so the class footer must be empty."""
        tex = _report(experiment_dir)._latex_header()

        assert r"\pagestyle{fancy}" in tex
        assert r"\fancypagestyle{plain}" in tex

    def test_eid_is_escaped_for_latex(self, tmp_path):
        d = tmp_path / "odd"
        d.mkdir()
        (d / "experiment.yaml").write_text(
            "eid: A_B&C\nname: 100% x\nresults: []\nevents: []\n"
        )
        tex = _report(str(d))._latex_header()

        assert r"A\_B\&C" in tex

    def test_only_left_anchors_are_used(self, experiment_dir):
        """eso-pic has no \AtPageUpperRight; using it would fail at LaTeX time."""
        tex = _report(experiment_dir)._latex_header()
        directives = [ln for ln in tex.splitlines() if not ln.lstrip().startswith("%")]

        assert not any("AtPageUpperRight" in ln for ln in directives)


class TestLogos:
    """Both logos ride in the header band, and both are packaged with it."""

    def test_assets_are_packaged(self):
        assert os.path.exists(report_module.DEFAULT_WORDMARK)
        assert os.path.exists(report_module.DEFAULT_ICON)

    def test_wordmark_background_matches_the_band(self):
        """
        The wallpaper effect depends on this exactly: the wordmark is drawn on
        the band colour, so it has no visible edge. Re-vendoring the asset with
        a different background (or an embedded colour profile) breaks it.
        """
        from PIL import Image

        im = Image.open(report_module.DEFAULT_WORDMARK)
        assert im.mode == "RGB"                       # no alpha, no profile
        assert im.info.get("icc_profile") is None
        assert "srgb" not in im.info
        assert im.getpixel((0, 0)) == report_module.THEME_RGB

    def test_both_are_drawn_by_default(self, experiment_dir):
        tex = _report(experiment_dir)._latex_header()

        assert tex.count("includegraphics") == 2
        assert "trappyscopes.png" in tex and "tsicon.png" in tex

    def test_each_can_be_turned_off(self, experiment_dir):
        exp = ExpExplorer([experiment_dir])

        assert ExpReport(exp, logo=False)._latex_header().count("includegraphics") == 1
        assert ExpReport(exp, icon=False)._latex_header().count("includegraphics") == 1
        assert ExpReport(exp, logo=False, icon=False)._latex_header().count(
            "includegraphics") == 0

    def test_a_missing_path_is_skipped_not_fatal(self, experiment_dir):
        tex = _report(experiment_dir)
        tex.logo = "/definitely/not/here.png"

        assert tex._latex_header().count("includegraphics") == 1

    def test_override_with_a_custom_image(self, experiment_dir, tmp_path):
        logo = tmp_path / "mine.png"
        logo.write_bytes(b"\x89PNG\r\n\x1a\n")
        exp = ExpExplorer([experiment_dir])

        assert "mine.png" in ExpReport(exp, logo=str(logo))._latex_header()


DAY_YAML = textwrap.dedent(
    """
    eid: DAY-1
    name: Metaexperiment_test_day
    created: 2026-01-01 00:00:00.000000
    results:
      - eid: DAY-1
        exptime: 10.0
        type: measurement
        measureid: cc-1
        label: Gptx6-4Aug26
        sep: false
        density: 1175000.0
        df: 2
    events:
      - eid: DAY-1
        exptime: 1.0
        type: measurement_stream
        name: cell_counts
        measureid: cc-1
        measurements: [counts]
        monitors: [density, df, label, sep]
      - eid: DAY-1
        exptime: 2.0
        type: user_note
        note: Culture clean and motile.
    """
).strip()

RUN_YAML = textwrap.dedent(
    """
    eid: RUN-1
    name: M8__YB__longterm_traj__RUN-1
    created: 2026-01-01 00:00:00.000000
    devices:
      blue: {config: {}, params: {calib_counts: [1, 2], calib_dt: 0.1}}
      beacon: {config: {}, params: null}
    processors: [node]
    results:
      - {eid: RUN-1, exptime: 10.0, type: measurement, measureid: acq-1, measureidx: 0, acq: split_0.mjpeg}
    events:
      - {eid: RUN-1, exptime: 1.0, type: measurement_stream, name: acq, measureid: acq-1, measurements: [acq]}
      - {eid: RUN-1, exptime: 2.0, type: filename_created, filename: /data/trapped/sample_video.mjpeg}
      - {eid: RUN-1, exptime: 3.0, type: payload_version_change, path: /x/y.py, version: 2}
      - {eid: RUN-1, exptime: 4.0, type: open_trap}
    """
).strip()


@pytest.fixture
def day_tree(tmp_path):
    """A day-organised tree: a run with its Metaexperiment as a sibling."""
    day = tmp_path / "2026_01_01"
    run = day / "M8__YB__longterm_traj__RUN-1"
    meta = day / "Metaexperiment_2026_01_01_test_DAY-1"
    run.mkdir(parents=True)
    meta.mkdir(parents=True)
    (run / "experiment.yaml").write_text(RUN_YAML)
    (meta / "experiment.yaml").write_text(DAY_YAML)
    return str(run)


HAS_REPORTLAB = __import__("importlib").util.find_spec("reportlab") is not None


@pytest.mark.skipif(not HAS_REPORTLAB, reason="needs reportlab")
class TestReportLabEngine:
    """The pip-only backend: pure Python, so it works on a Pi."""

    def test_is_the_default_when_available(self, experiment_dir, tmp_path):
        out = _report(experiment_dir).to_pdf(str(tmp_path / "r.pdf"))

        assert open(out, "rb").read(5) == b"%PDF-"

    def test_needs_no_pandoc_or_latex(self, experiment_dir, tmp_path, monkeypatch):
        monkeypatch.setattr(report_module.shutil, "which", lambda name: None)
        out = _report(experiment_dir).to_pdf(str(tmp_path / "r.pdf"), engine="reportlab")

        assert open(out, "rb").read(5) == b"%PDF-"

    def test_renders_tables_images_and_unicode(self, tagged_dir, tmp_path):
        """Em dashes and a figure must survive the Latin-1 core fonts."""
        exp = ExpExplorer([tagged_dir], extended_parse=True)
        rep = ExpReport(exp, outdir=str(tmp_path))
        img = tmp_path / "fig.png"
        try:
            from PIL import Image as PILImage
            PILImage.new("RGB", (40, 30), (200, 220, 140)).save(img)
        except ImportError:
            pytest.skip("needs Pillow")
        rep.add_image(str(img), caption="A figure \u2014 with an em dash")
        out = rep.to_pdf(str(tmp_path / "r.pdf"), engine="reportlab")

        assert os.path.getsize(out) > 1000

    def test_unknown_engine_is_rejected(self, experiment_dir, tmp_path):
        with pytest.raises(ValueError, match="unknown engine"):
            _report(experiment_dir).to_pdf(str(tmp_path / "r.pdf"), engine="latex")


HAS_MPL = __import__("importlib").util.find_spec("matplotlib") is not None




HAS_MPL = __import__("importlib").util.find_spec("matplotlib") is not None


class TestNotebook:
    """Notes come first; a run with none falls back to the day's."""

    def test_pulls_the_day_notes_when_the_run_has_none(self, day_tree):
        section = _report(day_tree).markdown().split(
            "## Experiment notebook")[1].split("\n## ")[0]

        assert "Culture clean and motile." in section
        assert "recorded no notes of its own" in section

    def test_own_notes_win(self, experiment_dir):
        section = _report(experiment_dir).markdown().split(
            "## Experiment notebook")[1].split("\n## ")[0]

        assert "started the run" in section
        assert "recorded no notes of its own" not in section

    def test_comes_straight_after_the_preamble(self, experiment_dir):
        md = _report(experiment_dir).markdown()

        assert md.index("## Experiment notebook") < md.index("## Event summary")

    def test_there_is_no_day_context_section(self, day_tree):
        assert "## Day context" not in _report(day_tree).markdown()


class TestAttributesAndScripts:
    def test_absent_when_there_is_nothing(self, experiment_dir):
        assert "## Experiment attributes" not in _report(experiment_dir).markdown()

    def test_attributes_and_scripts_are_named(self, tmp_path):
        d = tmp_path / "S"
        d.mkdir()
        (d / "experiment.yaml").write_text(
            "eid: S1\nname: s\nattribs: {pump: 3, ml_per_min: 0.5}\nresults: []\n"
            "events:\n"
            "  - {eid: S1, exptime: 1.0, type: payload_added, payload: scripts/run.py,"
            " path: /x/run.py, payload_kind: scripts}\n")
        section = _report(str(d)).markdown().split(
            "## Experiment attributes")[1].split("\n## ")[0]

        assert "ml_per_min" in section and "0.5" in section
        assert "scripts/run.py" in section

    def test_comes_before_the_streams(self, tmp_path):
        d = tmp_path / "S2"
        d.mkdir()
        (d / "experiment.yaml").write_text(
            "eid: S2\nname: s\nattribs: {pump: 3}\nresults: []\n"
            "events:\n  - {eid: S2, exptime: 1.0, type: measurement_stream,"
            " name: x, measureid: m1, measurements: [v]}\n")
        md = _report(str(d)).markdown()

        assert md.index("## Experiment attributes") < md.index("## Measurement streams")


class TestStreamStatistics:
    def test_statistics_follow_the_stream_definition(self, experiment_dir):
        section = _report(experiment_dir).markdown().split(
            "### temperature")[1].split("\n## ")[0]

        for column in ("n", "min", "mean", "median", "max"):
            assert f"| {column}" in section or f" {column} " in section

    def test_no_table_of_raw_rows(self, experiment_dir):
        section = _report(experiment_dir).markdown().split(
            "### temperature")[1].split("\n## ")[0]

        assert "measureidx" not in section

    def test_non_numeric_columns_are_summarised_by_value(self, day_tree):
        section = _report(day_tree).markdown().split("### acq")[1].split("\n## ")[0]

        assert "split_0.mjpeg" in section          # one row, so it is named


class TestDeviceTree:
    def test_draws_every_device_including_bare_ones(self, day_tree):
        tree = _report(day_tree).device_tree()

        assert "devices" in tree and "```" in tree
        assert "blue" in tree and "calib_counts" in tree
        assert "beacon" in tree and "no parameters recorded" in tree

    def test_absent_when_there_is_no_inventory(self, experiment_dir):
        assert _report(experiment_dir).device_tree() == ""


class TestEventLog:
    def test_collapses_types_that_recur_throughout(self, tmp_path):
        d = tmp_path / "R"
        d.mkdir()
        events = "".join(
            f"  - {{eid: R1, exptime: {i * 100.0}, type: cam_acq_finish,"
            f" filename: f{i}.mjpeg}}\n" for i in range(12))
        (d / "experiment.yaml").write_text(
            "eid: R1\nname: r\nresults: []\nevents:\n"
            "  - {eid: R1, exptime: 0.0, type: session, sessionid: s}\n" + events)
        log = _report(str(d)).event_log()

        assert "f0.mjpeg" in log                 # first occurrence kept in place
        assert "f7.mjpeg" not in log             # the rest folded away
        assert "11 more cam_acq_finish" in log
        assert "folded into a single row" in log

    def test_keeps_sporadic_types_in_full(self, day_tree):
        log = _report(day_tree).event_log()

        assert "open_trap" in log
        assert "sample_video.mjpeg" in log

    def test_lives_in_the_appendix(self, experiment_dir):
        md = _report(experiment_dir).markdown()

        assert md.index("## Appendix") < md.index("### Event log")


class TestDataFiles:
    def test_summarises_the_directory_rather_than_listing_it(self, experiment_dir):
        section = _report(experiment_dir).markdown().split("## Data")[1].split("\n## ")[0]

        assert "files," in section and "in total" in section
        assert ".yaml" in section
        assert "experiment.yaml" not in section      # counted, not listed


@pytest.mark.skipif(not HAS_MPL, reason="needs matplotlib")
class TestTimelines:
    def test_event_timeline_is_in_the_appendix(self, day_tree):
        md = _report(day_tree).markdown()

        assert "### Event timeline" in md and "![event timeline](" in md

    def test_sessions_become_spans(self, day_tree):
        rep = _report(day_tree)
        rep.events = [{"type": "session", "exptime": 0.0},
                      {"type": "session", "exptime": 100.0}]
        spans = rep._session_spans()

        assert len(spans) == 2 and spans[0][0] == 0.0

    def test_no_figures_without_plots(self, day_tree):
        exp = ExpExplorer([day_tree])
        rep = ExpReport(exp, plots=False)

        assert rep.events_timeline_figure() is None
        assert rep.spine_figure() is None


DEVICE_YAML = textwrap.dedent(
    """
    eid: DEV-1
    name: devices-of-both-shapes
    created: 2026-01-01 00:00:00.000000
    devices:
      blue: {config: {}, params: {calib_counts: [1, 2], calib_dt: 0.125}}
      cam: {cam: null}
      sample: {name: sample, kind: null, colony: Gptx6, strain: CC2894, media: TAPP51}
      trap: {name: trap, id: AB1, bsa: true, pre-usage: 4}
      optics: {name: optics, magnification: 1.5748}
      cell: {name: cell, no_cells: 2, sizes: {0: 5.9821, 1: 6.1851},
             gauss_fits: {0: {contrast: 0.88187, n_frames: 7}}}
    results: []
    events: []
    """
).strip()


@pytest.fixture
def device_dir(tmp_path):
    d = tmp_path / "DEV-1"
    d.mkdir()
    (d / "experiment.yaml").write_text(DEVICE_YAML)
    return str(d)


class TestPhysicalObjectDevices:
    """`sample`/`trap`/`cell`/`optics` are records, not instruments."""

    def test_sample_metadata_is_rendered(self, device_dir):
        tree = _report(device_dir).device_tree()

        assert "colony: Gptx6" in tree
        assert "strain: CC2894" in tree
        assert "media: TAPP51" in tree
        assert "no parameters recorded" not in tree.split("sample")[1].split("trap")[0]

    def test_trap_and_optics_are_rendered(self, device_dir):
        tree = _report(device_dir).device_tree()

        assert "id: AB1" in tree and "pre-usage: 4" in tree
        assert "magnification: 1.57" in tree        # and rounded

    def test_cell_nested_values_are_expanded_not_counted(self, device_dir):
        tree = _report(device_dir).device_tree()

        assert "5.98" in tree and "6.19" in tree     # sizes, in full
        assert "contrast=0.88" in tree               # nested leaf, inline
        assert "2 entries" not in tree               # never summarised away

    def test_instruments_are_still_summarised(self, device_dir):
        tree = _report(device_dir).device_tree()

        assert "params: calib_counts, calib_dt" in tree

    def test_an_empty_device_says_nothing_was_set(self, device_dir):
        tree = _report(device_dir).device_tree()
        cam = tree.split("cam")[1].split("optics")[0]

        assert "nothing was set" in cam


class TestAnalysisSection:
    """postprocess/ and analysis/ are downstream, counted apart from the data."""

    @staticmethod
    def _tree(tmp_path):
        d = tmp_path / "AN-1"
        for sub in ("postprocess", "analysis", "trapped"):
            (d / sub).mkdir(parents=True)
        (d / "experiment.yaml").write_text(
            "eid: AN-1\nname: a\nresults: []\nevents: []\n")
        (d / "trapped" / "clip.mjpeg").write_bytes(b"x" * 2048)
        (d / "postprocess" / "tracks.hd5").write_bytes(b"y" * 4096)
        (d / "analysis" / "fig.png").write_bytes(b"z" * 512)
        return str(d)

    def test_downstream_folders_get_their_own_section(self, tmp_path):
        md = _report(self._tree(tmp_path)).markdown()
        section = md.split("## Analysis")[1].split("\n## ")[0]

        assert "postprocess" in section and "analysis" in section
        assert "downstream files" in section

    def test_data_excludes_them(self, tmp_path):
        md = _report(self._tree(tmp_path)).markdown()
        data = md.split("## Data")[1].split("\n## ")[0]

        assert "trapped" in data
        assert "postprocess" not in data
        assert "excluding downstream analysis" in data

    def test_analysis_sits_just_before_the_appendix(self, tmp_path):
        md = _report(self._tree(tmp_path)).markdown()

        assert md.index("## Data") < md.index("## Analysis") < md.index("## Appendix")

    def test_absent_when_there_is_no_downstream_output(self, aborted_dir):
        """`experiment_dir` has an analysis/ folder with a file, so use one that doesn't."""
        assert "## Analysis" not in _report(aborted_dir).markdown()


class TestScriptsTable:
    def test_has_no_kind_column_and_names_the_only_version(self, tmp_path):
        d = tmp_path / "S3"
        d.mkdir()
        (d / "experiment.yaml").write_text(
            "eid: S3\nname: s\nresults: []\nevents:\n"
            "  - {eid: S3, exptime: 1.0, type: payload_added, payload: scripts/run.py,"
            " payload_kind: scripts}\n")
        section = _report(str(d)).markdown().split("### Scripts")[1].split("\n## ")[0]

        assert "Kind" not in section
        assert "only version" in section
        assert "scripts/run.py" in section


class TestCadence:
    """Collapsed file-generation runs report how often they fired."""

    def test_collapsed_rows_state_the_interval(self, tmp_path):
        d = tmp_path / "C1"
        d.mkdir()
        events = "".join(
            f"  - {{eid: C1, exptime: {i * 600.0}, type: filename_created,"
            f" filename: split_{i}.mjpeg}}\n" for i in range(12))
        (d / "experiment.yaml").write_text(
            "eid: C1\nname: c\nresults: []\nevents:\n"
            "  - {eid: C1, exptime: 0.0, type: session, sessionid: s}\n" + events)
        log = _report(str(d)).event_log()

        assert "11 more filename_created" in log
        assert "one every 0:10:00" in log

    def test_a_single_occurrence_has_no_cadence(self, experiment_dir):
        assert _report(experiment_dir)._cadence([5.0]) is None

    def test_cadence_is_the_median_gap(self, experiment_dir):
        rep = _report(experiment_dir)

        assert rep._cadence([0.0, 10.0, 20.0, 100.0]) == 10.0


class TestUnloggedFiles:
    """Files the log never announced, placed on the run's own clock."""

    @staticmethod
    def _run(tmp_path):
        d = tmp_path / "UL-1"
        for sub in ("trapped", "checkpoint1", "postprocess"):
            (d / sub).mkdir(parents=True)
        (d / "experiment.yaml").write_text(
            "eid: UL1\nname: u\ncreated: 2026-01-01 00:00:00.000000\nresults: []\n"
            "events:\n"
            "  - {eid: UL1, exptime: 10.0, machinetime: 1000000000000,"
            " type: session, sessionid: s}\n"
            "  - {eid: UL1, exptime: 20.0, machinetime: 1010000000000,"
            " type: cam_acq_vid_mjpeg_tpts, filename: trapped/sample_video.mjpeg}\n")
        # announced
        (d / "trapped" / "sample_video.mjpeg").write_bytes(b"a")
        # never announced: a sidecar, a diagnostic png, and the same basename
        # in a different folder
        (d / "trapped" / "sample_video.tpts").write_bytes(b"b")
        (d / "checkpoint1" / "detected_cells.png").write_bytes(b"c")
        (d / "checkpoint1" / "sample_video.mjpeg").write_bytes(b"d")
        (d / "UL1_2026_01_01__00_10_00__1020000000000__split_0.mjpeg").write_bytes(b"e")
        return str(d)

    def test_finds_files_no_event_names(self, tmp_path):
        names = {n for _, n, _, _, _ in _report(self._run(tmp_path)).unlogged_files()}

        assert "sample_video.tpts" in names
        assert "detected_cells.png" in names
        assert "experiment.yaml" in names
        assert "sample_video.mjpeg" not in {n for n in names if n.endswith("trapped")}

    def test_a_copy_in_another_folder_is_not_hidden_by_basename(self, tmp_path):
        rows = _report(self._run(tmp_path)).unlogged_files()
        checkpoint = [(n, top) for _, n, top, _, _ in rows if top == "checkpoint1"]

        assert ("sample_video.mjpeg", "checkpoint1") in checkpoint

    def test_the_announced_file_is_not_reported(self, tmp_path):
        rows = _report(self._run(tmp_path)).unlogged_files()

        assert ("sample_video.mjpeg", "trapped") not in [
            (n, top) for _, n, top, _, _ in rows]

    def test_a_stamped_filename_is_placed_from_its_nanoseconds(self, tmp_path):
        rows = _report(self._run(tmp_path)).unlogged_files()
        stamped = [r for r in rows if r[1].endswith("split_0.mjpeg")][0]

        # 1020000000000 ns is 20 s after the origin event at exptime 10 s
        assert stamped[4] == "filename"
        assert abs(stamped[0] - 30.0) < 0.01

    def test_summary_groups_by_folder(self, tmp_path):
        table = _report(self._run(tmp_path)).unlogged_summary()

        assert "Folder" in table and "checkpoint1" in table


class TestScriptsAppearTwice:
    """A payload_added event and the vendored copy on disk are both kept."""

    @staticmethod
    def _run(tmp_path):
        d = tmp_path / "SP-1"
        (d / "scripts").mkdir(parents=True)
        (d / "experiment.yaml").write_text(
            "eid: SP1\nname: s\ncreated: 2026-01-01 00:00:00.000000\nresults: []\n"
            "events:\n"
            "  - {eid: SP1, exptime: 5.0, type: payload_added,"
            " payload: scripts/run.py, path: /home/src/run.py, payload_kind: scripts}\n")
        (d / "scripts" / "run.py").write_bytes(b"print()")
        return str(d)

    def test_the_vendored_copy_still_counts_as_unlogged(self, tmp_path):
        rows = _report(self._run(tmp_path)).unlogged_files()

        assert "run.py" in {n for _, n, _, _, _ in rows}

    def test_both_the_event_and_the_file_appear_in_the_log(self, tmp_path):
        log = _report(self._run(tmp_path)).event_log()

        assert "payload_added" in log          # the event, at its exptime
        assert "scripts/run.py · from mtime" in log   # the file, at its mtime


class TestNestedExperiments:
    """A crashed run absorbed into its restart must not be counted twice."""

    @staticmethod
    def _nest(tmp_path):
        outer = tmp_path / "OUTER"
        inner = outer / "INNER"
        inner.mkdir(parents=True)
        (outer / "experiment.yaml").write_text(
            "eid: OUT1\nname: outer\nresults: []\nevents: []\n")
        (inner / "experiment.yaml").write_text(
            "eid: IN1\nname: inner\nresults: []\nevents: []\n")
        (outer / "video.mjpeg").write_bytes(b"x" * 4096)
        (inner / "video.mjpeg").write_bytes(b"y" * 1024)
        return str(outer), str(inner)

    def test_outer_census_excludes_the_nested_experiment(self, tmp_path):
        outer, _ = self._nest(tmp_path)
        census = _report(outer)._file_census()
        names = [top for _, top, _ in census]

        assert "INNER" not in names
        assert sum(size for _, _, size in census) == 4096 + len(
            "eid: OUT1\nname: outer\nresults: []\nevents: []\n")

    def test_the_nested_run_reports_its_own_files(self, tmp_path):
        _, inner = self._nest(tmp_path)
        census = _report(inner)._file_census()

        assert any(size == 1024 for _, _, size in census)

    def test_unlogged_scan_also_stops_at_the_boundary(self, tmp_path):
        outer, _ = self._nest(tmp_path)
        tops = {top for _, _, top, _, _ in _report(outer).unlogged_files()}

        assert "INNER" not in tops


class TestJunkFiles:
    """OS bookkeeping files are not experiment data."""

    def test_ds_store_and_friends_are_excluded(self, tmp_path):
        d = tmp_path / "J1"
        d.mkdir()
        (d / "experiment.yaml").write_text("eid: J1\nname: j\nresults: []\nevents: []\n")
        (d / "video.mjpeg").write_bytes(b"x")
        for junk in (".DS_Store", "._video.mjpeg", "Thumbs.db", "desktop.ini"):
            (d / junk).write_bytes(b"y")
        rep = _report(str(d))
        census = {n for n, _, _ in [(f, t, s) for f, t, s in rep._file_census()]}
        names = {n for _, n, _, _, _ in rep.unlogged_files()}

        assert ".DS_Store" not in names and "._video.mjpeg" not in names
        assert "Thumbs.db" not in names and "desktop.ini" not in names
        assert "video.mjpeg" in names

    def test_is_junk_is_explicit(self):
        assert report_module.is_junk(".DS_Store")
        assert report_module.is_junk("._resource")
        assert not report_module.is_junk("split_0.mjpeg")
