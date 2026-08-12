"""
trappy-explorer — explore Trappy-Scopes experiment data.

Two entry points:

``Explorer``
    The current, actively developed interface (``explorer.explorer``).

``ExpExplorer`` / ``LegacyExplorer``
    The frozen original single-file implementation (``explorer.legacy``),
    kept for reference and for notebooks that still import it by name.
"""

__version__ = "0.1.0"

from .explorer import Explorer
from .legacy import ExpExplorer, LegacyExplorer

__all__ = ["Explorer", "ExpExplorer", "LegacyExplorer", "__version__"]
