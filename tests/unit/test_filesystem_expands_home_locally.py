"""``~`` in a path given to the ``cluster_*`` helpers, on the local backend.

Real directories under the test's own ``$HOME``. The remote side of the same
rule needs a real SFTP server: ``tests/real_world/test_paths_with_a_tilde_real.py``.
"""

import os

from clustrix.config import ClusterConfig
from clustrix.filesystem import (
    cluster_exists,
    cluster_find,
    cluster_isdir,
    cluster_ls,
    cluster_stat,
)


def test_a_tilde_means_home_not_a_directory_named_tilde(tmp_path):
    home = os.path.expanduser("~")
    os.makedirs(os.path.join(home, "study", "sub"))
    with open(os.path.join(home, "study", "a.csv"), "w") as handle:
        handle.write("abc")
    # Somewhere else entirely, so a path joined onto it cannot find anything.
    config = ClusterConfig(cluster_type="local", local_work_dir=str(tmp_path))

    assert cluster_ls("~/study", config) == ["a.csv", "sub"]
    assert "study" in cluster_ls("~", config)
    assert cluster_exists("~/study/a.csv", config) is True
    assert cluster_isdir("~/study/sub", config) is True
    assert cluster_stat("~/study/a.csv", config).size == 3
    assert cluster_find("*.csv", "~/study", config) == ["a.csv"]


def test_a_relative_path_still_means_the_work_dir(tmp_path):
    (tmp_path / "here.txt").write_text("x")
    config = ClusterConfig(cluster_type="local", local_work_dir=str(tmp_path))

    assert cluster_ls(".", config) == ["here.txt"]
    assert cluster_exists("here.txt", config) is True
