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


# -- what an adversarial review found ------------------------------------------


def takes_folder(folder):
    return folder


def named_misleadingly(direction="up", profile="up", metadata="up", mode="up"):
    return direction, profile, metadata, mode


def named_for_paths(weights_file=None, out_dir=None, csv=None, path2=None):
    return weights_file, out_dir, csv, path2


def lists_the_working_directory():
    return os.listdir("./"), os.listdir("."), os.listdir(".."), os.listdir("/")


@pytest.fixture
def project_with_secrets(project):
    data = project / "data"
    (data / ".aws").mkdir()
    (data / ".aws" / "credentials").write_text("[default]\naws_secret_access_key=x\n")
    (data / ".env").write_text("API_KEY=hunter2\n")
    (data / "id_rsa").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n")
    (data / "private").mkdir()
    (data / "private" / "patients.csv").write_text("name,dx\n")
    os.symlink("/etc/hostname", data / "link_out.txt")
    os.symlink(project / "train", data / "linked_dir")
    return project


class TestMembersOfADirectory:
    """B1, B2, L1, M1: the same rules inside a directory as at its top."""

    def test_credentials_inside_a_directory_are_left_out_and_named(
        self, project_with_secrets
    ):
        data = project_with_secrets / "data"
        left_out = {}

        members = auto_stage._files_under(data, (), left_out)

        names = [name for name, _ in members]
        print("members:", names, "left out:", left_out)
        assert names == [
            "private/patients.csv",
            "subjects/s1.txt",
            "subjects/s2.txt",
            "table.csv",
        ]
        assert sorted(left_out["credential"]) == [
            data / ".aws" / "credentials",
            data / ".env",
            data / "id_rsa",
        ]

    def test_links_inside_a_directory_are_named_not_dropped_in_silence(
        self, project_with_secrets
    ):
        data = project_with_secrets / "data"
        left_out = {}

        auto_stage._files_under(data, (), left_out)

        assert sorted(left_out["link"]) == [data / "link_out.txt", data / "linked_dir"]

    @pytest.mark.parametrize("spelling", ["data/private", "{root}/data/private"])
    def test_an_excluded_path_removes_members_and_whole_subtrees(
        self, project_with_secrets, spelling
    ):
        data = project_with_secrets / "data"
        excluded = auto_stage._excluded_paths(
            [spelling.format(root=project_with_secrets), "data/subjects/s2.txt"]
        )

        names = [name for name, _ in auto_stage._files_under(data, excluded)]

        assert names == ["subjects/s1.txt", "table.csv"]

    def test_version_control_and_caches_are_never_members(self, project):
        (project / "data" / "__pycache__").mkdir()
        (project / "data" / "__pycache__" / "x.pyc").write_bytes(b"\x00")
        (project / "data" / ".ipynb_checkpoints").mkdir()
        (project / "data" / ".ipynb_checkpoints" / "nb.ipynb").write_text("{}")
        (project / "data" / ".git").mkdir()
        (project / "data" / ".git" / "HEAD").write_text("ref: x\n")

        names = [name for name, _ in auto_stage._files_under(project / "data")]

        assert names == ["subjects/s1.txt", "subjects/s2.txt", "table.csv"]

    def test_a_constant_naming_the_project_or_above_stages_nothing(self, project):
        """``os.listdir("./")`` used to upload the project, ``.git`` and all."""
        found = auto_stage.discover(lists_the_working_directory, (), {})
        refusals = {
            c.written: auto_stage._refusal(c, auto_stage.project_root()) for c in found
        }

        print(refusals)
        assert set(refusals) == {"./", "/"}, "'.' and '..' are not path-like"
        assert all(refusals.values()), refusals
        assert "the project directory" in refusals["./"]
        assert "contains the project" in refusals["/"]

    @pytest.mark.parametrize("named", [".", "./", "..", "/", "~"])
    def test_an_argument_naming_the_project_or_above_is_refused_too(
        self, project, named
    ):
        (candidate,) = auto_stage.discover(takes_folder, (named,), {})

        refusal = auto_stage._refusal(candidate, auto_stage.project_root())

        assert refusal and "name the files or directories" in refusal

    def test_the_working_directory_below_the_project_root_is_refused(
        self, project, monkeypatch
    ):
        monkeypatch.chdir(project / "data")
        root = auto_stage.project_root()
        (here,) = auto_stage.discover(takes_folder, (".",), {})
        (inside,) = auto_stage.discover(takes_folder, ("subjects",), {})

        assert "the working directory" in auto_stage._refusal(here, root)
        assert auto_stage._refusal(inside, root) is None


class TestParameterNames:
    """M2: ``direction`` contains "dir"; it does not name a directory."""

    @pytest.mark.parametrize("parameter", ["direction", "profile", "metadata", "mode"])
    def test_a_word_that_merely_contains_a_path_word_is_not_a_hint(
        self, project, parameter
    ):
        (project / "up").mkdir()
        (project / "up" / "junk.bin").write_text("junk")

        assert auto_stage.discover(named_misleadingly, (), {parameter: "up"}) == []

    @pytest.mark.parametrize("parameter", ["weights_file", "out_dir", "csv", "path2"])
    def test_a_whole_word_is(self, project, parameter):
        (candidate,) = auto_stage.discover(named_for_paths, (), {parameter: "train"})

        assert candidate.written == "train"

    @pytest.mark.parametrize("vague", ["data", "input", "root", "input_data"])
    def test_vague_words_are_not_hints(self, vague):
        assert not auto_stage._named_like_a_path(vague)


class TestIdentity:
    """B4: what a copy is named after decides whether a stale one is served."""

    def test_a_directory_changed_behind_the_same_size_and_time_is_a_new_input(
        self, project
    ):
        folder, table = project / "data", project / "data" / "table.csv"
        before = auto_stage._identity(folder, auto_stage._files_under(folder))
        stat = table.stat()
        table.write_text("a,b\n9,9\n")
        os.utime(table, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert table.stat().st_size == stat.st_size
        assert table.stat().st_mtime_ns == stat.st_mtime_ns

        after = auto_stage._identity(folder, auto_stage._files_under(folder))

        assert before[1] is True and after[1] is True
        assert before[0] != after[0]

    def test_above_the_limit_it_is_metadata_and_says_so(self, project, monkeypatch):
        monkeypatch.setattr(auto_stage, "_HASH_LIMIT_BYTES", 4)
        folder, table = project / "data", project / "data" / "table.csv"

        for path in (folder, table):
            identity, by_content = auto_stage._identity(
                path, auto_stage._files_under(path)
            )
            assert by_content is False and len(identity) == 20

        plan = auto_stage.DataPlan(
            [
                auto_stage.StagedInput(
                    folder, "/store/x/data", 8, "uploaded", "t", None, False
                )
            ]
        )
        assert "identified by size and time, not content" in plan.explain()

    def test_the_same_bytes_under_two_names_are_two_inputs(self, project):
        """They are stored as ``<identity>/<name>``, and one identity is
        declared complete as a whole."""
        twin = project / "data" / "twin.csv"
        twin.write_text((project / "data" / "table.csv").read_text())
        table = project / "data" / "table.csv"

        assert (
            auto_stage._identity(table, [("", table)])[0]
            != auto_stage._identity(twin, [("", twin)])[0]
        )


class TestRewritingKeepsWhatItDidNotChange:
    """H2: one rewritten path used to rebuild every container as a builtin."""

    REPLACEMENTS = {"/local/w.bin": "/cluster/w.bin"}

    def test_an_argument_with_no_path_in_it_is_the_same_object(self):
        import collections

        Point = collections.namedtuple("Point", "path n")
        untouched = [
            Point("x", 1),
            collections.defaultdict(int, a=1),
            collections.OrderedDict(a=1),
            {"/somewhere/else"},
            [1, [2, {"k": ("v",)}]],
        ]

        for value in untouched:
            assert auto_stage._rewritten(value, self.REPLACEMENTS) is value

    def test_a_named_tuple_stays_one(self):
        import collections

        Point = collections.namedtuple("Point", "path n")

        result = auto_stage._rewritten(Point("/local/w.bin", 3), self.REPLACEMENTS)

        assert type(result) is Point and result == Point("/cluster/w.bin", 3)

    def test_dict_subclasses_keep_their_type_order_and_factory(self):
        import collections

        counts = collections.defaultdict(list, first="/local/w.bin", second=2)
        ordered = collections.OrderedDict(z="/local/w.bin", a=1)

        new_counts = auto_stage._rewritten(counts, self.REPLACEMENTS)
        new_ordered = auto_stage._rewritten(ordered, self.REPLACEMENTS)

        assert type(new_counts) is collections.defaultdict
        assert new_counts.default_factory is list
        assert new_counts == {"first": "/cluster/w.bin", "second": 2}
        assert type(new_ordered) is collections.OrderedDict
        assert list(new_ordered.items()) == [("z", "/cluster/w.bin"), ("a", 1)]
        assert counts["first"] == "/local/w.bin", "the caller's object is not edited"

    def test_a_path_inside_a_set_is_rewritten(self):
        assert auto_stage._rewritten({"/local/w.bin", "other"}, self.REPLACEMENTS) == {
            "/cluster/w.bin",
            "other",
        }
        frozen = auto_stage._rewritten(frozenset({"/local/w.bin"}), self.REPLACEMENTS)
        assert frozen == frozenset({"/cluster/w.bin"}) and type(frozen) is frozenset

    def test_list_and_tuple_subclasses_keep_their_type(self):
        class Batch(list):
            pass

        class Pair(tuple):
            pass

        batch = Batch(["/local/w.bin", 1])
        batch.label = "kept"

        new_batch = auto_stage._rewritten(batch, self.REPLACEMENTS)
        new_pair = auto_stage._rewritten(Pair(("/local/w.bin", 1)), self.REPLACEMENTS)

        assert type(new_batch) is Batch and new_batch == ["/cluster/w.bin", 1]
        assert new_batch.label == "kept" and batch[0] == "/local/w.bin"
        assert type(new_pair) is Pair and new_pair == ("/cluster/w.bin", 1)

    def test_only_the_changed_branch_is_rebuilt(self):
        untouched = {"deep": [1, 2]}
        value = [untouched, ["/local/w.bin"]]

        result = auto_stage._rewritten(value, self.REPLACEMENTS)

        assert result == [untouched, ["/cluster/w.bin"]]
        assert result[0] is untouched and result is not value

    def test_objects_are_not_descended_into(self):
        """The documented limit: a path held by a dataclass is not found by
        ``discover`` either, so it is neither uploaded nor rewritten."""
        import dataclasses

        @dataclasses.dataclass
        class Settings:
            weights: str

        settings = Settings("/local/w.bin")

        assert auto_stage._rewritten(settings, self.REPLACEMENTS) is settings
        assert settings.weights == "/local/w.bin"


class TestLinksOnASharedFilesystem:
    """H1, against the in-process SSH server, which shares this filesystem."""

    connected = TestOnACluster.connected

    def test_a_relative_path_used_in_place_is_still_linked(self, project, connected):
        """The job runs in its own directory: ``open("data/table.csv")``
        finds nothing there unless a link says where the file is."""
        connection, config, remote = connected

        args, _, plan = auto_stage.prepare(connection, config, reads_a_literal, (), {})

        (staged,) = plan.inputs
        table = project / "data" / "table.csv"
        assert staged.how == "in place" and staged.remote == str(table)
        assert plan.links == [("data/table.csv", str(table))]

        job_dir = remote / "job"
        job_dir.mkdir()
        auto_stage.link_into_job_directory(connection, str(job_dir), plan.links)
        assert (job_dir / "data" / "table.csv").read_text() == "a,b\n1,2\n"

    def test_a_link_inside_a_linked_directory_is_not_made(self, project, connected):
        """It would be created *through* the outer link: inside the user's
        own directory, replacing the file with a link to itself."""
        connection, config, remote = connected

        def reads_both(folder):
            return open("data/table.csv").read(), os.listdir(folder)

        _, _, plan = auto_stage.prepare(connection, config, reads_both, ("data",), {})

        assert {item.link for item in plan.inputs} == {"data", "data/table.csv"}
        assert plan.links == [("data", str(project / "data"))]
        job_dir = remote / "job"
        job_dir.mkdir()
        auto_stage.link_into_job_directory(connection, str(job_dir), plan.links)
        table = project / "data" / "table.csv"
        assert not table.is_symlink() and table.read_text() == "a,b\n1,2\n"
        assert (job_dir / "data" / "table.csv").read_text() == "a,b\n1,2\n"

    def test_sizes_are_matched_to_paths_by_index_not_by_position(
        self, project, connected
    ):
        """A path whose answer never arrives must not shift the others."""
        connection, _, _ = connected
        table, folder = project / "data" / "table.csv", project / "data"
        odd = project / "data" / "a name; echo 7\nwith a newline"
        odd.write_text("12345")

        sizes = auto_stage._remote_sizes(
            connection, [str(project / "absent"), str(odd), str(table), str(folder)]
        )

        assert sizes == {
            str(project / "absent"): None,
            str(odd): 5,
            str(table): table.stat().st_size,
            str(folder): -1,
        }


# -- #176: what an adversarial review found ---------------------------------


def calls_an_installed_library():
    import installed_fakelib  # noqa: F401 - imported by the test first

    return installed_fakelib.login()


class TestTheHomeDirectoryIsNotAProject:
    """S1. With the home directory as the working directory and no git
    repository, the "project" was the home directory, so a constant in a
    library under ``~/.local/lib/.../site-packages`` naming a token file
    got that file uploaded."""

    @pytest.fixture
    def at_home(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / ".kaggle").mkdir(parents=True)
        (home / ".kaggle" / "kaggle.json").write_text('{"key": "k"}')
        (home / "notes.txt").write_text("plain\n")
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.chdir(home)
        return home

    def test_home_and_what_contains_it_are_no_project_root(self, at_home, monkeypatch):
        assert auto_stage.project_root() is None
        monkeypatch.chdir(at_home.parent)
        assert auto_stage.project_root() is None
        assert auto_stage.project_root(Path("/")) is None

    def test_nothing_named_in_code_is_found_from_there(self, at_home, caplog):
        def names_a_file_in_home():
            return open("notes.txt").read(), open("~/.kaggle/kaggle.json").read()

        with caplog.at_level("INFO", logger="clustrix.auto_stage"):
            found = auto_stage.discover(names_a_file_in_home, (), {})

        assert found == []
        assert "not a project" in caplog.text

    def test_arguments_and_listed_data_are_still_found(self, at_home):
        found = _found(
            auto_stage.discover(takes_paths, ("notes.txt",), {}, extra=["notes.txt"])
        )

        assert found["notes.txt"].explicit

    def test_a_constant_is_refused_when_there_is_no_project(self, at_home):
        candidate = auto_stage.Candidate(
            "notes.txt", at_home / "notes.txt", "the code of f", False
        )

        assert "no project directory" in auto_stage._refusal(candidate, None)

    def test_installed_code_is_not_project_code_even_inside_the_project(
        self, project, monkeypatch
    ):
        """A virtual environment inside the project puts site-packages
        under the project root; what is installed there is still not the
        user's code."""
        import sys

        site = project / "venv" / "lib" / "python3" / "site-packages"
        site.mkdir(parents=True)
        module = _import_from(
            site / "installed_fakelib.py",
            "def login():\n    return open('data/table.csv').read()\n",
        )
        monkeypatch.setitem(sys.modules, "installed_fakelib", module)
        root = auto_stage.project_root()

        assert Path(module.login.__code__.co_filename).is_relative_to(root)
        assert not auto_stage._is_project_function(module.login, root)
        assert auto_stage.discover(calls_an_installed_library, (), {}) == []
        # ... and decorating the library's function does not make it so.
        assert auto_stage.discover(module.login, (), {}) == []

    def test_the_same_code_outside_site_packages_is_searched(self, project):
        module = _import_from(
            project / "ownlib.py",
            "def login():\n    return open('data/table.csv').read()\n",
        )

        assert TABLE in _found(auto_stage.discover(module.login, (), {}))


class TestCredentialsAreRecognised:
    @pytest.mark.parametrize(
        "name",
        [
            ".cache/huggingface/token",
            ".huggingface/token",
            "hf_home/huggingface/token",
            ".pgpass",
            ".my.cnf",
            "prod.env",
            ".env",
            ".vault-token",
            ".netrc",
            "_netrc",
            ".kaggle/kaggle.json",
            "elsewhere/kaggle.json",
            ".config/gcloud/application_default_credentials.json",
            "application_default_credentials.json",
            ".docker/config.json",
            ".npmrc",
            ".pypirc",
            ".aws/credentials",
            ".aws/config",
            ".azure/accessTokens.json",
            ".config/gcloud/access_tokens.db",
            "server.pem",
            "server.key",
            "id_rsa",
            "id_rsa.pub",
            "id_ed25519",
            "id_ecdsa_sk",
        ],
    )
    def test_by_name(self, tmp_path, name):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
        candidate = auto_stage.Candidate(str(path), path, "argument 'path'", True)

        assert "credential" in auto_stage._refusal(candidate, tmp_path)

    def test_a_service_account_key_by_what_it_says_it_is(self, tmp_path):
        key = tmp_path / "my-project-1a2b3c.json"
        key.write_text('{\n  "type" : "service_account",\n  "private_key": "..."\n}')
        ordinary = tmp_path / "settings.json"
        ordinary.write_text('{"type": "experiment", "account": "service"}')
        big = tmp_path / "big.json"
        big.write_text('{"type": "service_account", "pad": "' + "x" * 70000 + '"}')

        refused = {
            path.name: auto_stage._refusal(
                auto_stage.Candidate(str(path), path, "argument", True), tmp_path
            )
            for path in (key, ordinary, big)
        }

        assert "credential" in refused["my-project-1a2b3c.json"]
        assert refused["settings.json"] is None
        # Only a small file is opened to look; this is the stated limit.
        assert refused["big.json"] is None

    def test_ordinary_data_is_not(self, tmp_path):
        for name in ("table.csv", "environment.yml", "tokens.txt", "keys.npy"):
            path = tmp_path / name
            path.write_text("x")
            candidate = auto_stage.Candidate(str(path), path, "argument", True)
            assert auto_stage._refusal(candidate, tmp_path) is None, name


class TestALinkedParentDoesNotHideACredential:
    """S2. ``keys -> ~/.ssh``: neither ``keys/aws-prod`` nor ``keys`` is
    itself credential-shaped, and both were uploaded."""

    @pytest.fixture
    def linked(self, project, tmp_path):
        ssh = tmp_path / "home" / ".ssh"
        ssh.mkdir(parents=True)
        (ssh / "aws-prod").write_text("PRIVATE KEY MATERIAL\n")
        (ssh / "config").write_text("Host x\n")
        (project / "keys").symlink_to(ssh)
        return project

    def test_a_file_reached_through_a_linked_directory(self, linked):
        (candidate,) = auto_stage.discover(takes_paths, ("keys/aws-prod",), {})

        refusal = auto_stage._refusal(candidate, auto_stage.project_root())

        assert "credential" in refusal and ".ssh/aws-prod" in refusal

    def test_the_linked_directory_itself(self, linked):
        (candidate,) = auto_stage.discover(takes_folder, ("keys",), {})

        assert "credential" in auto_stage._refusal(candidate, auto_stage.project_root())

    def test_members_are_judged_by_their_real_path_too(self, linked, tmp_path):
        """Walking a directory reached through a link: every member's real
        path is under ``.ssh``, whatever the path walked says."""
        walked = linked / "keys"
        left_out = {}

        files = auto_stage._files_under(walked, left_out=left_out)

        assert files == []
        assert {m.name for m in left_out["credential"]} == {"aws-prod", "config"}


class TestTheSameDataNotJustTheSamePath:
    """S4. Against the in-process SSH server, which shares this disk, so
    every local path "exists on the cluster". What differs is produced the
    only way one disk allows: the file changes after this side looked."""

    connected = TestOnACluster.connected

    @pytest.fixture(autouse=True)
    def forget(self):
        auto_stage._SAME_IN_PLACE.clear()
        auto_stage._LOCAL_SHA256.clear()

    def test_the_cluster_reports_sizes_and_hashes_of_what_it_holds(
        self, project, connected
    ):
        import hashlib

        connection, _, _ = connected
        folder, table = project / "data", project / "data" / "table.csv"
        (folder / "sp ace 'q\" $(x).txt").write_text("odd name\n")
        (folder / "__pycache__").mkdir()
        (folder / "__pycache__" / "x.pyc").write_bytes(b"never staged")

        listing = auto_stage._remote_listing(connection, [str(folder)])[str(folder)]
        hashes = auto_stage._remote_hashes(
            connection, [(str(folder), True), (str(table), False)]
        )

        assert listing == {
            "table.csv": 8,
            "subjects/s1.txt": 10,
            "subjects/s2.txt": 10,
            "sp ace 'q\" $(x).txt": 9,
        }
        digest = hashlib.sha256(table.read_bytes()).hexdigest()
        assert hashes[str(table)] == {"": digest}
        assert hashes[str(folder)]["table.csv"] == digest
        assert set(hashes[str(folder)]) == set(listing)

    def test_identical_data_is_used_in_place_and_the_plan_says_why(
        self, project, connected, caplog
    ):
        connection, config, remote = connected

        with caplog.at_level("INFO", logger="clustrix.auto_stage"):
            _, _, plan = auto_stage.prepare(
                connection, config, takes_paths, (project / "data" / "table.csv",), {}
            )
            _, _, folder_plan = auto_stage.prepare(
                connection, config, takes_folder, (str(project / "data"),), {}
            )

        assert plan.inputs[0].how == "in place"
        assert "same size and sha256" in plan.inputs[0].why
        assert "same 3 file(s) by size and sha256" in folder_plan.inputs[0].why
        assert "in place (the cluster has it at the same path: same size" in (
            caplog.text
        )
        assert not (remote / "work" / auto_stage.STORE_DIRECTORY).exists()

    @pytest.fixture
    def small_blocks(self, monkeypatch):
        """The limits lowered, instead of writing 256 MB: anything over 4
        bytes is "too big to read whole", and a block is 16 bytes."""
        monkeypatch.setattr(auto_stage, "_HASH_LIMIT_BYTES", 4)
        monkeypatch.setattr(auto_stage, "_SAMPLE_BLOCK_BYTES", 16)

    def test_data_too_big_to_read_whole_is_compared_by_sampled_blocks(
        self, project, connected, small_blocks
    ):
        connection, config, remote = connected
        big = project / "data" / "big.bin"
        big.write_bytes(os.urandom(16 * 40 + 5))

        _, _, plan = auto_stage.prepare(
            connection, config, takes_folder, (str(project / "data"),), {}
        )

        (staged,) = plan.inputs
        assert staged.how == "in place"
        assert "same names and sizes, and the same sha256 of" in staged.why
        assert "sampled block(s) of 4 of 4 file(s)" in staged.why
        assert "the rest was not compared" in staged.why
        assert auto_stage._LOCAL_SHA256 == {}, "no file was read whole"

    def test_the_cluster_hashes_the_same_blocks_this_side_does(
        self, tmp_path, connected, small_blocks
    ):
        """Real ``dd`` and a real SHA-256 command on the far side, for
        whole blocks, a short last block, a block read twice over the
        choices, and a file with nothing in it."""
        connection, _, _ = connected
        data = tmp_path / "data.bin"
        data.write_bytes(os.urandom(16 * 10 + 7))
        empty = tmp_path / "empty.bin"
        empty.write_bytes(b"")
        choices = [[0, 10], [0, 3, 4, 9, 10], [5], [10]]

        there = auto_stage._remote_sample_digests(
            connection,
            [(str(data), blocks) for blocks in choices] + [(str(empty), [0])],
        )

        here = [auto_stage._local_sample_digest(data, blocks) for blocks in choices]
        assert there == here + [auto_stage._local_sample_digest(empty, [0])]
        assert len(set(here)) == len(here), "different blocks, different digests"

    def test_one_changed_byte_in_a_sampled_block_is_a_different_digest(
        self, tmp_path, connected, small_blocks
    ):
        """What a stale copy of the same size looks like. One machine cannot
        hold two files at one path, so the copy has another name."""
        connection, _, _ = connected
        content = bytearray(os.urandom(16 * 10))
        ours = tmp_path / "ours.bin"
        ours.write_bytes(content)
        content[16 * 6 + 3] ^= 1
        theirs = tmp_path / "theirs.bin"
        theirs.write_bytes(content)

        hit, missed, unreadable = auto_stage._remote_sample_digests(
            connection,
            [
                (str(theirs), [0, 6, 9]),
                (str(theirs), [0, 5, 9]),
                (str(tmp_path / "absent.bin"), [0]),
            ],
        )

        assert hit != auto_stage._local_sample_digest(ours, [0, 6, 9])
        # The limit of sampling, on purpose: a block not drawn is not seen.
        assert missed == auto_stage._local_sample_digest(ours, [0, 5, 9])
        assert unreadable is None

    def test_the_blocks_chosen_cover_both_ends_and_stay_within_the_budget(
        self, tmp_path, small_blocks
    ):
        import random

        files = []
        for number in range(100):
            file = tmp_path / f"{number:03}.bin"
            file.write_bytes(b"x" * (16 * (number % 7) + number % 3))
            files.append((file.name, file))
        large = tmp_path / "large.bin"
        large.write_bytes(b"y" * (16 * 1000))

        many = auto_stage._choose_samples(files, random.Random(1))
        one = auto_stage._choose_samples([("", large)], random.Random(1))
        again = auto_stage._choose_samples([("", large)], random.Random(2))

        assert len(many) == auto_stage._SAMPLE_BLOCKS // 2
        assert sum(len(blocks) for _, _, blocks in many) <= auto_stage._SAMPLE_BLOCKS
        for _, file, blocks in many:
            last = max(1, -(-file.stat().st_size // 16)) - 1
            assert blocks[0] == 0 and blocks[-1] == last
        (_, _, blocks), (_, _, other) = one[0], again[0]
        assert blocks[0] == 0 and blocks[-1] == 999
        assert 32 < len(blocks) <= auto_stage._SAMPLE_BLOCKS
        assert blocks != other, "drawn afresh each time"

    def test_a_file_of_the_same_size_and_different_content_stops_the_call(
        self, project, connected
    ):
        """Hashed here, then rewritten with the same size and the same
        modification time: what the cluster hashes is no longer what this
        side hashed, which is what a stale copy on a cluster is."""
        from clustrix.staging import StagingError

        connection, config, remote = connected
        table = project / "data" / "table.csv"
        before = table.stat()
        auto_stage._local_sha256(table)
        table.write_text("a,b\n9,9\n")
        os.utime(table, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert table.stat().st_size == before.st_size

        with pytest.raises(StagingError) as refused:
            auto_stage.prepare(connection, config, takes_paths, (table,), {})

        message = str(refused.value)
        assert "not the same data" in message and "different content" in message
        assert "no_data" in message

    def test_the_call_runs_on_the_cluster_s_copy_when_asked_and_says_so(
        self, project, connected, caplog
    ):
        """``auto_stage_on_mismatch="use_cluster"``: the same stale copy as
        above no longer stops the call. Nothing is uploaded, the argument
        still names the path on the cluster, and both the plan and a
        warning say the data is not what this machine has."""
        connection, config, remote = connected
        config.auto_stage_on_mismatch = "use_cluster"
        table = project / "data" / "table.csv"
        before = table.stat()
        auto_stage._local_sha256(table)
        table.write_text("a,b\n9,9\n")
        os.utime(table, ns=(before.st_atime_ns, before.st_mtime_ns))

        with caplog.at_level("WARNING", logger="clustrix.auto_stage"):
            args, _, plan = auto_stage.prepare(
                connection, config, takes_paths, (table,), {}
            )

        (staged,) = plan.inputs
        assert staged.how == "in place" and args == (table,)
        assert "NOT what is here" in staged.why and "use_cluster" in staged.why
        assert "is not what is here" in caplog.text
        assert "has different content" in caplog.text
        assert not (remote / "work" / auto_stage.STORE_DIRECTORY).exists()

    def test_data_that_matches_draws_no_warning_under_use_cluster(
        self, project, connected, caplog
    ):
        connection, config, _ = connected
        config.auto_stage_on_mismatch = "use_cluster"

        with caplog.at_level("WARNING", logger="clustrix.auto_stage"):
            _, _, plan = auto_stage.prepare(
                connection, config, takes_paths, (project / "data" / "table.csv",), {}
            )

        assert "same size and sha256" in plan.inputs[0].why
        assert caplog.text == ""

    def test_a_directory_is_compared_member_by_member(self, project, connected):
        connection, _, _ = connected
        folder = project / "data"
        candidate = auto_stage.Candidate(str(folder), folder, "argument", True)
        files = auto_stage._files_under(folder)

        def verdict():
            auto_stage._SAME_IN_PLACE.clear()
            return auto_stage._compare_in_place(connection, [(candidate, files, 0)])[
                folder
            ]

        assert verdict().same is True

        # Same names, same sizes, one file's bytes changed behind this
        # side's hash of it.
        member = folder / "subjects" / "s1.txt"
        before = member.stat()
        member.write_text("SUBJECT 1\n")
        os.utime(member, ns=(before.st_atime_ns, before.st_mtime_ns))
        changed = verdict()
        assert changed.same is False
        assert changed.detail == "subjects/s1.txt has different content"

        # A member whose size on the cluster is no longer the size listed
        # here is found without hashing anything.
        files[0] = ("subjects/s1.txt", folder / "table.csv")
        resized = verdict()
        assert resized.same is False and "subjects/s1.txt is 10 B there" in (
            resized.detail
        )

    def test_listings_differ_by_names_and_sizes_but_not_by_what_was_left_out(self):
        here = {"a.csv": 3, "b.csv": 4, "c.csv": 5}
        there = {"a.csv": 3, "b.csv": 40, "extra.csv": 1, ".env": 9}

        differences = auto_stage._listing_differences(
            here, there, lambda relative: relative == ".env"
        )

        assert differences == [
            "c.csv is not there",
            "extra.csv is only there",
            "b.csv is 40 B there and 4 B here",
        ]
        assert auto_stage._listing_differences(here, dict(here), bool) == []

    def test_a_cluster_that_cannot_hash_gets_an_upload_not_the_benefit_of_the_doubt(
        self, project, tmp_path
    ):
        """A real remote with no ``sha256sum`` and no ``shasum`` on its
        PATH: the tools the rest of staging needs are there, those two are
        not."""
        import shutil

        tools = tmp_path / "tools"
        tools.mkdir()
        for tool in ("sh", "find", "wc", "mkdir", "chmod", "stat", "touch", "mv"):
            (tools / tool).symlink_to(shutil.which(tool))
        for tool in ("rm", "sleep", "cat", "ls", "test", "echo", "pwd", "printf"):
            (tools / tool).symlink_to(shutil.which(tool))
        remote = tmp_path / "remote"
        remote.mkdir()
        table = project / "data" / "table.csv"
        with LocalSSHServer(
            root=str(remote), password="s3cret", env={"PATH": str(tools)}
        ) as server:
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
                connection = executor.connection_manager
                assert auto_stage._remote_hashes(connection, [(str(table), False)]) == {
                    str(table): None
                }

                args, _, plan = auto_stage.prepare(
                    connection, config, takes_paths, (table,), {}
                )

        (staged,) = plan.inputs
        assert staged.how == "uploaded"
        assert "could not compute a sha256" in staged.why
        assert Path(args[0]).read_text() == "a,b\n1,2\n" and args[0] != table

    def test_a_comparison_is_made_once_per_session_and_again_after_an_edit(
        self, project, connected
    ):
        connection, config, _ = connected
        table = project / "data" / "table.csv"

        def why():
            return (
                auto_stage.prepare(connection, config, takes_paths, (table,), {})[2]
                .inputs[0]
                .why
            )

        assert "same size and sha256" in why()
        assert "found to be the same earlier in this session" in why()
        table.write_text("a,b\n1,2\n3,4\n")
        assert "same size and sha256" in why()


class TestNamesClustrixWritesItself:
    """S5. ``result.pkl`` passed as an argument was linked over the job's
    own ``result.pkl``."""

    connected = TestOnACluster.connected

    @pytest.mark.parametrize(
        "name",
        ["result.pkl", "job.sh", "function_data.pkl", "job.out", "slurm-12.err"],
    )
    def test_a_colliding_relative_input_is_refused_by_name(
        self, project, connected, name
    ):
        from clustrix.staging import StagingError

        connection, config, remote = connected
        (project / name).write_text("mine\n")

        with pytest.raises(StagingError) as refused:
            auto_stage.prepare(connection, config, takes_paths, (name,), {})

        assert repr(name) in str(refused.value) and "collides" in str(refused.value)
        assert not (remote / "work" / auto_stage.STORE_DIRECTORY).exists()

    def test_a_directory_named_like_one_is_refused_too(self, project, connected):
        from clustrix.staging import StagingError

        connection, config, _ = connected
        (project / "venv2_execution").mkdir()
        (project / "venv2_execution" / "x.txt").write_text("x")

        with pytest.raises(StagingError):
            auto_stage.prepare(
                connection, config, takes_paths, ("venv2_execution/x.txt",), {}
            )

    def test_the_same_name_by_absolute_path_or_below_a_directory_is_fine(
        self, project, connected
    ):
        connection, config, _ = connected
        (project / "result.pkl").write_text("mine\n")
        (project / "data" / "result.pkl").write_text("mine too\n")

        _, _, plan = auto_stage.prepare(
            connection,
            config,
            takes_paths,
            (str(project / "result.pkl"),),
            {"options": ["data/result.pkl"]},
        )

        assert {item.link for item in plan.inputs} == {None, "data/result.pkl"}

    def test_every_file_a_generated_job_script_names_is_reserved(self):
        """The list is in ``clustrix.utils`` beside the generators. This
        reads what they generate, so a new file there fails here."""
        import re

        from clustrix.utils import create_job_script, is_reserved_job_directory_name

        config = ClusterConfig(cluster_type="ssh", remote_work_dir="/w")
        named = set()
        for cluster_type in ("ssh", "slurm"):
            script = create_job_script(
                cluster_type,
                {"cores": 1, "memory": "1GB", "time": "00:01:00"},
                "/w/job_1",
                config,
            )
            named |= set(re.findall(r"open\('([^'/]+)'", script))
            named |= {
                os.path.basename(path)
                for path in re.findall(r"/w/job_1/([\w.%-]+)", script)
            }

        assert {"function_data.pkl", "result.pkl", "error.pkl"} <= named
        unreserved = {
            name
            for name in named
            if not is_reserved_job_directory_name(name.replace("%j", "1"))
        }
        assert unreserved == set()


class TestAReadOnlyInputThatWasMeantAsAnOutput:
    """S6. The sentence added to a job's PermissionError, and when."""

    def _plan(self, **changes):
        item = auto_stage.StagedInput(
            Path("/home/me/project/data/out.txt"),
            "/remote/work/data/abc/out.txt",
            7,
            "uploaded",
            "argument 'out_path'",
            link="data/out.txt",
        )
        for name, value in changes.items():
            setattr(item, name, value)
        return auto_stage.DataPlan([item])

    @pytest.mark.parametrize(
        "failed",
        [
            "data/out.txt",
            "./data/out.txt",
            "/remote/work/job_1_ab/data/out.txt",
            "/remote/work/data/abc/out.txt",
        ],
    )
    def test_the_staged_path_however_the_job_spelled_it(self, failed):
        note = auto_stage.staged_read_only(
            PermissionError(13, "Permission denied", failed), self._plan()
        )

        assert "read-only" in note and "no_data=['data/out.txt']" in note

    def test_a_file_inside_a_staged_directory(self):
        plan = self._plan(link="results", remote="/remote/work/data/abc/results")

        note = auto_stage.staged_read_only(
            PermissionError(13, "Permission denied", "results/run1/metrics.json"),
            plan,
        )

        assert "no_data=['results']" in note

    def test_an_absolute_argument_is_named_by_its_local_path(self):
        plan = self._plan(link=None)

        note = auto_stage.staged_read_only(
            PermissionError(13, "Permission denied", "/remote/work/data/abc/out.txt"),
            plan,
        )

        assert "no_data=['/home/me/project/data/out.txt']" in note

    def test_other_paths_and_inputs_used_in_place_get_no_note(self):
        denied = PermissionError(13, "Permission denied", "/etc/shadow")
        assert auto_stage.staged_read_only(denied, self._plan()) is None
        assert auto_stage.staged_read_only(
            PermissionError("no name"), self._plan()
        ) is (None)
        in_place = self._plan(how="in place")
        assert (
            auto_stage.staged_read_only(
                PermissionError(13, "Permission denied", "data/out.txt"), in_place
            )
            is None
        )
        similar = PermissionError(13, "Permission denied", "data/out.txt.bak")
        assert auto_stage.staged_read_only(similar, self._plan()) is None
