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

    def create_superset(parentdir):
        """
        Create a dataset object using all the folders in the parentdir.
        parentdir: valid directory containing Trappy-Scopes experiments.
        """
        all_dirs = os.listdir(parentdir)
        all_exps = [os.path.join(parentdir, dir_) for dir_ in all_dirs if dir_ != ".DS_Store"]  ## Remove DS store
        all_exps = [dir_ for dir_ in all_exps if os.path.isdir(dir_)]  ## Remove files, only retain directories
        ## Build ExpExplorer object -> This will filter for the invalid experiments automatically.
        return ExpExplorer(all_exps)

    def read_yaml(exp, safemode=True):
        """
        Parse the given yaml file.
        safemode: Opens the file in safe mode: ignore all complex serialised objects that require knowledge
        of their constructors.
        """
        class SafeLoaderIgnoreTags(yaml.SafeLoader):
            def ignore_unknown(self, node):
                return None  # Replace with a default value, like `None`
        # Register the fallback handler for all unknown tags
        SafeLoaderIgnoreTags.add_constructor(None, SafeLoaderIgnoreTags.ignore_unknown)

        with open(exp, "r") as file:
            if safemode:
                all_ = yaml.load(file, Loader=SafeLoaderIgnoreTags)
            else:
                all_ = yaml.load(file, Loader=yaml.Loader)
            return all_

    def __init__(self, datadirs, safemode=True):
        """
        datafiles : experiment directories (not experiment.yaml file).
        """

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
                yaml_content = ExpExplorer.read_yaml(expfilename, safemode=safemode)
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
        self.data[eid]["results"] = datadict["results"]
        self.data[eid]["events"] = datadict["events"]
        self.data[eid]["filetree"] = ExpExplorer.build_file_tree(self.datadir_map[eid])
        self.data[eid]["m_streams"] = []  ## LEGACY-FIX (3): always present, so __repr__ is safe
        datadict.pop("results")
        datadict.pop("events")
        self.data[eid]["metadata"] = datadict  ## Rest of the dictionary

    def __repr__(self):
        text = f"<Experiments :: {len(self.data)}  datasets>\n"
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
