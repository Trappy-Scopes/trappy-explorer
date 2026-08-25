"""
Legacy explorer — the original single-file ``ExpExplorer`` implementation.

This module is a *frozen* snapshot of the first working version of the
Trappy-Scopes experiment explorer, kept for reference and for backwards
compatibility with notebooks and scripts that still do::

    from explorer.legacy import ExpExplorer

Only the minimal fixes required to make the class importable and runnable
have been applied (each marked ``# LEGACY-FIX``). The API, naming and
structure are otherwise preserved verbatim. New work belongs in
``explorer.explorer.Explorer``, not here.

The one deliberate *extension* is ``extended_parse`` (see ``read_yaml``). Legacy
safemode maps every unknown YAML tag to ``None``, which silently drops the numpy
scalars and tuples that the framework writes into measurement payloads --
``density: !!python/object/apply:numpy._core.multiarray.scalar`` becomes
``None`` and nobody notices. ``extended_parse=True`` resolves those tags for
real. It is opt-in precisely because it breaks legacy fidelity: values that a
legacy read reported as ``None`` come back as actual numbers, so two reads of
the same file no longer agree. Use it when you want the data; leave it off when
you want to reproduce what the original explorer saw.

Fixes applied relative to the original paste:
  1. ``notexpfiles`` was filtered from the already-filtered list, so the
     "no logs" warning could never fire.
  2. ``datadir_map[eid]`` was indexed with the *expfiles* loop counter,
     silently mapping an eid to the wrong directory whenever a directory
     lacked an ``experiment.yaml``.
  3. ``m_streams`` was only set for experiments that had measurement
     streams, so ``__repr__`` raised ``KeyError`` for the others.
  4. ``get_converted_dir(eid=...)`` ignored its argument.
  5. The bare ``except:`` around YAML parsing swallowed real errors.
  6. An experiment with no ``results``/``events`` keys (a run aborted before its
     first measurement) raised ``KeyError`` from outside the try/except, so a
     single bad directory made an entire folder unreadable.
"""

import os
import yaml
import pandas as pd
from rich import print
from rich.pretty import Pretty
from rich.text import Text


class ExpExplorer:
    """
    Function to combine and load `Trappy-Scopes` experiment data.
    The utility mainly parses the `experiment.yaml` file.

    A dataset needs to contain an `experiment.yaml` file to be parsed.

    Usage: `exp_logs = ExpExplorer(["<path-to-experiment-directory>"])`

    ## Datastructure
       self
         *
         |- datadirs    (`[sequential list of data-directories within the experiment scope.]`)
         |- datadir_map (`{eid: <corresponding data-directory (fullpath)>}`)
         |- expmap      (`{eid, <corresponding fullpath to experiment.yaml file}`)
         |- invexpmap   (`{< fullpath to experiment.yaml file>: corresponding eid}`)
         |- eidlist     (Sequential list of all eids)
         |- data
         :   |- eid1
         :   :  |- results (all measurements)
         :   :  |- events  (all experiment events)
         :   :  |- filetree
         :   :  |- metadata
         :   :  |- m_streams
         :   :  :
         :   |- eidn
         :   :  |- results (all measurements)
         :   :  |- events  (all experiment events)
         :   :  |- filetree
         :   :  |- metadata
         :   :  |- m_streams
         |- df          (dataframe of all measurements)
         |- df_events   (dataframe of all the events)
         x
    """

    def build_file_tree(root_dir):
        """
        Returns the file
        Generated using ChatGPT.
        """
        file_tree = {}
        for dirpath, dirnames, filenames in os.walk(root_dir):
            # Get the current level dictionary
            current_level = file_tree
            # Traverse down the path to the current directory
            parts = os.path.relpath(dirpath, root_dir).split(os.sep)
            for part in parts:
                if part not in current_level:
                    current_level[part] = {}
                current_level = current_level[part]
            # Add subdirectories and files to the current level
            for dirname in dirnames:
                current_level[dirname] = {}
            for filename in filenames:
                if 'files' not in current_level:
                    current_level['files'] = []
                current_level['files'].append(filename)
        return file_tree

    def create_superset(parentdir, safemode=True, extended_parse=False):
        """
        Create a dataset object using all the folders in the parentdir.
        parentdir: valid directory containing Trappy-Scopes experiments.
        extended_parse: see `read_yaml` -- resolves numpy/tuple payloads and
        breaks legacy fidelity.
        """
        all_dirs = os.listdir(parentdir)
        all_exps = [os.path.join(parentdir, dir_) for dir_ in all_dirs if dir_ != ".DS_Store"]  ## Remove DS store
        all_exps = [dir_ for dir_ in all_exps if os.path.isdir(dir_)]  ## Remove files, only retain directories
        ## Build ExpExplorer object -> This will filter for the invalid experiments automatically.
        return ExpExplorer(all_exps, safemode=safemode, extended_parse=extended_parse)

    def read_yaml(exp, safemode=True, extended_parse=False):
        """
        Parse the given yaml file.

        safemode: Opens the file in safe mode: ignore all complex serialised objects that require knowledge
        of their constructors.

        extended_parse: Resolve the serialised python/numpy objects instead of
        discarding them. This is an *extension*, not legacy behaviour -- it
        deliberately breaks legacy mode, and it takes precedence over
        ``safemode``.

        Why it exists: the framework writes measurement payloads through numpy,
        so values land in the log as ``!!python/object/apply:numpy._core.multiarray.scalar``
        (with a ``!!python/object/apply:numpy.dtype`` argument and a ``!!binary``
        payload), and multi-channel readings land as ``!!python/tuple``. Legacy
        safemode maps every tag it does not know to ``None``, so those values are
        silently lost -- a `density` column reads as all-empty and nothing warns
        you. With ``extended_parse=True`` they come back as real floats and
        tuples.

        The cost, and the reason it is off by default: resolving these tags means
        running the constructors named in the file, so (a) the parse is only as
        trustworthy as the file -- never point it at an experiment directory you
        did not produce -- and (b) a tag whose module is not importable in this
        environment cannot be resolved. numpy 2.x writes ``numpy._core.*`` and
        numpy 1.x writes ``numpy.core.*``; reading the other era's logs needs the
        matching numpy, so unresolvable tags fall back to ``None`` with a warning
        rather than killing the parse.
        """
        class SafeLoaderIgnoreTags(yaml.SafeLoader):
            def ignore_unknown(self, node):
                return None  # Replace with a default value, like `None`
        # Register the fallback handler for all unknown tags
        SafeLoaderIgnoreTags.add_constructor(None, SafeLoaderIgnoreTags.ignore_unknown)

        class ExtendedLoader(yaml.UnsafeLoader):
            """Full constructor support, but a bad tag degrades instead of raising."""
            unresolved = set()

            def unresolved_tag(self, node):
                ExtendedLoader.unresolved.add(node.tag)
                return None

        ExtendedLoader.add_constructor(None, ExtendedLoader.unresolved_tag)

        with open(exp, "r") as file:
            if extended_parse:
                ExtendedLoader.unresolved = set()
                try:
                    all_ = yaml.load(file, Loader=ExtendedLoader)
                except Exception as err:
                    ## A constructor blew up mid-document -- fall back rather than
                    ## lose the whole experiment, and say so loudly.
                    print(f"[yellow]extended_parse failed on {exp} ({err!r}); "
                          f"falling back to legacy safemode for this file.[default]")
                    file.seek(0)
                    return yaml.load(file, Loader=SafeLoaderIgnoreTags)
                if ExtendedLoader.unresolved:
                    print(f"[yellow]extended_parse could not resolve these tags in {exp} "
                          f"(values are None): {sorted(ExtendedLoader.unresolved)}[default]")
                return all_
            elif safemode:
                all_ = yaml.load(file, Loader=SafeLoaderIgnoreTags)
            else:
                all_ = yaml.load(file, Loader=yaml.Loader)
            return all_

    def __init__(self, datadirs, safemode=True, extended_parse=False):
        """
        datafiles : experiment directories (not experiment.yaml file).
        extended_parse : resolve serialised numpy/python payloads instead of
        dropping them. See `read_yaml` -- this breaks legacy fidelity on purpose,
        so it is recorded on the instance as `self.extended_parse` to make it
        obvious later which way a given object was loaded.
        """

        self.extended_parse = extended_parse

        self.data = {}
        self.all = {}

        ## eids
        self.eidlist = []

        ## Map of all datadirectories
        self.datadirs = datadirs
        self.datadir_map = {}

        ## Map of experiment.yaml files
        self.expmap = {}
        self.eidlist = []

        ## Detect experiment.yaml files
        ## LEGACY-FIX (1)(2): keep the datadir paired with its experiment.yaml so the
        ## eid -> datadir mapping stays correct, and compute the missing-log list
        ## from the *unfiltered* candidates.
        candidates = [(dir_, os.path.join(dir_, "experiment.yaml")) for dir_ in datadirs]
        valid = [(dir_, file) for dir_, file in candidates if os.path.isfile(file)]
        notexpfiles = [file for _, file in candidates if not os.path.isfile(file)]
        self.expfiles = [file for _, file in valid]
        if len(notexpfiles) != 0:
            print(f"Error: the following experiments had no logs (will ignore them): {notexpfiles}")

        ## Parse experiment.yaml files
        for i, (datadir, expfilename) in enumerate(valid):
            print(f"Experiment: {i}")
            eid = None
            try:
                yaml_content = ExpExplorer.read_yaml(expfilename, safemode=safemode,
                                                     extended_parse=extended_parse)
                eid = yaml_content["eid"]
                print(f"eid: {eid}, path: {expfilename}")
            except Exception as err:  ## LEGACY-FIX (5): don't swallow the error silently
                print(f"[red]Error: experiment.yaml failed to parse ({expfilename}): {err!r}. "
                      f"Please consider using safemode=True, or maybe the file is damaged. "
                      f"Please try to open the file manually to check.[default]")
                continue

            ## Update indexes
            self.eidlist.append(eid)
            self.expmap[expfilename] = eid
            self.datadir_map[eid] = datadir  ## Save a map of eid: absolute_path_to_dataset_directory

            ## Update data
            self.update_datastructure(eid, yaml_content)

        ## Create inversemap
        self.invexpmap = inv_map = {v: k for k, v in self.expmap.items()}
        ## Create one data frame for results
        combined = []
        for eid in self.eidlist:
            combined = combined + self.data[eid]["results"]
        self.df = pd.DataFrame(combined)

        ## Make a common data frame of all events
        combined = []
        for eid in self.eidlist:
            combined = combined + self.data[eid]["events"]
        self.df_events = pd.DataFrame(combined)

        ## Make a map of all measurement streams
        for eid in self.eidlist:
            df_ = pd.DataFrame(self.data[eid]["events"])
            if not df_.empty and "type" in df_:
                streams = df_[df_.type == "measurement_stream"]
                if not streams.empty:
                    self.data[eid]["m_streams"] = list(streams.name.unique())
        ### Print success message
        print(f"Parsed {len(self.expmap)} experiments.")

    def update_datastructure(self, eid, datadict):
        """
        Takes a datadictionary (which is parsed from the yaml file and
        updates the internal datastructures of this object.
        """
        self.data[eid] = {}
        ## LEGACY-FIX (6): a run that was aborted before its first measurement has no
        ## `results`/`events` keys at all. Indexing them raised KeyError from *outside*
        ## the try/except in __init__, which killed the whole constructor -- one bad
        ## directory made a whole folder unreadable. Default to empty lists instead.
        self.data[eid]["results"] = datadict.get("results") or []
        self.data[eid]["events"] = datadict.get("events") or []
        self.data[eid]["filetree"] = ExpExplorer.build_file_tree(self.datadir_map[eid])
        self.data[eid]["m_streams"] = []  ## LEGACY-FIX (3): always present, so __repr__ is safe
        datadict.pop("results", None)
        datadict.pop("events", None)
        self.data[eid]["metadata"] = datadict  ## Rest of the dictionary

    def __repr__(self):
        mode = " [extended_parse]" if getattr(self, "extended_parse", False) else ""
        text = f"<Experiments :: {len(self.data)}  datasets{mode}>\n"
        for eid, v in self.data.items():
            text += f"  |- eid: {eid}\n"
            if self.data[eid]["m_streams"]:
                for s in self.data[eid]["m_streams"]:
                    text += f"  \t|- m-stream: {s}\n"
        return text

    def __getitem__(self, eid):
        """
        Specific accessor -> accepts both eid or index [0, len(self.eidlist)].
        """
        if len(self.eidlist) == 1:
            return self.data[self.eidlist[0]]
        elif eid in self.eidlist:
            return self.data[eid]
        elif eid < len(self.eidlist):
            return self.data[self.eidlist[eid]]
        else:
            raise KeyError("eid not present or index out of range.")

    def __eid_by_index__(self, eid):
        if eid in self.eidlist:
            return eid
        elif isinstance(eid, int) and eid < len(self.eidlist):
            return self.eidlist[eid]
        else:
            raise IndexError(f"Index out of range: {eid}")

    def get_analysis_dir(self, eid=0):
        """
        Return the corresponding analysis directory.
        """
        eid = self.__eid_by_index__(eid=eid)
        return os.path.join(self.datadir_map[eid], "analysis")

    def get_parent_dir(self, eid=0):
        """
        Return the corresponding analysis directory.
        """
        eid = self.__eid_by_index__(eid=eid)
        return self.datadir_map[eid]

    def get_converted_dir(self, eid=0):
        """
        Return the corresponding analysis directory.
        """
        eid = self.__eid_by_index__(eid=eid)  ## LEGACY-FIX (4): was hardcoded to eid=0
        return os.path.join(self.datadir_map[eid], "converted")

    def get_postprocess_dir(self, eid=0):
        """
        Return the corresponding analysis directory.
        """
        eid = self.__eid_by_index__(eid=eid)
        return os.path.join(self.datadir_map[eid], "postprocess")

    def notebook(self):
        from rich.table import Table
        from rich.console import Console

        t = Table()
        t.add_column("exptime (s)", no_wrap=False)
        if len(self.eidlist) != 1:
            t.add_column("eid", no_wrap=False)
            t.add_column("scopeid", no_wrap=False)
        t.add_column("note", no_wrap=False)

        def gen_row(row):
            if len(self.eidlist) != 1:
                return (
                       f'{row["exptime"]:.1f}', Text(str(row["eid"])),
                       str(row["scopeid"]), str(row["note"]))
            else:
                return (
                       f'{row["exptime"]:.1f}', Text(str(row["note"])))

        for index, row in self.df_events[self.df_events.type == "user_note"].iterrows():
            t.add_row(*gen_row(row))
        console = Console()
        console.print(t, highlight=True)

    def all_events(self):
        from rich.table import Table
        from rich.console import Console

        t = Table()
        t.add_column("exptime (s)", no_wrap=False)
        if len(self.eidlist) != 1:
            t.add_column("eid", no_wrap=False)
            t.add_column("scopeid", no_wrap=False)
        t.add_column("Event", no_wrap=False)

        def gen_row(row):
            if len(self.eidlist) != 1:
                return (
                       f'{row["exptime"]:.1f}', Text(str(row["eid"])),
                       str(row["scopeid"]), str(row["type"]))
            else:
                return (
                       f'{row["exptime"]:.1f}', Text(str(row["type"])))

        for index, row in self.df_events.iterrows():
            t.add_row(*gen_row(row))
        console = Console()
        console.print(t, highlight=True)


## Backwards-compatible alias used by the package export.
LegacyExplorer = ExpExplorer
