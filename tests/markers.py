"""Skip conditions shared by tests, defined once."""

import os

import pytest

#: For tests whose point is that clustrix handles a file it is *denied*.
#: The superuser is denied nothing -- a ``chmod 000`` file reads normally --
#: so under root (most containers, some CI images) there is no refusal to
#: handle and the test would fail for a reason unrelated to the code.
needs_permission_denial = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="running as root: a 0o000 file is still readable, so nothing is denied",
)
