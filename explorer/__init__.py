"""
trappy-explorer — explore Trappy-Scopes experiment data.

Two entry points:

``Explorer``
    The current, actively developed interface (``explorer.explorer``).

``ExpExplorer`` / ``LegacyExplorer``
    The frozen original single-file implementation (``explorer.legacy``),
    kept for reference and for notebooks that still import it by name.

``ExpReport``
    Renders one experiment from an explorer as Markdown, HTML or PDF
    (``explorer.report``).

``CombinedReport``
    Renders many experiments as a single document (``explorer.combined``) --
    a day of scopes side by side, keyed on wall clock rather than elapsed.
"""

__version__ = "0.1.0"

from .explorer import Explorer
from .legacy import ExpExplorer, LegacyExplorer
from .combined import CombinedReport
from .report import ExpReport

__all__ = ["Explorer", "ExpExplorer", "LegacyExplorer", "ExpReport",
           "CombinedReport", "__version__"]
