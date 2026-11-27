"""Finding the files a call names, and deciding which may leave the machine.

Everything here uses real files in a real project directory. Discovery and
admission are local and need no cluster; the linking and "already there"
cases run against the real in-process SSH server. Uploading -- which needs a
remote that does *not* share this filesystem -- is exercised against a real
sshd in ``tests/real_world/test_auto_stage_real.py``.
"""

import os
import subprocess
from pathlib import Path

import pytest

from clustrix import auto_stage
from clustrix.config import ClusterConfig
from clustrix.executor import ClusterExecutor
from tests.ssh_server import LocalSSHServer

TABLE = "data/table.csv"
ELSEWHERE = "/etc/hostname"
NOT_A_PATH = "accuracy/loss"  # has a slash, names nothing


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A git project with some data in it, as the working directory."""
    root = tmp_path / "project"
    (root / "data" / "subjects").mkdir(parents=True)
    (root / "data" / "table.csv").write_text("a,b\n1,2\n")
    for index in (1, 2):
        (root / "data" / "subjects" / f"s{index}.txt").write_text(f"subject {index}\n")
    (root / "train").mkdir()
    (root / "train" / "batch.npy").write_bytes(b"\x00" * 64)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)
    return root


def _found(candidates):
    return {c.written: c for c in candidates}


def _import_from(path, source):
    """Write ``source`` to ``path`` and import it as a real module."""
    import importlib.util

    path.write_text(source)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reads_a_literal():
    return open("data/table.csv").read()


def reads_through_a_global():
    return open(TABLE).read()


def helper():
    return open("data/subjects/s1.txt").read()


def calls_a_helper():
    return helper()


def names_things_that_are_not_here():
    open("data/missing.csv")
    return NOT_A_PATH, "https://example.org/data/table.csv"


def reads_outside_the_project():
    return open(ELSEWHERE).read()


def takes_paths(path, mode="train", options=None):
    return path, mode, options


class TestWhatIsFound:
    def test_a_literal_in_the_function(self, project):
        (candidate,) = auto_stage.discover(reads_a_literal, (), {})

        assert candidate.written == "data/table.csv"
        assert candidate.path == project / "data" / "table.csv"
        assert candidate.found_in == "the code of reads_a_literal"
        assert candidate.explicit is False

    def test_a_global_the_function_reads(self, project):
        (candidate,) = auto_stage.discover(reads_through_a_global, (), {})

        assert candidate.written == TABLE and candidate.found_in == "global TABLE"

    def test_a_literal_in_a_project_function_it_calls(self, project):
        """Followed because the helper's file is inside the project."""
        module = _import_from(
            project / "pipeline.py",
            "def load():\n"
            "    return open('data/subjects/s1.txt').read()\n"
            "\n"
            "def run():\n"
            "    return load()\n",
        )

        (candidate,) = auto_stage.discover(module.run, (), {})

        assert candidate.written == "data/subjects/s1.txt"
        assert candidate.found_in == "the code of load"

    def test_a_function_from_outside_the_project_is_not_searched(self, project):
        """``helper`` lives in this test module, which is not in the project:
        library code is full of strings, and none of them are the user's."""
        assert auto_stage.discover(calls_a_helper, (), {}) == []

    def test_only_paths_that_exist_here_count(self, project):
        assert auto_stage.discover(names_things_that_are_not_here, (), {}) == []

    def test_path_objects_and_path_like_strings_among_the_arguments(self, project):
        found = _found(
            auto_stage.discover(
                takes_paths,
                (Path("data/table.csv"),),
                {"options": {"extra": ["data/subjects/s2.txt", 3], "n": 4}},
            )
        )

        assert set(found) == {"data/table.csv", "data/subjects/s2.txt"}
        assert found["data/table.csv"].found_in == "argument 'path'"
        assert found["data/subjects/s2.txt"].found_in == "argument 'options'"
        assert all(candidate.explicit for candidate in found.values())

    def test_a_bare_word_is_a_path_only_where_the_parameter_says_so(self, project):
        """``mode="train"`` must not upload the directory called train."""
        as_mode = auto_stage.discover(
            takes_paths, ("data/table.csv",), {"mode": "train"}
        )
        as_path = auto_stage.discover(takes_paths, ("train",), {})

        assert set(_found(as_mode)) == {"data/table.csv"}
        assert set(_found(as_path)) == {"train"}

    def test_a_directory_argument(self, project):
        (candidate,) = auto_stage.discover(takes_paths, ("data/subjects",), {})

        assert candidate.path.is_dir()
        assert [name for name, _ in auto_stage._files_under(candidate.path)] == [
            "s1.txt",
            "s2.txt",
        ]

    def test_listed_data_is_added_and_excluded_data_is_dropped(self, project):
        found = _found(
            auto_stage.discover(
                reads_a_literal,
                (),
                {},
                extra=["train/batch.npy"],
                exclude=["data"],
            )
        )

        assert set(found) == {"train/batch.npy"}
        assert found["train/batch.npy"].found_in == "data=[...]"

    def test_an_explicit_mention_wins_over_an_incidental_one(self, project):
        (candidate,) = auto_stage.discover(
            reads_a_literal, (), {}, extra=["data/table.csv"]
        )

        assert candidate.explicit is True


class TestWhatMayLeaveTheMachine:
    def test_the_project_is_the_git_top_level(self, project, monkeypatch):
        monkeypatch.chdir(project / "data")

        assert auto_stage.project_root() == project.resolve()

    def test_a_home_directory_is_too_wide_to_be_a_project(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / "work").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(home)], check=True)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.chdir(home / "work")

        assert auto_stage.project_root() == (home / "work").resolve()

    def test_code_cannot_name_a_file_outside_the_project(self, project):
        (candidate,) = auto_stage.discover(reads_outside_the_project, (), {})

        refusal = auto_stage._refusal(candidate, auto_stage.project_root())

        assert "outside the project" in refusal and ELSEWHERE in refusal

    def test_an_argument_may_name_a_file_anywhere(self, project):
        (candidate,) = auto_stage.discover(takes_paths, (ELSEWHERE,), {})

        assert auto_stage._refusal(candidate, auto_stage.project_root()) is None

    def test_a_link_out_of_the_project_does_not_smuggle_a_file_in(self, project):
        (project / "data" / "sneaky.txt").symlink_to(ELSEWHERE)

        def reads_the_link():
            return open("data/sneaky.txt").read()

        (candidate,) = auto_stage.discover(reads_the_link, (), {})

        assert "outside the project" in auto_stage._refusal(
            candidate, auto_stage.project_root()
        )

    @pytest.mark.parametrize("explicit", [True, False])
    def test_a_credential_is_refused_however_it_was_named(self, project, explicit):
        key = project / ".ssh" / "id_ed25519"
        key.parent.mkdir()
        key.write_text("not really a key")
        candidate = auto_stage.Candidate(str(key), key, "anywhere", explicit)

        assert "credential" in auto_stage._refusal(candidate, auto_stage.project_root())


class TestRewritingArguments:
    def test_only_the_named_paths_change_and_types_are_kept(self):
        replacements = {"/local/w.bin": "/cluster/w.bin"}
        value = (
            Path("/local/w.bin"),
            "/local/w.bin",
            {"keep": "/local/other", "nested": ["/local/w.bin", 7]},
        )

        assert auto_stage._rewritten(value, replacements) == (
            Path("/cluster/w.bin"),
            "/cluster/w.bin",
            {"keep": "/local/other", "nested": ["/cluster/w.bin", 7]},
        )

    @pytest.mark.parametrize(
        "written,link",
        [
            ("data/table.csv", "data/table.csv"),
            ("./data//table.csv", "data/table.csv"),
            ("../sibling/x.csv", None),
            ("/abs/x.csv", None),
            (".", None),
        ],
    )
    def test_only_a_path_inside_the_working_directory_can_be_linked(
        self, written, link
    ):
        candidate = auto_stage.Candidate(written, Path("/unused"), "test", True)

        assert auto_stage._relative_link(candidate) == link


class TestOnACluster:
    """Against the in-process SSH server, which shares this filesystem."""

    @pytest.fixture
    def connected(self, tmp_path):
        remote = tmp_path / "remote"
        remote.mkdir()
        with LocalSSHServer(root=str(remote), password="s3cret") as server:
            config = ClusterConfig(
                cluster_type="ssh",
                cluster_host=server.host,
                cluster_port=server.port,
                username="tester",
                password="s3cret",
                ssh_host_key_policy="auto_add",
                remote_work_dir=str(remote / "work"),
            )
            with ClusterExecutor(config) as executor:
                executor.connect()
                yield executor.connection_manager, config, remote

    def test_a_file_already_at_the_same_path_is_used_where_it_is(
        self, project, connected
    ):
        """A shared filesystem: nothing is copied and nothing is rewritten."""
        connection, config, remote = connected
        table = project / "data" / "table.csv"

        args, kwargs, plan = auto_stage.prepare(
            connection, config, takes_paths, (table,), {}
        )

        (staged,) = plan.inputs
        assert staged.how == "in place" and staged.remote == str(table)
        assert args == (table,) and plan.links == []
        assert not (remote / "work" / auto_stage.STORE_DIRECTORY).exists()

    def test_a_different_file_at_the_same_path_is_not_guessed_about(
        self, project, connected
    ):
        """The size on the cluster is what `wc -c` reports there. A file
        that changes between the two looks is exactly a stale remote copy."""
        connection, config, _ = connected
        table = project / "data" / "table.csv"
        sizes = auto_stage._remote_sizes(connection, [str(table)])
        assert sizes == {str(table): table.stat().st_size}

        table.write_text("a,b\n1,2\n3,4\n5,6\n")
        stale = dict(sizes)
        assert auto_stage._remote_sizes(connection, [str(table)]) != stale

    def test_remote_sizes_tell_files_directories_and_absences_apart(
        self, project, connected
    ):
        connection, _, _ = connected
        table, folder = project / "data" / "table.csv", project / "data"

        assert auto_stage._remote_sizes(
            connection, [str(table), str(folder), str(project / "no such file")]
        ) == {
            str(table): table.stat().st_size,
            str(folder): -1,
            str(project / "no such file"): None,
        }

    def test_links_put_staged_files_at_the_paths_the_code_uses(
        self, project, connected, tmp_path
    ):
        connection, _, remote = connected
        job_dir = remote / "job"
        job_dir.mkdir()
        staged_file = remote / "store" / "abc" / "table.csv"
        staged_file.parent.mkdir(parents=True)
        staged_file.write_text("staged copy\n")
        staged_dir = remote / "store" / "def" / "subjects"
        staged_dir.mkdir(parents=True)
        (staged_dir / "s1.txt").write_text("one\n")

        auto_stage.link_into_job_directory(
            connection,
            str(job_dir),
            [
                ("data/table.csv", str(staged_file)),
                ("inputs/deep/subjects", str(staged_dir)),
                ("name with spaces.csv", str(staged_file)),
            ],
        )

        assert (job_dir / "data" / "table.csv").read_text() == "staged copy\n"
        assert (
            job_dir / "inputs" / "deep" / "subjects" / "s1.txt"
        ).read_text() == "one\n"
        assert (job_dir / "name with spaces.csv").read_text() == "staged copy\n"
        assert os.path.islink(job_dir / "data" / "table.csv")

    def test_no_links_means_no_command(self, connected):
        connection, _, remote = connected

        auto_stage.link_into_job_directory(connection, str(remote / "nowhere"), [])

        assert not (remote / "nowhere").exists()

    def test_a_call_that_names_no_files_touches_nothing(self, project, connected):
        connection, config, _ = connected

        args, kwargs, plan = auto_stage.prepare(
            connection, config, takes_paths, (3,), {"mode": "fast"}
        )

        assert (args, kwargs) == ((3,), {"mode": "fast"})
        assert plan.inputs == [] and "no local input files" in plan.explain()
