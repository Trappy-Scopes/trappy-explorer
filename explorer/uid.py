"""
Unique ids in the Trappy-Scopes format.

The same generator as ``trappyscopes/core/uid.py``: 10 characters from
``0-9a-f`` (NanoID), which is what every experiment's ``eid`` is -- the suffix
of a dataset folder name (``..._8618b9ea55``) and of a Metaexperiment folder.
Analysis tools (trackyscope) import it from here so that the scopes and the
analysis hand out ids of one kind.
"""

import nanoid

ALPHABET = "1234567890abcdef"
SIZE = 10


def uid() -> str:
    """A new id, e.g. ``'3f9a0c1e7b'``."""
    return nanoid.generate(ALPHABET, SIZE)
