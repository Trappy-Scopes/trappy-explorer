"""
A combined report: many experiments in one document.

This is a different shape from the single-experiment report, not a loop over it.
The rule that drives the difference: **`exptime` is not comparable between
experiments.** Two scopes that started eleven minutes apart both call their first
second `0:00:01`, so anything merged across experiments has to be re-keyed on
`dt`, the wall clock. The notebook, the timeline and the overview all do that;
per-experiment statistics stay on each run's own clock, where elapsed is right.

The other difference is that most sections become comparisons. Eight scopes on
one day run the same protocol with the same devices, so the interesting content
is not eight copies of the configuration -- it is what they had in common and
where they diverged.

    from explorer import ExpExplorer
    from explorer.combined import CombinedReport

    exp = ExpExplorer(day_dirs, extended_parse=True)
    CombinedReport(exp, title="2026_08_20").to_pdf("day.pdf")
"""

from __future__ import annotations

import datetime
import os
import re

import pandas as pd

from .report import (_ACQ_EVENTS, SPAN_MARKER, ExpReport, THEME_RGB, HEADER_BAND_PTS, FOOTER_BAND_PTS,
                     DEFAULT_WORDMARK, DEFAULT_ICON, SPINE_COLUMNS,
                     _md_table, _cell, _fmt_exptime, _as_float, _fmt_number)

__all__ = ["CombinedReport"]


class CombinedReport:
    """One document covering every experiment an explorer holds."""

    def __init__(self, exp, eids=None, outdir=None, title=None, plots=True,
                 logo=None, icon=None, max_rows=24, max_cell=48):
        self.exp = exp
        self.eids = list(eids or exp.eidlist)
        if not self.eids:
            raise ValueError(
                "CombinedReport needs at least one loaded experiment; the "
                "explorer parsed none (check the paths are reachable).")
        self.title = title or self._infer_title()
        self.plots = plots
        self.max_rows = max_rows
        self.max_cell = max_cell
        self.logo = DEFAULT_WORDMARK if logo is None else logo
        self.icon = DEFAULT_ICON if icon is None else icon
        self.outdir = outdir or os.path.dirname(
            os.path.abspath(exp.datadir_map[self.eids[0]]))
        self.elements = []
        self._figno = 0
        self._figdir = None
        # one single-experiment report per member, reused for its helpers
        self.members = {eid: ExpReport(exp, eid, outdir=self.outdir, plots=plots)
                        for eid in self.eids}

    # ---------------------------------------------------------------- helpers

    def _infer_title(self):
        days = {self._created(eid).date() for eid in self.eids
                if self._created(eid) is not None}
        if len(days) == 1:
            return days.pop().strftime("%Y_%m_%d")
        if days:
            return f"{min(days)} to {max(days)}"
        return "Combined report"

    def _created(self, eid):
        value = (self.exp.data[eid].get("metadata") or {}).get("created")
        return value if hasattr(value, "date") else None

    def scope(self, eid):
        """The scope that produced a run: M1..M8, MDev, or the folder prefix."""
        meta = self.exp.data[eid].get("metadata") or {}
        for event in self.exp.data[eid].get("events") or []:
            if event.get("scopeid"):
                return str(event["scopeid"])
        name = str(meta.get("name") or os.path.basename(self.exp.datadir_map[eid]))
        return name.split("__")[0].split("_")[0]

    def is_day_log(self, eid):
        name = str((self.exp.data[eid].get("metadata") or {}).get("name") or "")
        return name.lower().startswith(("metaexperiment", "cellcounting"))

    def _wall(self, event):
        value = event.get("dt")
        return value if hasattr(value, "strftime") else None

    def _figure_dir(self):
        if self._figdir is None:
            self._figdir = os.path.join(self.outdir, "combined_figures")
            os.makedirs(self._figdir, exist_ok=True)
        return self._figdir

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

    # ------------------------------------------------------------- structure

    @staticmethod
    def discover(root, recursive=True):
        """
        Every experiment directory under `root`, nested ones included.

        A crashed run is restarted into a new directory and the old one ends up
        *inside* it -- three such pairs exist in the reference corpus (M1
        2026_04_07, M7 and M8 2025_12_04), always the earlier run within the
        later. A non-recursive scan silently reports only the outer run, losing
        everything recorded before the crash, so this walks all the way down.
        """
        found = []
        for dirpath, dirnames, filenames in os.walk(root):
            if "experiment.yaml" in filenames:
                found.append(dirpath)
                if not recursive:
                    dirnames[:] = []
        return sorted(found)

    def nesting(self):
        """{outer eid: [inner eids]} for members that contain one another."""
        paths = {eid: os.path.abspath(self.exp.datadir_map[eid]) for eid in self.eids}
        out = {}
        for outer, outer_path in paths.items():
            for inner, inner_path in paths.items():
                if inner is outer:
                    continue
                if inner_path.startswith(outer_path + os.sep):
                    out.setdefault(outer, []).append(inner)
        return out

    def structure(self):
        """Retained for callers that want the folder layout on its own."""
        lines = []
        nesting = self.nesting()
        inner = {e for group in nesting.values() for e in group}
        for eid in self._ordered():
            if eid in inner:
                continue
            path = os.path.abspath(self.exp.datadir_map[eid])
            lines.append(os.path.basename(path))
            for child in nesting.get(eid, []):
                lines.append("   \u2514\u2500 " + os.path.basename(
                    os.path.abspath(self.exp.datadir_map[child])))
        return "```\n" + "\n".join(lines) + "\n```\n"

    # --------------------------------------------------------------- sections

    def preamble(self):
        created = sorted(c for c in (self._created(e) for e in self.eids)
                         if c is not None)
        hosts = sorted({str(((self.exp.data[e].get("metadata") or {})
                             .get("syspermastate") or {}).get("hostname"))
                        for e in self.eids})
        eids = list(self.eids)
        shown = ", ".join(eids[:12]) + (", \u2026" if len(eids) > 12 else "")
        rows = [
            ("Experiments", f"{len(eids)} ({shown})"),
            ("Scopes", ", ".join(sorted({self.scope(e) for e in self.eids}))),
            ("First started", created[0] if created else None),
            ("Last started", created[-1] if created else None),
            ("Hosts", ", ".join(h for h in hosts if h != "None")),
            ("Parse mode", ", ".join(sorted({self.members[e].parse_mode
                                             for e in self.eids}))),
        ]
        body = "\n".join(f"- **{k}:** {_cell(v, limit=0)}"
                         for k, v in rows if v not in (None, ""))
        return f"# Combined Experiment Report\n\n## {self.title}\n\n{body}"

    def overview(self):
        """
        The cohort at a glance, with where each experiment sits on disk.

        The layout is one line per experiment, not a directory listing: which
        folder holds which run is worth a column, what is inside each folder is
        not -- that belongs to the single-experiment report.
        """
        rows = []
        nesting = self.nesting()
        inner = {e for group in nesting.values() for e in group}
        for eid in self._ordered():
            member = self.members[eid]
            created = self._created(eid)
            rows.append([
                _cell(self.scope(eid), 12), _cell(eid, 12),
                created.strftime("%H:%M:%S") if created else "",
                _fmt_exptime(member.duration_s()),
                str(len(member.results)), str(len(member.events)),
                str(len(member.stream_events())),
                str(sum(1 for e in member.events if e.get("type") == "user_note")),
                "\u21b3" if eid in inner else "",
            ])
        return ("## Experiments overview\n\n"
                + _md_table(["Scope", "eid", "Started", "Duration", "Measurements",
                             "Events", "Streams", "Notes", ""], rows)
                + "\n" + self._folder_tree())

    def _folder_tree(self):
        """Where the experiments sit, one level deep -- no folder contents."""
        paths = {eid: os.path.abspath(self.exp.datadir_map[eid]) for eid in self.eids}
        nesting = self.nesting()
        inner = {e for group in nesting.values() for e in group}
        roots = sorted({os.path.dirname(p) for e, p in paths.items()
                        if e not in inner})
        lines = []
        for root in roots:
            lines.append(root)
            outer = [e for e in self._ordered()
                     if e not in inner and os.path.dirname(paths[e]) == root]
            for i, eid in enumerate(outer):
                last = i == len(outer) - 1
                lines.append(("\u2514\u2500 " if last else "\u251c\u2500 ")
                             + os.path.basename(paths[eid]))
                for child in nesting.get(eid, []):
                    lines.append(("   " if last else "\u2502  ")
                                 + "\u2514\u2500 " + os.path.basename(paths[child]))
        return "```\n" + "\n".join(lines) + "\n```\n"

    def _ordered(self):
        """Members in a stable order: by scope, then by start time."""
        return sorted(self.eids,
                      key=lambda e: (self.scope(e), self._created(e) or
                                     datetime.datetime.min))

    @staticmethod
    def _date_banded(entries, columns):
        """
        Rows with a date row wherever the day changes.

        A run crosses midnight, so every timestamp would otherwise have to carry
        its date -- widening the narrowest column in the table to hold something
        that changes twice. The date becomes a row of its own instead, and the
        clock column holds only a time.
        """
        rows, current = [], None
        for when, cells in entries:
            day = when.date() if when is not None else None
            if day != current:
                current = day
                label = when.strftime("%A %d %B %Y") if when else "(no timestamp)"
                rows.append([f"{SPAN_MARKER}**{label}**"] + [""] * (columns - 1))
            rows.append(cells)
        return rows

    def notebook(self):
        """
        Every note from every experiment, on the wall clock.

        Elapsed time cannot order this table: the scopes start minutes apart, so
        two notes at `0:05:00` are not simultaneous. `dt` is the only clock that
        means the same thing across experiments. Scope and eid are both columns
        because one scope can contribute several experiments.
        """
        rows = []
        for eid in self.eids:
            for event in self.exp.data[eid].get("events") or []:
                if event.get("type") != "user_note":
                    continue
                wall = self._wall(event)
                rows.append((wall,
                             [wall.strftime("%H:%M:%S") if wall else "",
                              _fmt_exptime(event.get("exptime")),
                              _cell(self.scope(eid), 10), _cell(eid, 12),
                              _cell(event.get("note"), limit=0)]))
        if not rows:
            return "## Experiment notebook\n\n_No notes were recorded._\n"
        rows.sort(key=lambda r: (r[0] is None, r[0]))
        return ("## Experiment notebook\n\n"
                + _md_table(["Time", "Elapsed", "Scope ID", "eid", "Note"],
                            self._date_banded(rows, 5)))

    def _column_label(self, eid):
        """
        A column heading that identifies one experiment.

        The scope alone is not enough when a scope contributed several runs --
        four columns all headed `MDev` name nothing.
        """
        scope = self.scope(eid)
        if sum(1 for e in self.eids if self.scope(e) == scope) == 1:
            return scope
        return f"{scope}\u00b7{eid[:6]}"

    def summary(self):
        """Event types down the side, experiments across the top."""
        counts, types = {}, set()
        for eid in self.eids:
            per = {}
            for event in self.exp.data[eid].get("events") or []:
                kind = event.get("type") or "unknown"
                per[kind] = per.get(kind, 0) + 1
                types.add(kind)
            counts[eid] = per
        order = self._ordered()
        header = ["Event type"] + [self._column_label(e) for e in order] + ["Total"]
        rows = []
        for kind in sorted(types):
            per = [counts[e].get(kind, 0) for e in order]
            rows.append([kind] + [str(n) if n else "·" for n in per]
                        + [str(sum(per))])
        totals = [sum(counts[e].values()) for e in order]
        rows.append(["**all events**"] + [str(n) for n in totals] + [str(sum(totals))])
        return "## Event summary\n\n" + _md_table(header, rows)

    @staticmethod
    def _ranges(scopes):
        """M1, M2, M3, M5 -> "M1\u2013M3, M5" -- one row instead of eight."""
        numbered, other = [], []
        for name in scopes:
            match = re.fullmatch(r"([A-Za-z]+)(\d+)", str(name))
            (numbered if match else other).append(
                (match.group(1), int(match.group(2))) if match else name)
        parts = []
        for prefix in sorted({p for p, _ in numbered}):
            nums = sorted(n for p, n in numbered if p == prefix)
            run = [nums[0]]
            for value in nums[1:]:
                if value == run[-1] + 1:
                    run.append(value)
                else:
                    parts.append(f"{prefix}{run[0]}" if len(run) == 1
                                 else f"{prefix}{run[0]}\u2013{prefix}{run[-1]}")
                    run = [value]
            parts.append(f"{prefix}{run[0]}" if len(run) == 1
                         else f"{prefix}{run[0]}\u2013{prefix}{run[-1]}")
        return ", ".join(parts + sorted(other))

    def _group_identical(self, per_scope):
        """{scope: value} -> [(scopes-as-range, value)], commonest first."""
        buckets = {}
        for scope, value in per_scope.items():
            buckets.setdefault(value, []).append(scope)
        return [(self._ranges(sorted(scopes)), value)
                for value, scopes in sorted(buckets.items(),
                                            key=lambda kv: -len(kv[1]))]

    def device_configuration(self):
        """
        One device tree per distinct inventory, and the physical objects as a
        table with identical scopes collapsed onto one row.

        Eight scopes running the same protocol declare the same devices, so
        eight trees would be seven repetitions. What varies -- the sample, the
        trap -- is what gets the space.
        """
        shapes, objects = {}, {}
        for eid in self.eids:
            devices = (self.exp.data[eid].get("metadata") or {}).get("devices")
            if not isinstance(devices, dict):
                continue
            shapes[self.scope(eid)] = tuple(sorted(devices))
            for name, spec in devices.items():
                if not isinstance(spec, dict) or "params" in spec or "config" in spec:
                    continue
                fields = {k: v for k, v in sorted(spec.items())
                          if k not in ("name", "kind") and v is not None}
                if fields:
                    objects.setdefault(name, {})[self.scope(eid)] = ", ".join(
                        f"{k}={_fmt_number(v) or _cell(v, 24)}"
                        for k, v in fields.items())
        if not shapes:
            return ""

        out = ["### Devices\n"]
        for scopes, names in self._group_identical(shapes):
            out.append(f"**{scopes}**\n")
            lines = ["devices"]
            for i, name in enumerate(names):
                stem = "\u2514\u2500 " if i == len(names) - 1 else "\u251c\u2500 "
                lines.append(stem + str(name))
            out.append("```\n" + "\n".join(lines) + "\n```\n")

        for name in sorted(objects):
            rows = [[scopes, _cell(value, 96)]
                    for scopes, value in self._group_identical(objects[name])]
            out.append(f"#### {name}\n")
            out.append(_md_table(["Scopes", "Fields"], rows))
        return "\n".join(out)

    def scripts(self):
        """Scripts each experiment ran, identical sets collapsed together."""
        per_scope = {}
        for eid in self.eids:
            names = []
            for event in self.exp.data[eid].get("events") or []:
                if event.get("type") in ("payload_added", "payload_version_change"):
                    name = event.get("payload") or event.get("path")
                    if name and name not in names:
                        names.append(str(name))
            if names:
                per_scope[self.scope(eid)] = tuple(sorted(names))
        if not per_scope:
            return ""
        rows = []
        for scopes, names in self._group_identical(per_scope):
            for name in names:
                rows.append([scopes, _cell(name, 96)])
        return "### Scripts\n\n" + _md_table(["Scopes", "Script"], rows)

    def protocols(self):
        """Protocols, with identical sets across scopes collapsed to one row."""
        per_scope = {}
        for eid in self.eids:
            files = self.members[eid].protocol_files()
            if files:
                per_scope[self.scope(eid)] = tuple(
                    f"{f['file']} \u2014 {f['title']}" if f["title"] else f["file"]
                    for f in files)
        if not per_scope:
            return ""
        rows = [[scopes, _cell("; ".join(names), 96)]
                for scopes, names in self._group_identical(per_scope)]
        return "### Protocols\n\n" + _md_table(["Scopes", "Protocols"], rows)

    def experiment_attributes(self):
        """The body section, in the same order the single report uses."""
        blocks = [b for b in (self.attributes(), self.scripts(), self.protocols()) if b]
        if not blocks:
            return ""
        return "## Experiment attributes\n\n" + "\n".join(blocks)

    def attributes(self):
        """Run parameters, with values common to every experiment marked."""
        attribs = {}
        for eid in self.eids:
            value = (self.exp.data[eid].get("metadata") or {}).get("attribs")
            if isinstance(value, dict) and value:
                attribs[self.scope(eid)] = value
        if not attribs:
            return ("### Attributes\n\n_No experiment recorded any `attribs`._\n")
        keys = sorted({k for a in attribs.values() for k in a})
        rows = []
        for key in keys:
            per_scope = {scope: _cell(a.get(key), 40) for scope, a in attribs.items()}
            grouped = self._group_identical(per_scope)
            if len(grouped) == 1:
                rows.append([key, grouped[0][1] + f"  ({grouped[0][0]})"])
            else:
                rows.append([key, "; ".join(f"{scopes}: {value}"
                                            for scopes, value in grouped)])
        missing = sorted({self.scope(e) for e in self.eids} - set(attribs))
        table = _md_table(["Attribute", "Value"], rows)
        if missing:
            table += f"\n_No `attribs` recorded for: {self._ranges(missing)}._\n"
        return "### Attributes\n\n" + table

    def streams(self):
        """Each stream once, with a row per experiment and a shared plot."""
        by_name = {}
        for eid in self.eids:
            member = self.members[eid]
            frame = member.results_frame()
            for event in member.stream_events():
                name = event.get("name") or event.get("measureid")
                sub = (frame[frame["measureid"] == event.get("measureid")]
                       if "measureid" in frame.columns else frame.iloc[0:0])
                by_name.setdefault(name, []).append((eid, event, sub))
        if not by_name:
            return ""

        out = ["## Measurement streams\n"]
        for name, members in sorted(by_name.items()):
            out.append(f"### {name}\n")
            rows = []
            for eid, event, sub in sorted(members, key=lambda m: self.scope(m[0])):
                payload = [c for c in sub.dropna(axis=1, how="all").columns
                           if c not in SPINE_COLUMNS]
                numeric = []
                for col in payload:
                    values = pd.to_numeric(sub[col], errors="coerce").dropna()
                    if not values.empty and len(values) >= 0.6 * max(len(sub), 1):
                        numeric.append(f"{col} {values.mean():.2f} "
                                       f"({values.min():.2f}–{values.max():.2f})")
                rows.append([
                    _cell("day log" if self.is_day_log(eid) else self.scope(eid), 12),
                    str(len(sub)),
                    _fmt_exptime(event.get("exptime")),
                    _cell("; ".join(numeric) if numeric
                          else ", ".join(payload) or "—", 80)])
            out.append(_md_table(["Scope", "Rows", "Started", "mean (min–max)"],
                                 rows))
            figure = self._overlay_plot(name, members)
            if figure:
                out.append(f"\n![{name}]({figure})\n")
            out.append("")
        return "\n".join(out)

    def _overlay_plot(self, name, members):
        """One line per experiment on a shared elapsed axis."""
        plt = self._plt()
        if plt is None:
            return None
        series = []
        for eid, event, sub in members:
            if sub.empty:
                continue
            hours = pd.to_numeric(sub.get("exptime"), errors="coerce") / 3600.0
            for col in [c for c in sub.dropna(axis=1, how="all").columns
                        if c not in SPINE_COLUMNS]:
                values = pd.to_numeric(sub[col], errors="coerce")
                if values.notna().sum() >= 8:
                    series.append((col, self.scope(eid), hours, values))
        if not series:
            return None
        columns = sorted({c for c, _, _, _ in series})
        fig, axes = plt.subplots(len(columns), 1, sharex=True, squeeze=False,
                                 figsize=(7.4, 2.0 * len(columns)))
        for ax, col in zip(axes[:, 0], columns):
            for column, scope, hours, values in series:
                if column == col:
                    ax.plot(hours, values, lw=0.9, label=scope)
            ax.set_ylabel(col, fontsize=8)
            ax.grid(alpha=0.3, lw=0.5)
            ax.tick_params(labelsize=7)
        axes[0, 0].legend(fontsize=6, ncol=4, framealpha=0.9)
        axes[-1, 0].set_xlabel("elapsed (hours)", fontsize=8)
        fig.suptitle(f"{name} — all experiments", fontsize=9)
        fig.tight_layout()
        self._figno += 1
        path = os.path.join(self._figure_dir(), f"overlay_{self._figno}.png")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return path

    def data(self):
        """Run files per experiment; downstream output has its own section."""
        rows = []
        for eid in self._ordered():
            member = self.members[eid]
            census = [c for c in member._file_census()
                      if c[1] not in member.ANALYSIS_DIRS]
            if not census:
                continue
            unlogged = [u for u in member.unlogged_files()
                        if u[2] not in member.ANALYSIS_DIRS]
            rows.append([_cell(self.scope(eid), 10), _cell(eid, 12),
                         str(len(census)), member._human(sum(c[2] for c in census)),
                         str(len(unlogged))])
        if not rows:
            return ""
        return ("## Data\n\n"
                + _md_table(["Scope", "eid", "Files", "Size", "No event"], rows))

    def analysis(self):
        """Downstream products, counted apart from what the runs recorded."""
        rows = []
        for eid in self._ordered():
            member = self.members[eid]
            census = [c for c in member._file_census()
                      if c[1] in member.ANALYSIS_DIRS]
            if not census:
                continue
            rows.append([_cell(self.scope(eid), 10), _cell(eid, 12),
                         str(len(census)), member._human(sum(c[2] for c in census))])
        if not rows:
            return ""
        return ("## Analysis\n\n"
                + _md_table(["Scope", "eid", "Downstream files", "Size"], rows))

    #: One colour per scope. Event type is already carried by the lane, so
    #: colour is free to say which scope produced each marker.
    SCOPE_COLOURS = ["#1f6f8b", "#b5651d", "#3f7fae", "#5a9e46", "#8e44ad",
                     "#c0392b", "#16a085", "#7f8c8d", "#b03060", "#b58900"]

    def _scope_palette(self):
        scopes = sorted({self.scope(e) for e in self.eids})
        return {s: self.SCOPE_COLOURS[i % len(self.SCOPE_COLOURS)]
                for i, s in enumerate(scopes)}

    def events_timeline_figure(self):
        """
        The single-experiment event timeline, merged.

        Same shape as the one-experiment figure -- a lane per event type, with
        sessions as spans above -- except that the axis is wall clock, because
        elapsed time would stack runs that started minutes apart on top of one
        another, and colour now carries the scope. The type is already given by
        the lane, so colour is free to say who.
        """
        plt = self._plt()
        if plt is None:
            return None
        import matplotlib.dates as mdates
        from matplotlib.lines import Line2D

        palette = self._scope_palette()
        by_type, sessions = {}, []
        for eid in self.eids:
            scope = self.scope(eid)
            for event in self.exp.data[eid].get("events") or []:
                wall = self._wall(event)
                if wall is None:
                    continue
                kind = event.get("type") or "unknown"
                if kind == "session":
                    sessions.append((eid, scope, wall))
                    continue
                by_type.setdefault(kind, []).append((scope, wall))
        if not by_type:
            return None

        order = sorted(by_type, key=lambda k: min(w for _, w in by_type[k]))
        rows = len(order) + (1 if sessions else 0)
        fig, ax = plt.subplots(figsize=(11.0, 0.40 * rows + 1.8))
        labels, y = [], 0

        if sessions:
            # One lane per scope. Drawn on a single row the spans sit on top of
            # one another and only the last is visible, which is the opposite of
            # what a Gantt is for.
            # per experiment, not per scope: several runs of one scope would
            # otherwise share a row and overlap each other
            per_eid = {}
            for eid, scope, wall in sessions:
                per_eid.setdefault(eid, (scope, []))[1].append(wall)
            last = max(w for lst in by_type.values() for _, w in lst)
            for eid in self._ordered():
                if eid not in per_eid:
                    continue
                scope, times = per_eid[eid]
                times = sorted(times)
                ends = [self.members[eid].duration_s()]
                spans = [(mdates.date2num(times[i]),
                          mdates.date2num(times[i + 1] if i + 1 < len(times) else last)
                          - mdates.date2num(times[i])) for i in range(len(times))]
                ax.broken_barh(spans, (y - 0.32, 0.64), facecolors=palette[scope],
                               alpha=0.55, edgecolors="white", linewidth=0.4)
                labels.append(f"session \u00b7 {self._column_label(eid)} ({len(times)})")
                y += 1

        for kind in order:
            if y % 2 == 0:
                ax.axhspan(y - 0.5, y + 0.5, color="#000000", alpha=0.035, lw=0)
            for scope, wall in by_type[kind]:
                ax.plot([mdates.date2num(wall)], [y], "|", color=palette[scope],
                        markersize=8, markeredgewidth=1.0)
            labels.append(f"{kind} ({len(by_type[kind])})")
            y += 1

        ax.set_yticks(range(len(labels)))
        ax.set_yticklabels(labels, fontsize=7)
        ax.set_ylim(len(labels) - 0.5, -0.5)
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=2))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.grid(axis="x", alpha=0.3, lw=0.5)
        ax.tick_params(labelsize=7)
        ax.set_xlabel("wall clock", fontsize=8)
        ax.legend(handles=[Line2D([0], [0], color=c, lw=2, label=s)
                           for s, c in palette.items()],
                  fontsize=6.5, ncol=min(len(palette), 9), loc="upper center",
                  bbox_to_anchor=(0.5, -0.18), frameon=False)
        for spine in ("top", "right", "left"):
            ax.spines[spine].set_visible(False)
        fig.tight_layout()
        self._figno += 1
        path = os.path.join(self._figure_dir(), f"events_{self._figno}.png")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return path

    def event_log(self):
        """
        Every experiment's events in one chronological table.

        The explorer already merges these into `df_events`; this is that view
        with the scope and eid attached, since a row is meaningless without
        knowing which run produced it. Types that recur throughout a run are
        folded per scope, so each one still shows its own cadence.
        """
        entries = []
        for eid in self.eids:
            member = self.members[eid]
            scope = self.scope(eid)
            for event in self.exp.data[eid].get("events") or []:
                wall = self._wall(event)
                # keyed on the experiment, not the scope: eleven runs of one
                # scope each firing an event once are eleven events, not a
                # recurring one
                entries.append((wall, (eid, event.get("type")), scope, eid,
                                event.get("type") or "unknown",
                                member._event_detail(event)))
        if not entries:
            return ""

        spans = {}
        for wall, key, _, _, _, _ in entries:
            if wall is not None:
                spans.setdefault(key, []).append(wall)
        collapsible = set()
        for key, times in spans.items():
            if len(times) < 8:
                continue
            width = (max(times) - min(times)).total_seconds()
            member_span = self.members[key[0]].duration_s()
            if member_span and width / member_span >= 0.5:
                collapsible.add(key)

        entries.sort(key=lambda e: (e[0] is None, e[0]))
        seen, banded = set(), []
        for wall, key, scope, eid, kind, detail in entries:
            stamp = wall.strftime("%H:%M:%S") if wall else ""
            if key in collapsible:
                if key in seen:
                    continue
                seen.add(key)
                times = sorted(spans[key])
                gaps = sorted((b - a).total_seconds()
                              for a, b in zip(times, times[1:]))
                cadence = gaps[len(gaps) // 2] if gaps else None
                banded.append((wall, [stamp, _cell(scope, 8), _cell(eid, 12),
                                      _cell(kind, 26), _cell(detail, 44)]))
                note = f"\u2026 {len(times) - 1} more, through " \
                       f"{times[-1].strftime('%d %b %H:%M:%S')}"
                if cadence:
                    note += f", one every {_fmt_exptime(cadence)}"
                banded.append((wall, ["", _cell(scope, 8), "", "", note]))
                continue
            banded.append((wall, [stamp, _cell(scope, 8), _cell(eid, 12),
                                  _cell(kind, 26), _cell(detail, 44)]))
        return ("### Event log\n\n"
                + _md_table(["Time", "Scope", "eid", "Type", "Detail"],
                            self._date_banded(banded, 5)))

    def appendix(self):
        parts = ["## Appendix\n"]
        devices = self.device_configuration()
        if devices:
            parts.append(devices)
        figure = self.events_timeline_figure()
        if figure:
            parts.append(f"### Event timeline\n\n![event timeline]({figure})\n")
        parts.append(self.event_log())
        if self.elements:
            parts.append("### Attachments\n\n" + "\n\n".join(self.elements) + "\n")
        return "\n".join(parts)

    # ----------------------------------------------------------------- output

    def markdown(self):
        # the same flow as the single-experiment report, with an overview of the
        # cohort added at the top -- the one thing a combined report must say
        # that a single one need not
        parts = [self.preamble(), self.overview(), self.notebook(), self.summary(),
                 self.experiment_attributes(), self.streams(), self.data(),
                 self.analysis(), self.appendix()]
        return "\n\n".join(p.strip() for p in parts if p and p.strip()) + "\n"

    def to_markdown(self, path):
        with open(path, "w") as handle:
            handle.write(self.markdown())
        return path

    def to_pdf(self, path):
        from . import render_reportlab
        return render_reportlab.render(self.markdown(), path, {
            "band_rgb": THEME_RGB,
            "header_pts": HEADER_BAND_PTS,
            "footer_pts": FOOTER_BAND_PTS,
            "logo": self.logo if (self.logo and os.path.exists(self.logo)) else None,
            "icon": self.icon if (self.icon and os.path.exists(self.icon)) else None,
            "icon_scale": 0.62,
            "eid": f"{len(self.eids)} experiments",
            "title": self.title,
            "stamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    def __repr__(self):
        return f"<CombinedReport {self.title} — {len(self.eids)} experiments>"
