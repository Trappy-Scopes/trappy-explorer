"""
Report generation for a Trappy-Scopes experiment.

Reads an :class:`explorer.legacy.ExpExplorer` by *composition* -- you hand it an
explorer and an eid, and it renders that experiment::

    from explorer import ExpExplorer, ExpReport

    exp = ExpExplorer(["/path/to/experiment-dir"], extended_parse=True)
    rep = ExpReport(exp)                 # first experiment; or ExpReport(exp, eid)
    print(rep.markdown())                # the report, as text
    rep.to_markdown("/tmp/report.md")
    rep.to_html("/tmp/report.html")
    rep.to_pdf("/tmp/report.pdf")        # needs pandoc + a LaTeX engine

Rewritten on 2026-09-15 from the version moved out of trappyscopes'
`expframework/report.py`. The original was a mixin on `Experiment` and could not
run standalone; beyond that it had three faults that had to be designed out
rather than patched:

  1. It imported `core.utilities.fluff` and `core.bookkeeping.session.Session` --
     trappyscopes internals that do not exist in this package.
  2. `generate_report()` rendered markdown -> HTML -> RML and then fed the RML to
     a reportlab `Paragraph`. RML is the input language of `rlextra`'s commercial
     RML2PDF (cf. the commented-out `rlextra` imports it fell back from); the
     open-source reportlab cannot consume it, so that path could only ever emit
     markup as literal text.
  3. `generate_()` then called pypandoc and wrote over the reportlab output, so
     the two backends raced and the pandoc one always won.

So the reportlab/html2rml stack is gone and pandoc -- the backend that was
actually doing the work -- is the only one left. Markdown is the canonical
output and needs nothing beyond pandas; HTML needs `markdown-it-py`; PDF needs
the `pandoc` binary and a LaTeX engine.

The *visual* theme of the original is kept: dark RGB(15, 20, 25) bands across the
top and foot of every page, drawn full-bleed via `eso-pic` rather than onto a
reportlab canvas, with the generation timestamp, page number and eid in white at
the foot. The body keeps pandoc's own typography.

The header band carries the two logos rather than any text. That colour is not a
coincidence: it is the background `trappyscopes.png` is drawn on, and `tsicon.png`
is transparent, so neither shows an edge and the band reads as a single surface.
The icon's flagella are white, so it is legible only on the dark band -- on a
white page it would show as a bare green ellipse.
The vendored copies are flattened to plain RGB with their colour metadata
stripped -- with the PNG's `srgb` chunk left in, a viewer colour-manages the image
but not the band fill behind it and the wordmark appears as a lighter rectangle.

Both live in `explorer/assets` and ship with the package. The original pointed at
`.../scope-cli/utilities/trappyscopes.png`, an absolute path outside its own tree
that no longer resolves; `logo=` and `icon=` override them, `False` drops one.
"""

from __future__ import annotations

import datetime
import os
import re
import glob
import shutil
import subprocess
import tempfile

import pandas as pd

from .legacy import ExpExplorer

__all__ = ["ExpReport"]


#: Columns every row carries regardless of what was measured. Everything else on
#: a result row is payload -- the values the stream actually recorded.
#: A table row whose first cell starts with this is a banner: it spans every
#: column, so a long label (a date) cannot widen the narrow column it sits in.
#: Defined here rather than in the renderer so that emitting one costs nothing --
#: `explorer.report` has no rendering dependencies.
SPAN_MARKER = "::span::"

#: Files the operating system leaves behind, which are not experiment data.
#: There is no established library for this -- `pathspec` parses gitignore-style
#: patterns if this ever needs to be user-configurable, but it ships no list of
#: its own, so the set is spelled out here. `legacy.py` already drops `.DS_Store`
#: when globbing experiment directories; this is the same idea one level down.
JUNK_FILES = frozenset({
    ".DS_Store", ".localized", ".AppleDouble", ".apdisk", ".VolumeIcon.icns",
    "Thumbs.db", "ehthumbs.db", "desktop.ini", ".directory",
})
JUNK_PREFIXES = ("._", ".fseventsd", ".Spotlight-V100", ".Trashes",
                 ".TemporaryItems", ".nfs")
JUNK_DIRS = frozenset({"__MACOSX", ".Spotlight-V100", ".fseventsd", ".Trashes",
                       "$RECYCLE.BIN", ".TemporaryItems"})


def is_junk(filename):
    """True for OS bookkeeping files that are not part of the experiment."""
    return filename in JUNK_FILES or filename.startswith(JUNK_PREFIXES)


SPINE_COLUMNS = {
    "eid", "scopeid", "sid", "mid", "scriptid", "attribs", "machinetime",
    "sessiontime", "type", "measureid", "dt", "exptime", "measureidx",
    "success", "stream",
}

#: What to show in the event table's Detail column, per event type. Only
#: `measurement_stream` and `tracked_task` actually carry a `name`: reading
#: `name` for the others left Detail blank on 542 of 2595 events in the corpus
#: (280 `filename_created`, 262 `session`), which read as "nothing happened".
_EVENT_DETAIL = {
    "user_note": ("note",),
    "measurement_stream": ("name",),
    "filename_created": ("filename",),
    "session": ("sessionid",),
    "tracked_task": ("name", "duration", "interrupted"),
    "protocol_checkpoint": ("protocol", "step"),
    "protocol_loaded": ("protocol",),
    "payload_version_change": ("path", "version"),
    "payload_added": ("path",),
    "payload_skip": ("path",),
    "script_run": ("action", "iteration", "no_iterations"),
    "cam_acq_img": ("filename", "tsec"),
    "cam_acq_finish": ("filename",),
    "cam_acq_vid_mjpeg_tpts": ("filename", "tsec", "quality"),
    "cam_acq_vid_mjpeg_tpts_multi": ("filename", "tsec"),
    "open_trap": (),
    "trap_closed": (),
    "delay": ("duration",),
}

#: Acquisition events -- what a long-term run actually produces. The `acq`
#: stream is NOT sufficient on its own: on the 2026_07_02 M4 run it holds zero
#: rows while 82 files were created, so a stream-only view reports that run as
#: having acquired nothing.
_ACQ_EVENTS = ("cam_acq_vid_mjpeg_tpts", "cam_acq_vid_mjpeg_tpts_multi",
               "cam_acq_img", "filename_created")

#: Framework bookkeeping -- 27% of all events in the test corpus. Summarised by
#: count rather than listed, unless `verbose_events=True`.
_NOISY_EVENTS = ("payload_version_change", "payload_added", "payload_skip")

#: Hardware inventory keys. On real runs these carry the configuration that
#: `attribs` carries on the test runs -- all four reference runs have `attribs`
#: of None and a populated inventory.
_INVENTORY_KEYS = ("devices", "actuators", "detectors", "processors")


def _as_float(value):
    """Best-effort float. Log fields are sometimes strings, sometimes None."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_exptime(value):
    """Seconds since experiment start -> `H:MM:SS`."""
    seconds = _as_float(value)
    if seconds is None:
        return ""
    negative = seconds < 0
    hours, rest = divmod(int(abs(seconds)), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{'-' if negative else ''}{hours}:{minutes:02d}:{secs:02d}"


def _fmt_number(value):
    """
    Floats to two decimals, everywhere they are printed.

    Raw repr leaks the binary artefacts of the log -- `3.9100846600000003`,
    `57.15848632812499` -- which say nothing and crowd every table.
    """
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:.2f}"
    try:                                   # numpy scalars from extended_parse
        import numpy as np
        if isinstance(value, np.floating):
            return f"{float(value):.2f}"
    except ImportError:
        pass
    return None


def _cell(value, limit=48):
    """One table cell: single-line, pipe-safe, bounded."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        text = ""
    elif isinstance(value, (list, tuple)):
        text = "(" + ", ".join(_fmt_number(v) or str(v) for v in value) + ")"
    else:
        text = _fmt_number(value) or str(value)
    text = " ".join(text.split())
    if limit and len(text) > limit:
        text = text[: limit - 1] + "…"
    return text.replace("\\", "\\\\").replace("|", "\\|")


def _md_table(headers, rows):
    """A padded GitHub-flavoured pipe table (avoids a `tabulate` dependency)."""
    if not rows:
        return "_No entries._\n"
    headers = [str(h) for h in headers]
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    def line(cells):
        return "| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(cells)) + " |"
    # pandoc sizes a pipe table's columns from this separator row. Give every
    # column two characters of slack, or a value that exactly fills its column
    # ends up touching the next one ("11:42:03Nominal completion.").
    out = [line(headers), "|" + "|".join("-" * (w + 4) for w in widths) + "|"]
    out += [line(row) for row in rows]
    return "\n".join(out) + "\n"


def _tex_escape(text):
    text = str(text)
    for old, new in (
        ("\\", r"\textbackslash{}"), ("&", r"\&"), ("%", r"\%"), ("$", r"\$"),
        ("#", r"\#"), ("_", r"\_"), ("{", r"\{"), ("}", r"\}"),
        ("~", r"\textasciitilde{}"), ("^", r"\textasciicircum{}"),
    ):
        text = text.replace(old, new)
    return text


#: The theme of the original report.py: a dark band across the top of every page
#: carrying the title (and an optional motif on the right), a matching band at
#: the foot with the page number and eid, white text, Helvetica throughout.
#: `Color(15/255, 20/255, 25/255)` there is RGB(15, 20, 25) here.
#: The theme of the original report.py: dark bands across the top and foot of
#: every page, white text, the logos in the header. `Color(15/255, 20/255,
#: 25/255)` there is RGB(15, 20, 25) here -- and it is exactly the background
#: colour of `trappyscopes.png`, so the wordmark merges into the band with no
#: visible edge. `tsicon.png` is transparent and does the same. The icon is the
#: dark-background cell mark: flat body in the brand green RGB(205, 235, 139),
#: black eyespot, and *white* flagella -- which is why it only reads against the
#: band and looks like a bare ellipse on white.
THEME_RGB = (15, 20, 25)
HEADER_BAND_PTS = 75          # ExpReport.header_height_pts in the original
FOOTER_BAND_PTS = 15          # ExpReport.footer_height_pts in the original

#: Packaged with the module on purpose. The original pointed at an absolute path
#: outside its own tree (`.../scope-cli/utilities/trappyscopes.png`) and that
#: path no longer exists, which is exactly the failure mode to avoid.
ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
DEFAULT_WORDMARK = os.path.join(ASSET_DIR, "trappyscopes.png")
DEFAULT_ICON = os.path.join(ASSET_DIR, "tsicon.png")

_LATEX_HEADER = r"""
\usepackage{eso-pic}
\usepackage{graphicx}
\usepackage{xcolor}
\usepackage{underscore}
\usepackage{fancyhdr}
\definecolor{trappydark}{RGB}{__RGB__}

%% The bands carry the page number themselves, so silence the class's own
%% footer -- otherwise every page prints the number twice. `plain` has to be
%% redefined too, since that is the style the opening page uses.
\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0pt}
\renewcommand{\footrulewidth}{0pt}
\fancypagestyle{plain}{\fancyhf{}%
  \renewcommand{\headrulewidth}{0pt}\renewcommand{\footrulewidth}{0pt}}
\sloppy
\setlength{\emergencystretch}{3em}

%% Bands are drawn full-bleed behind every page, the way the original drew them
%% straight onto the canvas. eso-pic places them in one pass, so this does not
%% depend on LaTeX being run twice. Only \AtPageUpperLeft / \AtPageLowerLeft
%% exist -- there is no \AtPageUpperRight -- so anything on the right is placed
%% by right-aligning inside a \makebox the width of the page.
\AddToShipoutPictureBG{%
  \AtPageUpperLeft{\textcolor{trappydark}{\rule[-__HEADPT__pt]{\paperwidth}{__HEADPT__pt}}}%
__LOGOS__%
  \AtPageLowerLeft{\textcolor{trappydark}{\rule{\paperwidth}{__FOOTPT__pt}}}%
  \AtPageLowerLeft{\raisebox{__FOOTMID__pt}[0pt][0pt]{%
      \hspace{1.2cm}\color{white}\sffamily\fontsize{5}{6}\selectfont Autogenerated: __STAMP__}}%
  \AtPageLowerLeft{\raisebox{__FOOTMID__pt}[0pt][0pt]{%
      \makebox[\paperwidth][c]{\color{white}\sffamily\fontsize{7}{8}\selectfont \thepage}}}%
  \AtPageLowerLeft{\raisebox{__FOOTMID__pt}[0pt][0pt]{%
      \makebox[\paperwidth][r]{\color{white}\sffamily\fontsize{7}{8}\selectfont eid: __EID__\hspace{1.2cm}}}}%
}
"""

#: Wordmark, flush left and filling the band: its own background is the band
#: colour, so it reads as part of the band rather than as a pasted-in image.
_WORDMARK = (
    "  \\AtPageUpperLeft{\\raisebox{-__HEADPT__pt}[0pt][0pt]{"
    "\\makebox[\\paperwidth][l]{\\hspace{__PADL__}"
    "\\includegraphics[height=__HEADPT__pt,keepaspectratio]{__WORDMARK__}}}}%\n"
)

#: Icon, vertically centred against the right edge of the same band.
_ICON = (
    "  \\AtPageUpperLeft{\\raisebox{-__ICONMID__pt}[0pt][0pt]{"
    "\\makebox[\\paperwidth][r]{"
    "\\includegraphics[height=__ICONPT__pt,keepaspectratio]{__ICON__}\\hspace{__PADR__}}}}%\n"
)


class ExpReport:
    """
    Render one experiment from an :class:`ExpExplorer` as Markdown, HTML or PDF.

    Parameters
    ----------
    exp : ExpExplorer
        A loaded explorer. Pass ``extended_parse=True`` when constructing it if
        you want serialised numpy payloads resolved -- otherwise measurement
        columns that the log *does* contain will render empty, and the report
        says which mode produced it.
    eid : str or int
        Experiment id, or its index in ``exp.eidlist``. Defaults to the first.
    outdir : str, optional
        Where ``to_*()`` write when given no explicit path. Defaults to the
        experiment's ``analysis/`` directory, created on demand.
    logo, icon : str or False, optional
        Images for the header band -- the wordmark on the left and the icon on
        the right. Both default to the copies packaged in ``explorer/assets``;
        pass a path to override one, or ``False`` to leave it out.
    max_rows : int
        Row cap per measurement table; the rest are summarised as a count.
    max_cell : int
        Character cap per table cell.
    """

    def __init__(self, exp, eid=0, outdir=None, logo=None, icon=None,
                 max_rows=20, max_cell=48, plots=True, day=None,
                 verbose_events=False):
        self.exp = exp
        self.eid = exp.__eid_by_index__(eid=eid)
        self.data = exp.data[self.eid]
        self.metadata = self.data.get("metadata") or {}
        self.events = self.data.get("events") or []
        self.results = self.data.get("results") or []
        self.outdir = outdir or exp.get_analysis_dir(self.eid)
        # None means "the packaged asset"; False means "no logo at all".
        self.logo = DEFAULT_WORDMARK if logo is None else logo
        self.icon = DEFAULT_ICON if icon is None else icon
        self.max_rows = max_rows
        self.max_cell = max_cell
        self.plots = plots
        self.verbose_events = verbose_events
        # None -> look for a Metaexperiment beside this run; False -> don't.
        self.day = day
        self.elements = []          # extra markdown blocks (figures, paragraphs)
        self._figdir = None
        self._figno = 0
        self._census_cache = None
        self._unlogged_cache = None

    # ---------------------------------------------------------------- helpers

    @property
    def title(self):
        return str(self.metadata.get("name") or self.eid)

    @property
    def extended_parse(self):
        return bool(getattr(self.exp, "extended_parse", False))

    @property
    def parse_mode(self):
        """
        What the parse actually did, not what was asked for.

        A single unconstructible value used to send the whole file back to
        safemode, and the report still said `extended_parse` -- claiming numbers
        had been resolved when every numpy payload in it had read as None.
        """
        note = getattr(self.exp, "parse_notes", {}).get(self.eid)
        if not note:
            return "extended_parse" if self.extended_parse else "legacy safemode"
        mode = note.get("mode", "unknown")
        unresolved = note.get("unresolved") or []
        if unresolved:
            mode += f" ({len(unresolved)} tag(s) unresolved)"
        return mode

    def stream_map(self):
        """``{measureid: stream name}`` -- results only identify themselves by id."""
        return {
            e["measureid"]: e.get("name")
            for e in self.events
            if e.get("type") == "measurement_stream" and e.get("measureid")
        }

    def stream_events(self):
        return [e for e in self.events if e.get("type") == "measurement_stream"]

    @staticmethod
    def declared_columns(stream_event):
        """
        Payload columns a stream declares.

        `measurements`, `monitors` and `detections` are mechanically identical in
        the framework -- reading only `measurements` makes a declared column look
        undeclared, which is exactly the wrong answer when someone is asking why
        that column is empty.
        """
        return (
            list(stream_event.get("measurements") or [])
            + list(stream_event.get("monitors") or [])
            + list(stream_event.get("detections") or [])
        )

    def results_frame(self):
        """All results, with a `stream` column joined on from the stream events."""
        df = pd.DataFrame(self.results)
        if not df.empty and "measureid" in df.columns:
            df["stream"] = df["measureid"].map(self.stream_map())
        return df

    def duration_s(self):
        times = [_as_float(r.get("exptime")) for r in self.events + self.results]
        times = [t for t in times if t is not None]
        return max(times) if times else None

    # --------------------------------------------------------------- sections

    def preamble(self):
        meta = self.metadata
        user = meta.get("user") or {}
        sysinfo = meta.get("syspermastate") or {}
        rows = [
            ("Experiment", self.title),
            ("eid", self.eid),
            ("Created", meta.get("created")),
            ("Duration", _fmt_exptime(self.duration_s()) or "unknown"),
            ("User", user.get("user") if isinstance(user, dict) else user),
            ("Host", sysinfo.get("hostname") if isinstance(sysinfo, dict) else sysinfo),
            ("Directory", self.exp.datadir_map.get(self.eid)),
            ("Parse mode", self.parse_mode),
        ]
        body = "\n".join(
            f"- **{k}:** {_cell(v, limit=0)}" for k, v in rows if v not in (None, "")
        )
        note = ""
        if not self.extended_parse:
            note = (
                "\n\n> Loaded in legacy safemode: serialised numpy payloads are read as\n"
                "> empty. Reload with `extended_parse=True` before concluding that a\n"
                "> measurement column recorded nothing.\n"
            )
        return f"# Experiment Report\n\n## {self.title}\n\n{body}\n{note}"

    def summary(self):
        counts = {}
        for event in self.events:
            key = event.get("type") or "unknown"
            counts[key] = counts.get(key, 0) + 1
        rows = [["Measurements", str(len(self.results))],
                ["Events", str(len(self.events))],
                ["Measurement streams", str(len(self.stream_events()))],
                ["Sessions", str(counts.get("session", 0))]]
        rows += [[f"Events \u2014 {k}", str(v)] for k, v in sorted(counts.items())]
        return "## Event summary\n\n" + _md_table(["Item", "Count"], rows)

    def attributes(self):
        """
        What the experiment was set up to do: its parameters and its scripts.

        Together these are the sum of what the run was told to do -- `attribs`
        carries the parameters, the payload events carry the code.
        """
        out = []
        attribs = self.metadata.get("attribs")
        if isinstance(attribs, dict) and attribs:
            rows = [[_cell(k, self.max_cell), _cell(v, 72)]
                    for k, v in sorted(attribs.items())]
            out.append("### Attributes\n")
            out.append(_md_table(["Attribute", "Value"], rows))

        scripts, seen = [], set()
        for event in self.events:
            kind = event.get("type")
            if kind in ("payload_added", "payload_version_change", "payload_skip"):
                name = event.get("payload") or event.get("path")
                if not name or name in seen:
                    continue
                seen.add(name)
                scripts.append([_cell(name, 60),
                                _cell(event.get("version"), 14) or "only version",
                                _cell(event.get("description") or event.get("note"), 60)])
            elif kind == "script_run":
                name = f"{event.get('action')} \u00d7 {event.get('no_iterations')}"
                if name in seen:
                    continue
                seen.add(name)
                scripts.append([_cell(name, 60), "only version",
                                _cell(event.get("description"), 60)])
        if scripts:
            out.append("### Scripts\n")
            out.append(_md_table(["Script", "Version", "Description"], scripts))

        protocols = self.protocols()
        if protocols:
            out.append(protocols)
        if not out:
            return ""
        return "## Experiment attributes\n\n" + "\n".join(out)

    #: Where protocol documents live, and what they look like.
    PROTOCOL_DIRS = ("protocols",)
    PROTOCOL_EXTS = (".md", ".markdown", ".txt")

    @staticmethod
    def _frontmatter(text):
        """YAML frontmatter at the head of a protocol, as a dict."""
        if not text.startswith("---"):
            return {}, text
        end = text.find("\n---", 3)
        if end == -1:
            return {}, text
        head, body = text[3:end], text[end + 4:]
        meta = {}
        for line in head.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip()
        return meta, body.lstrip("\n")

    def protocol_files(self):
        """Protocol documents shipped with the run, newest first."""
        root = self.exp.datadir_map.get(self.eid)
        if not root or not os.path.isdir(root):
            return []
        out = []
        for dirpath, _, filenames in os.walk(root):
            rel = os.path.relpath(dirpath, root)
            top = "." if rel == "." else rel.split(os.sep)[0]
            if top not in self.PROTOCOL_DIRS:
                continue
            for filename in sorted(filenames):
                if not filename.lower().endswith(self.PROTOCOL_EXTS):
                    continue
                path = os.path.join(dirpath, filename)
                try:
                    text = open(path, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                meta, body = self._frontmatter(text)
                title = meta.get("description") or ""
                for line in body.splitlines():
                    if line.startswith("# "):
                        title = title or line[2:].strip()
                        break
                out.append({"file": os.path.relpath(path, root), "title": title,
                            "author": meta.get("author", ""), "body": body,
                            "bytes": len(text)})
        return out

    def protocols(self):
        """
        The protocols the run was executed under, and where they were touched.

        A protocol is the method: without it the report says what happened but
        not what was meant to happen. The documents are small, so the summary
        lives here and the full text goes in the appendix.
        """
        files = self.protocol_files()
        events = [e for e in self.events
                  if e.get("type") in ("protocol_loaded", "protocol_checkpoint")]
        if not files and not events:
            return ""

        out = []
        if files:
            rows = [[_cell(f["file"], 44), _cell(f["title"], 60),
                     _cell(f["author"], 20), self._human(f["bytes"])]
                    for f in files]
            out.append(_md_table(["File", "Title", "Author", "Size"], rows))
        if events:
            rows = [[_fmt_exptime(e.get("exptime")), _cell(e.get("type"), 24),
                     _cell(self._event_detail(e), 60)] for e in events]
            out.append("\n" + _md_table(["Elapsed", "Event", "Detail"], rows))
        return "### Protocols\n\n" + "\n".join(out)

    def protocol_texts(self):
        """Each protocol in full, with its headings demoted to fit the report."""
        blocks = []
        for entry in self.protocol_files():
            lines = [f"#### {entry['file']}", ""]
            for line in entry["body"].splitlines():
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    lines.append("**" + stripped.lstrip("#").strip() + "**")
                else:
                    lines.append(line)
            blocks.append("\n".join(lines).strip())
        if not blocks:
            return ""
        return "### Protocol documents\n\n" + "\n\n".join(blocks) + "\n"

    def streams(self):
        """
        One block per measurement stream: what it is, then what it recorded.

        The statistics belong with the stream, not in a separate section, and a
        table of raw rows is never useful -- 256 `tandh` rows say less than five
        numbers and a plot.
        """
        df = self.results_frame()
        events = self.stream_events()
        if not events:
            return ""

        out = ["## Measurement streams\n"]
        for event in events:
            name = event.get("name")
            measureid = event.get("measureid")
            declared = self.declared_columns(event)
            sub = df[df["measureid"] == measureid] if "measureid" in df.columns else df.iloc[0:0]
            out.append(f"### {name or measureid}\n")
            out.append(_md_table(
                ["measureid", "Declared columns", "Started", "Rows"],
                [[_cell(measureid, 20), _cell(", ".join(declared), 60),
                  _fmt_exptime(event.get("exptime")), str(len(sub))]]))
            if sub.empty:
                out.append("\n_No rows were ever written for this stream._\n")
                continue

            sub = sub.dropna(axis=1, how="all")
            payload = [c for c in sub.columns if c not in SPINE_COLUMNS]
            missing = [c for c in declared if c not in payload]
            stats = self._stream_stats(sub, payload)
            if stats:
                out.append("\n" + stats)
            text = self._stream_values(sub, payload)
            if text:
                out.append("\n" + text)
            figure = self._stream_plot(sub, payload, name or measureid)
            if figure:
                out.append(f"\n![{name or measureid}]({figure})\n")
            if missing:
                why = ("the values were dropped by the parse"
                       if not self.extended_parse else
                       "the stream declared them but never wrote them")
                out.append(f"\n_Declared but empty: {', '.join(missing)} \u2014 {why}._\n")
            out.append("")
        return "\n".join(out)

    def _stream_values(self, sub, payload):
        """Non-numeric payload columns: what values occurred, and how often."""
        rows = []
        for col in payload:
            numeric = pd.to_numeric(sub[col], errors="coerce")
            if numeric.notna().sum() >= 0.6 * len(sub):
                continue                      # numeric: the statistics cover it
            values = sub[col].dropna().astype(str)
            if values.empty:
                continue
            counts = values.value_counts()
            if len(counts) > 6:
                rows.append([_cell(col, self.max_cell),
                             f"{len(counts)} distinct values",
                             _cell(counts.index[0], 52)])
            else:
                rows.append([_cell(col, self.max_cell),
                             ", ".join(f"{v} \u00d7{n}" for v, n in counts.items())[:70],
                             ""])
        if not rows:
            return ""
        return _md_table(["Column", "Values", "Most common"], rows)

    def _find_day_log(self):
        """
        The day-level Metaexperiment this run belongs to, if it can be *proved*.

        In a day-organised tree (`<month>/<date>/{M1..M8, Metaexperiment_*}`) the
        day log is the sibling directory. In a flat archive -- which is what
        `~/experiments` is, with dozens of Metaexperiments in one folder --
        position proves nothing, and taking the first sibling attributes one
        day's culture and density to a run from another year. So the candidate's
        `created` date has to match the run's; no match, no day log.
        """
        if self.day is False:
            return None
        if isinstance(self.day, str):
            return (self.day if os.path.isfile(self.day)
                    else os.path.join(self.day, "experiment.yaml"))
        rundir = self.exp.datadir_map.get(self.eid)
        if not rundir:
            return None
        created = self.metadata.get("created")
        day = created.date() if hasattr(created, "date") else None
        if day is None:
            return None

        parent = os.path.dirname(os.path.abspath(rundir))
        candidates = sorted(glob.glob(os.path.join(parent, "Metaexperiment_*",
                                                   "experiment.yaml")))
        if len(candidates) > 4:
            # A flat archive: narrow by the date in the folder name before
            # parsing dozens of files, then still confirm against `created`.
            stamp = day.strftime("%Y_%m_%d")
            candidates = [c for c in candidates if stamp in c]
        for path in candidates:
            try:
                doc = ExpExplorer.read_yaml(path, extended_parse=False) or {}
            except Exception:
                continue
            other = doc.get("created")
            if hasattr(other, "date") and other.date() == day:
                return path
        return None

    def notebook(self):
        """
        The notebook: what the operator wrote down, in order.

        A per-scope run usually carries none of its own -- all four reference
        runs have zero -- because the notes live in the day-level Metaexperiment
        beside it. Those are pulled in here rather than into a separate section,
        and labelled with where they came from.
        """
        rows = [[_fmt_exptime(e.get("exptime")), _cell(e.get("note"), limit=0)]
                for e in self.events if e.get("type") == "user_note"]
        if rows:
            return "## Experiment notebook\n\n" + _md_table(["Elapsed", "Note"], rows)

        path = self._find_day_log()
        if path and os.path.isfile(path):
            try:
                doc = ExpExplorer.read_yaml(path, extended_parse=self.extended_parse) or {}
            except Exception:
                doc = {}
            if doc.get("eid") != self.eid:
                rows = [[_fmt_exptime(e.get("exptime")), _cell(e.get("note"), limit=0)]
                        for e in (doc.get("events") or [])
                        if e.get("type") == "user_note"]
                if rows:
                    name = doc.get("name") or os.path.basename(os.path.dirname(path))
                    return ("## Experiment notebook\n\n"
                            f"_This run recorded no notes of its own; these are the day's, "
                            f"from `{name}`._\n\n"
                            + _md_table(["Elapsed", "Note"], rows))
        return "## Experiment notebook\n\n_No notes were recorded._\n"

    def _acquisition_streams(self):
        """Streams whose payload is file names -- rendered under Acquisition."""
        return {e.get("name") for e in self.stream_events()
                if set(self.declared_columns(e)) <= {"acq"} and e.get("name")}

    def _acquisition_items(self):
        """
        Every file the run recorded, from the stream *and* the events.

        The `acq` stream alone is not enough -- one reference run has an empty
        `acq` stream alongside 82 `filename_created` events.
        """
        seen, items = set(), []
        df = self.results_frame()
        if not df.empty and "acq" in df.columns:
            for _, row in df[df["acq"].notna()].iterrows():
                name = str(row["acq"])
                if name not in seen:
                    seen.add(name)
                    items.append((_as_float(row.get("exptime")), name, "acq"))
        for event in self.events:
            if event.get("type") not in _ACQ_EVENTS:
                continue
            name = event.get("filename")
            if not name or name in seen:
                continue
            seen.add(name)
            items.append((_as_float(event.get("exptime")), str(name),
                          event.get("type")))
        items.sort(key=lambda i: (i[0] is None, i[0]))
        return items

    def _task_items(self):
        """tracked_task events as spans; `duration` is seconds, logged at the end."""
        out = []
        for event in self.events:
            if event.get("type") != "tracked_task":
                continue
            end = _as_float(event.get("exptime"))
            span = _as_float(event.get("duration")) or 0.0
            if end is None:
                continue
            out.append((max(0.0, end - span), max(span, 1e-9),
                        str(event.get("name") or "task"),
                        bool(event.get("interrupted"))))
        return out

    def _trap_items(self):
        """open_trap -> trap_closed pairs, in order."""
        spans, opened = [], None
        for event in self.events:
            kind, t = event.get("type"), _as_float(event.get("exptime"))
            if t is None:
                continue
            if kind == "open_trap":
                opened = t
            elif kind == "trap_closed":
                spans.append((opened, t - opened) if opened is not None else (t, 0.0))
                opened = None
        if opened is not None:
            spans.append((opened, (self.duration_s() or opened) - opened))
        return spans

    def _session_spans(self):
        """Sessions as spans: each session event runs until the next, or the end."""
        starts = sorted(t for t in
                        (_as_float(e.get("exptime")) for e in self.events
                         if e.get("type") == "session") if t is not None)
        if not starts:
            return []
        total = self.duration_s() or (starts[-1] + 1.0)
        bounds = starts + [total]
        return [(bounds[i], max(bounds[i + 1] - bounds[i], total * 0.002))
                for i in range(len(starts))]

    def _plt(self):
        if not self.plots:
            return None
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            return plt
        except ImportError:
            return None

    def events_timeline_figure(self):
        """
        Every event as a tick on its own lane, with sessions as spans above.

        Sessions are the only thing here with a duration, so they are drawn as
        bars; everything else is an instant and is drawn as one.
        """
        plt = self._plt()
        if plt is None:
            return None
        H = 3600.0
        by = {}
        for event in self.events:
            t = _as_float(event.get("exptime"))
            if t is None:
                continue
            by.setdefault(event.get("type") or "unknown", []).append(t / H)
        if not by:
            return None
        sessions = self._session_spans()
        if sessions:
            by.pop("session", None)          # drawn as spans, not ticks
        order = sorted(by, key=lambda k: min(by[k]))
        if not order and not sessions:
            return None

        palette = ["#1f6f8b", "#b5651d", "#3f7fae", "#5a9e46", "#8e44ad",
                   "#c0392b", "#16a085", "#b03060", "#b58900", "#7f8c8d",
                   "#2c3e50", "#d35400"]
        rows = (1 if sessions else 0) + len(order)
        fig, ax = plt.subplots(figsize=(9.0, 0.34 * rows + 1.5))
        labels, y = [], 0
        if sessions:
            ax.broken_barh([(a / H, b / H) for a, b in sessions], (y - 0.34, 0.68),
                           facecolors="#5a9e46", edgecolors="white", linewidth=0.4,
                           alpha=0.85)
            labels.append(f"session ({len(sessions)})")
            y += 1
        for i, kind in enumerate(order):
            colour = palette[i % len(palette)]
            ax.plot(by[kind], [y] * len(by[kind]), "|", color=colour,
                    markersize=9, markeredgewidth=1.0)
            labels.append(f"{kind} ({len(by[kind])})")
            y += 1
        for row in range(len(labels)):
            if row % 2 == 0:
                ax.axhspan(row - 0.5, row + 0.5, color="#000000", alpha=0.035, lw=0)
        ax.set_yticks(range(len(labels)))
        ax.set_yticklabels(labels, fontsize=7)
        ax.set_ylim(len(labels) - 0.5, -0.5)
        ax.set_xlabel("elapsed (hours)", fontsize=8)
        ax.grid(axis="x", alpha=0.3, lw=0.5)
        ax.tick_params(labelsize=7)
        for spine in ("top", "right", "left"):
            ax.spines[spine].set_visible(False)
        fig.tight_layout()
        self._figno += 1
        path = os.path.join(self._figure_dir(), f"events_{self._figno}.png")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return path

    def milestones(self):
        """
        One entry per thing worth naming, with the repetitive types folded in.

        A type that recurs throughout the run contributes its first occurrence
        and a single summary entry, so the spine stays readable.
        """
        collapsible = self._high_frequency_types()
        seen, out = set(), []
        for event in self.events:
            t = _as_float(event.get("exptime"))
            kind = event.get("type")
            if t is None:
                continue
            if kind in collapsible:
                if kind in seen:
                    continue
                seen.add(kind)
                rest = [e for e in self.events if e.get("type") == kind and e is not event]
                out.append((t, kind, self._event_detail(event)[:40]))
                times = [x for x in (_as_float(e.get("exptime")) for e in rest)
                         if x is not None]
                if times:
                    cadence = self._cadence(times + [t])
                    every = f", every {_fmt_exptime(cadence)}" if cadence else ""
                    out.append((max(times), kind, f"+{len(rest)} more{every}"))
                continue
            out.append((t, kind, self._event_detail(event)[:40]))
        return sorted(out)

    def spine_figure(self, rotation=45, reach=(30, 22)):
        """
        Milestones on a horizontal spine: bent stem, rotated label.

        Spaced by sequence rather than elapsed time -- events arrive in bursts,
        and a linear time axis piles the setup on top of itself.
        """
        plt = self._plt()
        if plt is None:
            return None
        items = self.milestones()
        if len(items) < 2:
            return None
        palette = {"user_note": "#b03060", "measurement_stream": "#1f6f8b",
                   "open_trap": "#b58900", "trap_closed": "#b58900",
                   "session": "#5a9e46", "cam_acq_img": "#8e44ad"}
        n = len(items)
        dx, dy = reach
        fig, ax = plt.subplots(figsize=(0.92 * n + 2.0, 5.4))
        ax.plot([-0.4, n - 0.6], [0, 0], color="#0f1419", lw=2.2, zorder=2,
                solid_capstyle="round")
        for i, (t, kind, detail) in enumerate(items):
            side = 1 if i % 2 == 0 else -1
            colour = palette.get(kind, "#6b7785")
            label = f"{_fmt_exptime(t)}  {kind}"
            if detail:
                short = os.path.basename(detail) if "/" in detail else detail
                label += f" \u2014 {short[:26]}" + ("\u2026" if len(short) > 26 else "")
            ax.annotate(label, xy=(i, 0), xytext=(side * dx, side * dy),
                        textcoords="offset points", rotation=rotation,
                        rotation_mode="anchor", fontsize=6.6, color="#22303c",
                        ha="left" if side > 0 else "right",
                        va="bottom" if side > 0 else "top", zorder=5,
                        arrowprops=dict(arrowstyle="-", color=colour, lw=0.8,
                                        shrinkA=1, shrinkB=3,
                                        connectionstyle=f"angle,angleA=0,"
                                                        f"angleB={rotation},rad=0"))
            ax.scatter([i], [0], s=26, facecolors="white", edgecolors=colour,
                       linewidths=1.0, zorder=6)
        ax.set_xlim(-0.6, n - 0.4); ax.set_ylim(-1.0, 1.0)
        ax.set_xticks([]); ax.set_yticks([])
        for spine in ("top", "right", "left", "bottom"):
            ax.spines[spine].set_visible(False)
        fig.tight_layout()
        self._figno += 1
        path = os.path.join(self._figure_dir(), f"spine_{self._figno}.png")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return path

    #: Folders holding downstream products rather than the run's own recording.
    ANALYSIS_DIRS = ("postprocess", "analysis")

    def _file_census(self):
        """Walk the experiment directory once: (ext, folder, bytes) per file."""
        if self._census_cache is not None:
            return self._census_cache
        root = self.exp.datadir_map.get(self.eid)
        if not root or not os.path.isdir(root):
            self._census_cache = []
            return self._census_cache
        out = []
        for dirpath, dirnames, filenames in os.walk(root):
            # A crashed run is restarted into a new folder and the old one is
            # absorbed into it. Counting the inner experiment's files as the
            # outer one's would double them, so stop at any nested experiment --
            # it is reported in its own right.
            if dirpath != root and "experiment.yaml" in filenames:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d not in JUNK_DIRS]
            rel = os.path.relpath(dirpath, root)
            top = "." if rel == "." else rel.split(os.sep)[0]
            for filename in filenames:
                if is_junk(filename):
                    continue
                ext = (os.path.splitext(filename)[1] or "(none)").lower()
                try:
                    size = os.path.getsize(os.path.join(dirpath, filename))
                except OSError:
                    size = 0
                out.append((ext, top, size))
        self._census_cache = out
        return out

    @staticmethod
    def _human(n):
        for unit in ("B", "kB", "MB", "GB", "TB"):
            if n < 1024 or unit == "TB":
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
            n /= 1024.0

    def _file_tables(self, census):
        by_ext, folders, total = {}, {}, 0
        for ext, top, size in census:
            c, b = by_ext.get(ext, (0, 0))
            by_ext[ext] = (c + 1, b + size)
            fc, fb = folders.get(top, (0, 0))
            folders[top] = (fc + 1, fb + size)
            total += size
        ext_rows = [[e, str(c), self._human(b)] for e, (c, b) in
                    sorted(by_ext.items(), key=lambda kv: -kv[1][1])]
        folder_rows = [[f, str(c), self._human(b)] for f, (c, b) in
                       sorted(folders.items(), key=lambda kv: -kv[1][1])]
        return len(census), total, ext_rows, folder_rows

    #: Acquisition filenames embed the machine clock:
    #: `<eid>_<date>__<HH_MM_SS>__<nanoseconds>__split_N.mjpeg`. That integer is
    #: the same `machinetime` the events carry, so an unlogged file can be placed
    #: on the run's own timeline exactly rather than guessed at from mtime.
    _STAMPED = re.compile(r"__(\d{2})_(\d{2})_(\d{2})__(\d{13,19})__")

    def _time_origin(self):
        """(machinetime_ns, exptime_s) of the earliest event carrying both."""
        best = None
        for event in self.events:
            ns, t = event.get("machinetime"), _as_float(event.get("exptime"))
            try:
                ns = int(ns)
            except (TypeError, ValueError):
                continue
            if t is None:
                continue
            if best is None or ns < best[0]:
                best = (ns, t)
        return best

    def logged_filenames(self):
        """
        What the events name, as (paths, bare names).

        Matching on basename alone is wrong here: `trapped/`, `checkpoint1/` and
        `checkpoint2/` each hold a `sample_video.mjpeg`, and only one of them is
        ever announced -- so a basename match reports all three as logged and
        hides two unlogged copies. Events that carry a folder are matched on the
        path; only a bare filename falls back to the name.
        """
        paths, bare = set(), set()
        def record(value):
            text = str(value).replace("\\", "/").lstrip("/")
            if "/" in text:
                paths.add(text)
            else:
                bare.add(text)
        for event in self.events:
            # `filename`/`path` are what the run says it wrote. `payload` is
            # deliberately NOT used: a `payload_added` event names the *source*
            # script, not the vendored copy under `scripts/`, so that copy stays
            # in the unlogged list and keeps its own mtime row -- the event and
            # the file are two different facts and both are worth seeing.
            name = event.get("filename") or event.get("path")
            if name:
                record(name)
        df = self.results_frame()
        if not df.empty and "acq" in df.columns:
            for value in df["acq"].dropna():
                record(value)
        return paths, bare

    @staticmethod
    def _is_logged(relpath, paths, bare):
        relpath = relpath.replace(os.sep, "/").lstrip("./")
        if os.path.basename(relpath) in bare:
            return True
        return any(p == relpath or p.endswith("/" + relpath) or relpath.endswith("/" + p)
                   for p in paths)

    def unlogged_files(self):
        """
        Files on disk that no event announced, placed on the run's clock.

        Every acquisition writes a `.mjpeg` and a `.tpts` beside it but logs only
        the video, and everything under `postprocess/` is written after the fact,
        so the log is not a complete inventory of what the run produced. Time
        comes from the nanosecond stamp in the filename where there is one, and
        from the file's mtime otherwise -- which is recorded, because the two are
        not equally trustworthy.
        """
        if self._unlogged_cache is not None:
            return self._unlogged_cache
        root = self.exp.datadir_map.get(self.eid)
        if not root or not os.path.isdir(root):
            self._unlogged_cache = []
            return self._unlogged_cache
        paths, bare = self.logged_filenames()
        origin = self._time_origin()
        created = self.metadata.get("created")

        out = []
        for dirpath, dirnames, filenames in os.walk(root):
            if dirpath != root and "experiment.yaml" in filenames:
                dirnames[:] = []        # a nested experiment reports its own files
                continue
            dirnames[:] = [d for d in dirnames if d not in JUNK_DIRS]
            rel = os.path.relpath(dirpath, root)
            top = "." if rel == "." else rel.split(os.sep)[0]
            for filename in filenames:
                if is_junk(filename):
                    continue
                relpath = os.path.join(rel, filename) if rel != "." else filename
                if self._is_logged(relpath, paths, bare):
                    continue
                match = self._STAMPED.search(filename)
                elapsed, source = None, "mtime"
                if match and origin:
                    ns0, t0 = origin
                    elapsed = (int(match.group(4)) - ns0) / 1e9 + t0
                    source = "filename"
                else:
                    try:
                        stamp = os.path.getmtime(os.path.join(dirpath, filename))
                    except OSError:
                        stamp = None
                    if stamp is not None and hasattr(created, "timestamp"):
                        elapsed = stamp - created.timestamp()
                out.append((elapsed, filename, top,
                            os.path.splitext(filename)[1].lower() or "(none)", source))
        out.sort(key=lambda r: (r[0] is None, r[0]))
        self._unlogged_cache = out
        return out

    def unlogged_summary(self, census_filter=None):
        """A table of unlogged files by type: count, span and cadence."""
        rows, groups = [], {}
        for elapsed, name, top, ext, source in self.unlogged_files():
            if census_filter and not census_filter(top):
                continue
            groups.setdefault((top, ext, source), []).append(elapsed)
        for (top, ext, source), times in sorted(groups.items(),
                                                key=lambda kv: (kv[0][0], -len(kv[1]))):
            known = [t for t in times if t is not None]
            cadence = self._cadence(known)
            rows.append([top, ext, str(len(times)),
                         _fmt_exptime(min(known)) if known else "",
                         _fmt_exptime(max(known)) if known else "",
                         # a zero cadence is a fact (files written together),
                         # not a missing value
                         _fmt_exptime(cadence) if cadence is not None else "",
                         source])
        if not rows:
            return ""
        return _md_table(["Folder", "Type", "Files", "First", "Last", "Cadence",
                          "Time from"], rows)

    def data_files(self):
        """
        What the run itself recorded, summarised from the file tree.

        There is no "acquisition" object in the log -- files are named by camera
        events and `filename_created`, and the directory is the ground truth for
        what exists. Downstream products live in their own section.
        """
        census = [row for row in self._file_census()
                  if row[1] not in self.ANALYSIS_DIRS]
        if not census:
            return ""
        count, total, ext_rows, folder_rows = self._file_tables(census)
        out = ["## Data\n\n",
               f"{count} files, {self._human(total)} in total, "
               "excluding downstream analysis.\n\n",
               _md_table(["Type", "Files", "Size"], ext_rows), "\n",
               _md_table(["Folder", "Files", "Size"], folder_rows)]
        unlogged = self.unlogged_summary(
            census_filter=lambda top: top not in self.ANALYSIS_DIRS)
        if unlogged:
            # The caveats about mtime (negative = predates the run, very large =
            # written afterwards) live in `unlogged_files` rather than on the
            # page: the "Time from" column already says which clock each row used.
            out += ["\n### Files with no event\n\n", unlogged]
        return "".join(out)

    def analysis(self):
        """
        Downstream products: whatever `postprocess/` and `analysis/` hold.

        These are derived from the run rather than recorded by it, so they are
        counted apart from the data -- otherwise a reprocessing run looks like
        it acquired twice as much.
        """
        census = [row for row in self._file_census()
                  if row[1] in self.ANALYSIS_DIRS]
        if not census:
            return ""
        count, total, ext_rows, folder_rows = self._file_tables(census)
        return ("## Analysis\n\n"
                f"{count} downstream files, {self._human(total)} in total.\n\n"
                + _md_table(["Folder", "Files", "Size"], folder_rows) + "\n"
                + _md_table(["Type", "Files", "Size"], ext_rows))

    def _high_frequency_types(self, min_count=8, min_span=0.5):
        """
        Types that recur throughout the run, as opposed to firing in a burst.

        These are the ones worth collapsing in the log: 47 `cam_acq_vid_mjpeg_tpts`
        spread over 23 hours are three pages of noise, while the same type firing
        three times during setup is worth reading.
        """
        total = self.duration_s() or 0.0
        times = {}
        for event in self.events:
            t = _as_float(event.get("exptime"))
            if t is None:
                continue
            times.setdefault(event.get("type"), []).append(t)
        return {k for k, v in times.items()
                if len(v) >= min_count and total and (max(v) - min(v)) / total >= min_span}

    @staticmethod
    def _cadence(times):
        """
        Median interval between successive occurrences.

        A collapsed run of file-generation events is only meaningful if you can
        see how often it fired: "43 files" says nothing, "one every 31:02" says
        the acquisition was on a timer.
        """
        times = sorted(t for t in times if t is not None)
        if len(times) < 2:
            return None
        gaps = sorted(b - a for a, b in zip(times, times[1:]))
        return gaps[len(gaps) // 2]

    def event_log(self):
        """
        Every event in order, with the files that no event announced merged in.

        A diagnostic PNG written at 1:07:58 belongs beside the events of that
        minute, not in a separate list -- that is the only way to see that the
        alignment images land between trap setup and the first checkpoint.
        Downstream `postprocess/` output is left out: it was written days later
        and would swamp the run's own timeline.
        """
        entries = []
        for event in self.events:
            entries.append((_as_float(event.get("exptime")),
                            ("event", event.get("type")),
                            event.get("type") or "unknown",
                            self._event_detail(event)))
        for elapsed, name, top, ext, source in self.unlogged_files():
            if top in self.ANALYSIS_DIRS:
                continue
            where = name if top == "." else f"{top}/{name}"
            entries.append((elapsed, ("file", top, ext), f"file {ext}",
                            f"{where} \u00b7 from {source}"))

        total = self.duration_s() or 0.0
        spans = {}
        for elapsed, key, _, _ in entries:
            if elapsed is not None:
                spans.setdefault(key, []).append(elapsed)
        collapsible = {k for k, v in spans.items()
                       if len(v) >= 8 and total and (max(v) - min(v)) / total >= 0.5}

        entries.sort(key=lambda e: (e[0] is None, e[0]))
        seen, rows = set(), []
        for elapsed, key, kind, detail in entries:
            if key in collapsible:
                if key in seen:
                    continue
                seen.add(key)
                rows.append([_fmt_exptime(elapsed), _cell(kind, self.max_cell),
                             _cell(detail, self.max_cell)])
                rest = [t for t in spans[key] if t != elapsed]
                cadence = self._cadence(spans[key])
                note = f"through {_fmt_exptime(max(spans[key]))}"
                if cadence is not None:
                    note += f", one every {_fmt_exptime(cadence)}"
                rows.append(["", f"\u2026 {len(rest)} more {kind}", note])
                continue
            rows.append([_fmt_exptime(elapsed), _cell(kind, self.max_cell),
                         _cell(detail, self.max_cell)])

        note = ""
        if collapsible:
            names = sorted({k[1] if k[0] == "event" else f"{k[1]}/*{k[2]}"
                            for k in collapsible})
            note = ("\n_Recurring throughout the run and folded into a single row: "
                    f"{', '.join(names)}. Rows marked `file` were found on disk with "
                    "no event announcing them._\n")
        return "### Event log\n\n" + _md_table(["Elapsed", "Type", "Detail"], rows) + note

    #: Devices whose contents are rendered in full rather than summarised.
    #: These four are the ones that are not reliably populated across the
    #: corpus, so a count ("14 entries") hides exactly what needs checking.
    FULL_RENDER = ("cell", "sample", "trap", "optics")

    def _render_mapping(self, mapping, lead, full, depth=0):
        """A dict as tree lines; nested leaf dicts collapse to key=value pairs."""
        lines = []
        items = list(mapping.items())
        for i, (key, value) in enumerate(items):
            last = i == len(items) - 1
            stem = "\u2514\u2500 " if last else "\u251c\u2500 "
            below = lead + ("   " if last else "\u2502  ")
            if isinstance(value, dict) and value:
                if full and depth < 1 and any(isinstance(v, dict) for v in value.values()):
                    lines.append(f"{lead}{stem}{key}")
                    lines += self._render_mapping(value, below, full, depth + 1)
                elif full:
                    inline = ", ".join(
                        f"{k}={_fmt_number(v) or _cell(v, 24)}" for k, v in value.items())
                    lines.append(f"{lead}{stem}{key}: {inline[:160]}")
                else:
                    lines.append(f"{lead}{stem}{key}: {len(value)} entries")
            elif isinstance(value, (list, tuple)):
                if full or len(value) <= 6:
                    lines.append(f"{lead}{stem}{key}: {_cell(value, 160)}")
                else:
                    lines.append(f"{lead}{stem}{key}: {len(value)} values")
            else:
                lines.append(f"{lead}{stem}{key}: {_cell(value, 120 if full else 52)}")
        return lines

    def _device_lines(self, name, spec, lead):
        """
        One device's children.

        Devices come in two shapes. Instruments carry `{config, params}`; the
        physical objects -- `sample`, `trap`, `cell`, `optics` -- are flat
        records of metadata instead, and reading only `params` reported the
        sample that names the culture, strain, media and antibiotics as having
        nothing. Those four are rendered in full: they are the ones that are not
        reliably set, so a summary hides the very thing worth checking.
        """
        full = name in self.FULL_RENDER
        if spec is None:
            return [f"{lead}\u2514\u2500 (not recorded \u2014 the log holds no entry for it)"]
        if not isinstance(spec, dict):
            return [f"{lead}\u2514\u2500 {_cell(spec, 120)}"]

        if ("params" in spec or "config" in spec) and not full:
            groups = [(k, spec.get(k)) for k in ("params", "config")
                      if isinstance(spec.get(k), dict) and spec.get(k)]
            if not groups:
                return [f"{lead}\u2514\u2500 (no parameters recorded)"]
            lines = []
            for i, (label, block) in enumerate(groups):
                stem = "\u2514\u2500 " if i == len(groups) - 1 else "\u251c\u2500 "
                keys = sorted(block)
                shown = ", ".join(keys[:6])
                if len(keys) > 6:
                    shown += f", \u2026 (+{len(keys) - 6})"
                lines.append(f"{lead}{stem}{label}: {shown}")
            return lines

        fields = {k: v for k, v in sorted(spec.items())
                  if k not in ("name", "kind") and v is not None and v != {}}
        if not fields:
            return [f"{lead}\u2514\u2500 (no metadata recorded \u2014 nothing was set)"]
        return self._render_mapping(fields, lead, full)

    def device_tree(self):
        """The hardware and sample inventory, drawn as a tree."""
        blocks = []
        for key in _INVENTORY_KEYS:
            value = self.metadata.get(key)
            if not value:
                continue
            lines = [key]
            if isinstance(value, dict):
                names = sorted(value)
                for i, name in enumerate(names):
                    last = i == len(names) - 1
                    lines.append(("\u2514\u2500 " if last else "\u251c\u2500 ") + str(name))
                    lines += self._device_lines(name, value[name],
                                                "   " if last else "\u2502  ")
            elif isinstance(value, (list, tuple)):
                for i, item in enumerate(value):
                    stem = "\u2514\u2500 " if i == len(value) - 1 else "\u251c\u2500 "
                    lines.append(f"{stem}{item}")
            else:
                lines.append(f"\u2514\u2500 {value}")
            blocks.append("```\n" + "\n".join(lines) + "\n```")
        if not blocks:
            return ""
        return "### Device tree\n\n" + "\n\n".join(blocks) + "\n"

    def _stream_stats(self, sub, payload):
        """min/mean/median/max per numeric column -- 256 rows say more this way."""
        rows = []
        for col in payload:
            values = pd.to_numeric(sub[col], errors="coerce").dropna()
            # Proportional, not absolute: a two-row stream is still numeric, and
            # an absolute floor silently demoted it to a categorical column.
            if values.empty or len(values) < 0.6 * len(sub):
                continue
            rows.append([_cell(col, self.max_cell), str(len(values)),
                         f"{values.min():.2f}", f"{values.mean():.2f}",
                         f"{values.median():.2f}", f"{values.max():.2f}"])
        if not rows:
            return ""
        return _md_table(["Column", "n", "min", "mean", "median", "max"], rows)

    def _stream_plot(self, sub, payload, label):
        """
        Plot the numeric columns against elapsed time.

        matplotlib is an optional extra: without it the tables above still carry
        the data, so a missing import quietly means no figure, not no report.
        """
        if not self.plots:
            return None
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            return None

        hours = pd.to_numeric(sub.get("exptime"), errors="coerce") / 3600.0
        columns = [c for c in payload
                   if pd.to_numeric(sub[c], errors="coerce").notna().sum() >= 8]
        if hours is None or hours.notna().sum() < 8 or not columns:
            return None

        fig, axes = plt.subplots(len(columns), 1, sharex=True,
                                 figsize=(7.2, 1.9 * len(columns)), squeeze=False)
        for ax, col in zip(axes[:, 0], columns):
            ax.plot(hours, pd.to_numeric(sub[col], errors="coerce"), lw=1.0)
            ax.set_ylabel(col)
            ax.grid(alpha=0.3, lw=0.5)
        axes[-1, 0].set_xlabel("elapsed (hours)")
        fig.suptitle(label, fontsize=10)
        fig.tight_layout()

        self._figno += 1
        path = os.path.join(self._figure_dir(), f"stream_{self._figno}.png")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return path

    def _event_detail(self, event):
        """The informative part of an event, whatever its type calls it."""
        keys = _EVENT_DETAIL.get(event.get("type"))
        if keys is None:
            # An unknown type still says something: show its non-spine fields.
            keys = [k for k in event if k not in SPINE_COLUMNS and k != "name"]
        parts = []
        for key in keys:
            value = event.get(key)
            if value not in (None, "", [], {}):
                # a raw duration prints as 3.9100846600000003; note *text* is
                # left alone, since numbers inside prose are the author's
                parts.append(_fmt_number(value) or str(value))
        return " \u00b7 ".join(parts)

    def appendix(self):
        parts = ["## Appendix\n"]
        tree = self.device_tree()
        if tree:
            parts.append(tree)
        texts = self.protocol_texts()
        if texts:
            parts.append(texts)
        parts.append(self.event_log())
        figure = self.events_timeline_figure()
        if figure:
            parts.append(f"### Event timeline\n\n![event timeline]({figure})\n")
        spine = self.spine_figure()
        if spine:
            parts.append(f"### Milestones\n\n![milestones]({spine})\n")
        if self.elements:
            parts.append("### Attachments\n\n" + "\n\n".join(self.elements) + "\n")
        return "\n".join(parts)

    def _figure_dir(self):
        if self._figdir is None:
            self._figdir = os.path.join(self.outdir, "report_figures")
            os.makedirs(self._figdir, exist_ok=True)
        return self._figdir

    def add_paragraph(self, text):
        """Append a paragraph to the appendix."""
        self.elements.append(str(text))
        return self

    def add_image(self, imgpath, caption="", figno=None):
        """Append an existing image to the appendix."""
        if not os.path.exists(imgpath):
            raise FileNotFoundError(imgpath)
        self._figno += 1
        figno = self._figno if figno is None else figno
        # pandoc numbers figures itself, so the alt text is the whole caption --
        # emitting "Figure N:" here too produced "Figure 1: Figure 1: ...".
        label = caption or f"Figure {figno}"
        self.elements.append(f"![{label}]({os.path.abspath(imgpath)})")
        return self

    def add_fig(self, fig, caption="", figno=None):
        """Append a matplotlib figure; it is saved as PNG beside the report."""
        self._figno += 1
        figno = self._figno if figno is None else figno
        path = os.path.join(self._figure_dir(), f"figure_{figno}.png")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        return self.add_image(path, caption=caption, figno=figno)

    # ----------------------------------------------------------------- output

    def markdown(self):
        """The whole report as a Markdown string. No optional dependencies."""
        parts = [
            self.preamble(), self.notebook(), self.summary(),
            self.attributes(), self.streams(), self.data_files(),
            self.analysis(), self.appendix(),
        ]
        return "\n\n".join(p.strip() for p in parts if p and p.strip()) + "\n"

    def _resolve(self, path, suffix):
        if path is None:
            os.makedirs(self.outdir, exist_ok=True)
            return os.path.join(self.outdir, f"report{suffix}")
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        return path

    def to_markdown(self, path=None):
        path = self._resolve(path, ".md")
        with open(path, "w") as handle:
            handle.write(self.markdown())
        return path

    def to_html(self, path=None):
        """Render to standalone HTML. Requires `markdown-it-py`."""
        from markdown_it import MarkdownIt

        path = self._resolve(path, ".html")
        md = MarkdownIt("commonmark").enable("strikethrough").enable("table")
        body = md.render(self.markdown())
        html = (
            "<!doctype html>\n<html><head><meta charset='utf-8'>"
            f"<title>{self.title}</title><style>"
            "body{font-family:-apple-system,Segoe UI,sans-serif;max-width:60rem;"
            "margin:2rem auto;padding:0 1rem;line-height:1.5}"
            "table{border-collapse:collapse;margin:1rem 0;font-size:.9em}"
            "th,td{border:1px solid #ccc;padding:.3rem .5rem;text-align:left}"
            "th{background:#0f1419;color:#fff}"
            "blockquote{border-left:3px solid #f0ad4e;margin:0;padding:.2rem 1rem;"
            "background:#fffbf0}"
            "img{max-width:100%}</style></head><body>\n"
            f"{body}\n</body></html>\n"
        )
        with open(path, "w") as handle:
            handle.write(html)
        return path

    @staticmethod
    def _tex_path(path):
        """A path graphicx will accept: absolute, forward slashes."""
        return os.path.abspath(path).replace("\\", "/")

    def _logo_block(self):
        """
        The header band's contents.

        Both images are the reason the band looks the way it does:
        `trappyscopes.png` is drawn on RGB(15, 20, 25), the band's own colour,
        and `tsicon.png` is transparent -- so neither shows an edge and the band
        reads as one surface rather than as artwork pasted onto a rectangle.
        """
        block = ""
        if self.logo and os.path.exists(self.logo):
            block += (_WORDMARK
                      .replace("__WORDMARK__", self._tex_path(self.logo))
                      .replace("__PADL__", "0.9cm"))
        icon_pts = HEADER_BAND_PTS * 0.62
        if self.icon and os.path.exists(self.icon):
            block += (_ICON
                      .replace("__ICON__", self._tex_path(self.icon))
                      .replace("__ICONPT__", f"{icon_pts:.1f}")
                      .replace("__ICONMID__", f"{(HEADER_BAND_PTS + icon_pts) / 2:.1f}")
                      .replace("__PADR__", "1.2cm"))
        return block

    def _latex_header(self):
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return (
            _LATEX_HEADER
            .replace("__LOGOS__", self._logo_block())
            .replace("__RGB__", ",".join(str(c) for c in THEME_RGB))
            .replace("__HEADPT__", str(HEADER_BAND_PTS))
            .replace("__FOOTPT__", str(FOOTER_BAND_PTS))
            .replace("__FOOTMID__", f"{FOOTER_BAND_PTS * 0.35:.1f}")
            .replace("__EID__", _tex_escape(self.eid))
            .replace("__STAMP__", _tex_escape(stamp))
        )

    def to_pdf(self, path=None, engine=None, latex_engine=None):
        """
        Render to PDF.

        `engine="reportlab"` (the default when reportlab is importable) needs
        nothing but pip -- reportlab is pure Python, so it works on a Raspberry
        Pi scope node. `engine="pandoc"` goes through the `pandoc` binary and a
        LaTeX engine for better typography, and says by name what is missing.
        """
        if engine is None:
            engine = "reportlab" if self._has_reportlab() else "pandoc"
        if engine == "reportlab":
            return self._to_pdf_reportlab(self._resolve(path, ".pdf"))
        if engine != "pandoc":
            raise ValueError(f"unknown engine {engine!r}; use 'reportlab' or 'pandoc'")
        return self._to_pdf_pandoc(self._resolve(path, ".pdf"), latex_engine)

    @staticmethod
    def _has_reportlab():
        try:
            import reportlab  # noqa: F401
            return True
        except ImportError:
            return False

    def _to_pdf_reportlab(self, path):
        try:
            from . import render_reportlab
        except ImportError as err:
            raise RuntimeError(
                "reportlab is required for the default PDF engine "
                "(`pip install reportlab`), or pass engine='pandoc'."
            ) from err
        return render_reportlab.render(self.markdown(), path, {
            "band_rgb": THEME_RGB,
            "header_pts": HEADER_BAND_PTS,
            "footer_pts": FOOTER_BAND_PTS,
            "logo": self.logo if (self.logo and os.path.exists(self.logo)) else None,
            "icon": self.icon if (self.icon and os.path.exists(self.icon)) else None,
            "icon_scale": 0.62,
            "eid": self.eid,
            "title": self.title,
            "stamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    def _to_pdf_pandoc(self, path, engine=None):
        """
        Render via pandoc + LaTeX.

        Both are system dependencies, not pip ones; this raises a RuntimeError
        naming whichever is missing rather than failing obscurely inside pandoc.
        """
        if shutil.which("pandoc") is None:
            raise RuntimeError(
                "pandoc is required for engine='pandoc' and was not found on PATH. "
                "Install it (`brew install pandoc`), or use the reportlab engine."
            )
        if engine is None:
            engine = next((e for e in ("xelatex", "pdflatex") if shutil.which(e)), None)
        if engine is None or shutil.which(engine) is None:
            raise RuntimeError(
                "A LaTeX engine (xelatex or pdflatex) is required for engine='pandoc' "
                "and was not found on PATH. Use the reportlab engine instead."
            )

        tmpdir = tempfile.mkdtemp(prefix="trappy-report-")
        try:
            md_path = os.path.join(tmpdir, "report.md")
            header_path = os.path.join(tmpdir, "header.tex")
            with open(md_path, "w") as handle:
                handle.write(self.markdown())
            with open(header_path, "w") as handle:
                handle.write(self._latex_header())

            cmd = [
                "pandoc", md_path, "-o", path,
                "--from=markdown", f"--pdf-engine={engine}",
                "-H", header_path,
                # the top margin has to clear the header band
                "-V", "geometry:top=3.2cm,bottom=2.2cm,left=2cm,right=2cm",
                "-V", "colorlinks=true",
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(
                    f"pandoc failed ({proc.returncode}):\n{proc.stderr.strip()}")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        return path

    #: Kept for continuity with the original mixin's entry point.
    generate_report = to_pdf

    def __repr__(self):
        mode = "extended_parse" if self.extended_parse else "legacy"
        return (f"<ExpReport eid={self.eid} — {len(self.results)} measurements, "
                f"{len(self.events)} events [{mode}]>")
