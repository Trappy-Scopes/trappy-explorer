"""The id generator matches the Trappy-Scopes eid format."""

import re

from explorer import uid


def test_uid_has_the_eid_format():
    ids = {uid() for _ in range(1000)}

    assert len(ids) == 1000
    assert all(re.fullmatch(r"[0-9a-f]{10}", i) for i in ids)
