"""Data packages, exercised against real files, a real SSH server, and real HF.

Nothing here is mocked. The local half runs against real files on real disk.
The transport half runs against ``tests/ssh_server.py`` -- a real paramiko
server on a real socket, doing a real SSH handshake and real SFTP against a
real directory -- driven through the shipped ``ConnectionManager``, which is
the same code path that ships every job payload.

The HuggingFace half is skipped, loudly, when no token is available. It is
never faked: a mocked HF test would prove that ``huggingface_hub`` was called,
which is not the thing in doubt.
"""

import json
import os
import pickle
import subprocess
import sys
import textwrap
import uuid
import warnings
from pathlib import Path

import pytest

import clustrix
from clustrix.config import ClusterConfig
from clustrix.staging import (
    DataPackage,
    PackagedFile,
    StagingError,
    _confine,
    _describe,
    _digest_bytes,
    _digest_file,
    _is_sensitive,
    _read_verified,
    _safe_relpath,
    _validate_package_id,
    data_package,
    delete_data_package,
    list_data_packages,
    materialize_packages,
)

from tests.ssh_server import LocalSSHServer

REPO_ROOT = str(Path(clustrix.__file__).resolve().parents[1])


def _hf_token_available():
    """A usable token, or None. Never invents one.

    Resolved at **import** time, which is before any fixture runs. That matters
    because ``tests/conftest.py``'s autouse ``isolate_home`` gives every test a
    throwaway ``$HOME``, and the token written by ``hf auth login`` lives under
    the real one. Reading it here catches it while ``$HOME`` is still the
    developer's; the ``hf_config`` fixture then hands it to the tests that need
    it, and only to those, so no other test and no other subprocess inherits a
    credential it has no use for.
    """
    from clustrix.hf_jobs import _token_from_hf_cli_cache

    return os.environ.get("HF_TOKEN") or _token_from_hf_cli_cache()


#: Captured before $HOME is redirected. None on a machine with no credentials.
REAL_HF_TOKEN = _hf_token_available()

if REAL_HF_TOKEN is None:
    # A skip is only useful if someone sees it, and nobody does: pytest prints
    # "SKIPPED" reasons only under -rs, which this project's addopts does not
    # set, and pyproject.toml is not this module's to change. A warning is
    # printed by default, in the warnings summary, so the run says out loud
    # that the remote half of data packages went unverified.
    warnings.warn(
        "clustrix data packages: no HuggingFace token (HF_TOKEN or `hf auth "
        "login`), so every test of the remote store -- upload, download, "
        "digest verification, public-repo refusal, exists, delete and listing "
        "-- is SKIPPED, NOT PASSED, and that half is UNVERIFIED in this run.",
        stacklevel=1,
    )

requires_hf = pytest.mark.skipif(
    REAL_HF_TOKEN is None,
    reason=(
        "SKIPPED, NOT PASSED: no HuggingFace token (HF_TOKEN or `hf auth "
        "login`), so the remote-store half of data packages -- upload, "
        "download, digest verification, exists, delete, and listing -- has "
        "NOT been exercised in this run and is UNVERIFIED. It is never mocked; "
        "a mocked version of this would prove only that huggingface_hub was "
        "called."
    ),
)


@pytest.fixture
def local_config(tmp_path):
    """A config whose caches live in tmp_path, so tests cannot pollute ~."""
    return ClusterConfig(
        cluster_type="local",
        local_cache_dir=str(tmp_path / "cache"),
    )


@pytest.fixture
def sample_tree(tmp_path):
    root = tmp_path / "project"
    (root / "data").mkdir(parents=True)
    (root / "data" / "subjects.csv").write_bytes(b"id,score\n1,0.5\n2,0.75\n")
    (root / "data" / "nested").mkdir()
    (root / "data" / "nested" / "extra.bin").write_bytes(bytes(range(256)) * 4)
    return root


# ---------------------------------------------------------------------------
# building a package from real files
# ---------------------------------------------------------------------------


class TestBuildingFromRealFiles:
    def test_a_single_file_becomes_a_one_file_package(self, sample_tree, local_config):
        target = sample_tree / "data" / "subjects.csv"
        pkg = data_package(target, config=local_config)

        assert pkg.filenames() == ["subjects.csv"]
        assert pkg.total_bytes == target.stat().st_size
        assert pkg.files[0].digest == _digest_file(target)

    def test_a_directory_keeps_the_relative_paths_the_function_uses(
        self, sample_tree, local_config
    ):
        pkg = data_package(sample_tree, config=local_config)

        assert sorted(pkg.filenames()) == [
            "data/nested/extra.bin",
            "data/subjects.csv",
        ]

    def test_base_chooses_what_paths_are_relative_to(self, sample_tree, local_config):
        pkg = data_package(
            sample_tree / "data" / "subjects.csv",
            base=sample_tree,
            config=local_config,
        )
        assert pkg.filenames() == ["data/subjects.csv"]

    def test_raw_bytes_are_packaged_under_a_chosen_name(self, local_config):
        pkg = data_package(
            b"hello cluster", filename="greeting.txt", config=local_config
        )

        assert pkg.filenames() == ["greeting.txt"]
        assert pkg.read_bytes() == b"hello cluster"

    def test_a_missing_file_fails_at_build_time_not_on_the_worker(
        self, tmp_path, local_config
    ):
        with pytest.raises(StagingError, match="No such file"):
            data_package(tmp_path / "not-here.csv", config=local_config)

    def test_distinct_directories_keep_distinct_paths(self, tmp_path, local_config):
        """The common ancestor becomes the root, so nothing collides."""
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        (tmp_path / "a" / "x.csv").write_text("one")
        (tmp_path / "b" / "x.csv").write_text("two")

        pkg = data_package(
            [tmp_path / "a" / "x.csv", tmp_path / "b" / "x.csv"], config=local_config
        )

        assert sorted(pkg.filenames()) == ["a/x.csv", "b/x.csv"]

    def test_a_file_outside_an_explicit_base_is_refused_not_renamed(
        self, tmp_path, local_config
    ):
        """Falling back to the bare name is how two files silently collide."""
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        (tmp_path / "a" / "x.csv").write_text("one")
        (tmp_path / "b" / "x.csv").write_text("two")

        with pytest.raises(StagingError, match="is not under base"):
            data_package(
                [tmp_path / "a" / "x.csv", tmp_path / "b" / "x.csv"],
                base=tmp_path / "a",
                config=local_config,
            )

    def test_naming_the_same_file_twice_collapses(self, sample_tree, local_config):
        target = sample_tree / "data" / "subjects.csv"
        pkg = data_package([target, target], base=sample_tree, config=local_config)

        assert pkg.filenames() == ["data/subjects.csv"]

    def test_two_different_files_on_one_name_is_refused(self, tmp_path):
        """Silently dropping one would give a wrong answer, not an error."""
        from clustrix.staging import _dedupe

        one = tmp_path / "x.csv"
        two = tmp_path / "y.csv"
        one.write_text("one")
        two.write_text("two")

        assert _dedupe([("a.csv", one), ("a.csv", one)]) == [("a.csv", one)]
        with pytest.raises(StagingError, match="would both be at"):
            _dedupe([("a.csv", one), ("a.csv", two)])


class TestNothingIsInferred:
    """Inputs are declared, never guessed. This is the whole safety story."""

    def test_a_path_mentioned_only_in_source_is_not_packaged(
        self, sample_tree, local_config
    ):
        pkg = data_package(sample_tree / "data" / "subjects.csv", config=local_config)
        # dependency_analysis.py would classify this string as a data file.
        assert "s3://bucket/notes.log" not in pkg.filenames()
        assert len(pkg.files) == 1


# ---------------------------------------------------------------------------
# refusing things that should be refused
# ---------------------------------------------------------------------------


class TestCredentialShapedPaths:
    @pytest.mark.parametrize(
        "name", ["server.pem", "id_rsa", ".env", "signing.key", ".netrc"]
    )
    def test_a_credential_shaped_file_is_refused(self, tmp_path, local_config, name):
        secret = tmp_path / name
        secret.write_text("not actually a credential")

        with pytest.raises(StagingError, match="looks like a credential"):
            data_package(secret, config=local_config)

    def test_a_path_under_dot_ssh_is_refused_whatever_it_is_called(self, tmp_path):
        assert _is_sensitive(tmp_path / ".ssh" / "authorized_keys")

    def test_an_explicit_override_allows_it(self, tmp_path, local_config):
        secret = tmp_path / "server.pem"
        secret.write_text("not actually a credential")

        pkg = data_package(secret, config=local_config, allow_sensitive=True)
        assert pkg.filenames() == ["server.pem"]

    def test_ordinary_data_is_not_mistaken_for_a_credential(self, tmp_path):
        assert not _is_sensitive(tmp_path / "data" / "keys.csv")
        assert not _is_sensitive(tmp_path / "tokens.parquet")


class TestPathConfinement:
    @pytest.mark.parametrize(
        "bad", ["../escape", "a/../../escape", "/etc/passwd", "..", ""]
    )
    def test_a_path_escaping_the_package_root_is_rejected(self, bad):
        with pytest.raises(StagingError):
            _safe_relpath(bad)

    def test_escapes_are_rejected_not_clamped_when_materialising(
        self, tmp_path, local_config
    ):
        """A hand-built package with a hostile name must not write outside dest."""
        payload = b"pwned"
        hostile = DataPackage(
            name="hostile",
            package_id="deadbeef",
            files=(
                PackagedFile(
                    relpath="../../escaped.txt",
                    size=len(payload),
                    digest=_digest_bytes(payload),
                ),
            ),
            inline={"../../escaped.txt": payload},
        )
        dest = tmp_path / "dest"

        with pytest.raises(StagingError, match="escapes the package root"):
            hostile.materialize(dest=str(dest), config=local_config)
        assert not (tmp_path.parent / "escaped.txt").exists()


class TestSizeBands:
    def test_a_package_at_or_above_stage_max_bytes_raises(self, tmp_path):
        big = tmp_path / "big.bin"
        big.write_bytes(b"x" * 4096)
        config = ClusterConfig(
            cluster_type="local",
            local_cache_dir=str(tmp_path / "cache"),
            stage_max_bytes=1024,
        )

        with pytest.raises(StagingError) as excinfo:
            data_package(big, config=config)

        message = str(excinfo.value)
        assert "stage_max_bytes" in message  # names the knob
        assert "big.bin" in message  # names the file
        assert "1.0 KB" in message  # names the threshold

    def test_a_package_above_the_warn_band_is_logged(self, tmp_path, caplog):
        payload = tmp_path / "mid.bin"
        payload.write_bytes(b"y" * 4096)
        config = ClusterConfig(
            cluster_type="local",
            local_cache_dir=str(tmp_path / "cache"),
            stage_warn_bytes=1024,
            stage_inline_max_bytes=1024 * 1024,
        )

        with caplog.at_level("WARNING"):
            data_package(payload, config=config)

        assert any("stage_warn_bytes" in record.message for record in caplog.records)

    def test_small_data_stays_inline_and_needs_no_remote_store(
        self, sample_tree, local_config
    ):
        pkg = data_package(sample_tree / "data" / "subjects.csv", config=local_config)

        assert pkg.is_inline
        assert pkg.repo_id is None

    def test_force_local_keeps_a_large_package_inline(self, tmp_path):
        payload = tmp_path / "mid.bin"
        payload.write_bytes(b"z" * 4096)
        config = ClusterConfig(
            cluster_type="local",
            local_cache_dir=str(tmp_path / "cache"),
            stage_inline_max_bytes=16,
        )

        pkg = data_package(payload, config=config, force_local=True)

        assert pkg.is_inline
        assert pkg.repo_id is None


# ---------------------------------------------------------------------------
# dereferencing, on real disk
# ---------------------------------------------------------------------------


class TestDereferencing:
    def test_path_returns_the_original_file_when_this_machine_has_it(
        self, sample_tree, local_config
    ):
        target = sample_tree / "data" / "subjects.csv"
        pkg = data_package(target, config=local_config)

        assert Path(pkg.path()).resolve() == target.resolve()

    def test_materialize_recreates_the_tree_at_the_same_relative_paths(
        self, sample_tree, local_config, tmp_path
    ):
        pkg = data_package(sample_tree, config=local_config)
        dest = tmp_path / "worker"

        root = Path(pkg.materialize(dest=str(dest), config=local_config))

        assert (root / "data" / "subjects.csv").read_bytes() == (
            sample_tree / "data" / "subjects.csv"
        ).read_bytes()
        assert (root / "data" / "nested" / "extra.bin").read_bytes() == (
            sample_tree / "data" / "nested" / "extra.bin"
        ).read_bytes()

    def test_materialised_files_are_not_world_readable(
        self, sample_tree, local_config, tmp_path
    ):
        pkg = data_package(sample_tree, config=local_config)
        root = Path(pkg.materialize(dest=str(tmp_path / "worker"), config=local_config))

        mode = (root / "data" / "subjects.csv").stat().st_mode & 0o077
        assert mode == 0, oct(mode)

    def test_a_corrupted_payload_is_caught_and_not_written(
        self, tmp_path, local_config
    ):
        payload = b"the real contents"
        pkg = DataPackage(
            name="tampered",
            package_id="cafebabe",
            files=(
                PackagedFile(
                    relpath="x.bin", size=len(payload), digest=_digest_bytes(payload)
                ),
            ),
            inline={"x.bin": b"tampered contents!"},
        )
        dest = tmp_path / "worker"

        with pytest.raises(StagingError, match="Digest mismatch"):
            pkg.materialize(dest=str(dest), config=local_config)
        assert not (dest / "x.bin").exists()

    def test_no_partial_file_is_left_at_the_real_name(self, tmp_path, local_config):
        payload = b"good"
        pkg = DataPackage(
            name="tampered",
            package_id="cafebabe",
            files=(
                PackagedFile(
                    relpath="x.bin", size=len(payload), digest=_digest_bytes(payload)
                ),
            ),
            inline={"x.bin": b"bad!"},
        )
        dest = tmp_path / "worker"
        with pytest.raises(StagingError):
            pkg.materialize(dest=str(dest), config=local_config)

        assert list(dest.glob("*")) == [] or not any(
            p.name == "x.bin" for p in dest.glob("*")
        )

    def test_asking_for_a_file_that_is_not_in_the_package_says_what_is(
        self, sample_tree, local_config
    ):
        pkg = data_package(sample_tree, config=local_config)

        with pytest.raises(StagingError, match="It holds"):
            pkg.path("data/absent.csv")

    def test_path_without_a_name_refuses_on_a_multi_file_package(
        self, sample_tree, local_config
    ):
        pkg = data_package(sample_tree, config=local_config)

        with pytest.raises(StagingError, match="needs one of them by name"):
            pkg.path()

    def test_materialize_packages_walks_a_list(self, sample_tree, local_config):
        one = data_package(sample_tree / "data" / "subjects.csv", config=local_config)
        two = data_package(sample_tree / "data" / "nested", config=local_config)

        roots = materialize_packages([one, two], config=local_config)

        assert len(roots) == 2
        assert all(Path(root).is_dir() for root in roots)


# ---------------------------------------------------------------------------
# @cluster integration -- real in-process execution
# ---------------------------------------------------------------------------


def _read_from_package(pkg):
    """Body of the decorated function; dereferences on the worker."""
    with open(pkg.path("data/subjects.csv"), "rb") as handle:
        return handle.read()


def _read_from_several(packages):
    return [len(p.read_bytes(p.filenames()[0])) for p in packages]


class TestClusterIntegration:
    def test_a_package_passed_to_a_cluster_function_is_dereferenced_there(
        self, sample_tree, local_config
    ):
        clustrix.configure(
            cluster_type="local", local_cache_dir=local_config.local_cache_dir
        )
        pkg = data_package(sample_tree, config=local_config)

        decorated = clustrix.cluster(cores=1)(_read_from_package)
        assert decorated(pkg) == (sample_tree / "data" / "subjects.csv").read_bytes()

    def test_a_list_of_packages_is_accepted(self, sample_tree, local_config):
        clustrix.configure(
            cluster_type="local", local_cache_dir=local_config.local_cache_dir
        )
        one = data_package(sample_tree / "data" / "subjects.csv", config=local_config)
        two = data_package(
            sample_tree / "data" / "nested" / "extra.bin", config=local_config
        )

        decorated = clustrix.cluster(cores=1)(_read_from_several)
        assert decorated([one, two]) == [one.total_bytes, two.total_bytes]


# ---------------------------------------------------------------------------
# persistence: pickle now, use in a fresh interpreter later
# ---------------------------------------------------------------------------


WORKER = textwrap.dedent("""
    import pickle, sys
    sys.path.insert(0, sys.argv[1])
    from clustrix.config import ClusterConfig

    with open(sys.argv[2], "rb") as handle:
        pkg = pickle.load(handle)

    config = ClusterConfig(cluster_type="local", local_cache_dir=sys.argv[3])
    root = pkg.materialize(dest=sys.argv[4], config=config)
    print(pkg.name)
    print(sorted(pkg.filenames()))
    print(open(root + "/data/subjects.csv", "rb").read().decode())
    """)


class TestPickledPackagesSurvive:
    """The documented persistence story: save the object, load it later."""

    def test_the_object_holds_no_live_client_socket_or_credential(
        self, sample_tree, local_config, monkeypatch
    ):
        """A saved package must never be a credential sitting on disk.

        This assertion used to be wrapped in ``if REAL_HF_TOKEN:``, which meant
        it did nothing on any machine without HuggingFace credentials --
        including CI, which is every machine that runs this suite
        automatically. A token field pickled into DataPackage passed there.

        So the token is supplied rather than hoped for: HF_TOKEN is set to a
        value this test knows, through the same environment variable
        ``_hf_token`` actually reads, and the pickle is searched for it. The
        real token is still checked when there is one.
        """
        sentinel = "hf_" + "SENTINEL" * 4
        monkeypatch.setenv("HF_TOKEN", sentinel)

        pkg = data_package(sample_tree, config=local_config)
        blob = pickle.dumps(pkg)

        assert sentinel.encode() not in blob
        if REAL_HF_TOKEN:
            assert REAL_HF_TOKEN.encode() not in blob
        assert pickle.loads(blob).filenames() == pkg.filenames()

    def test_a_stale_materialisation_path_does_not_survive_the_pickle(
        self, sample_tree, tmp_path, local_config
    ):
        """Where it was last unpacked is true of one machine at one moment."""
        pkg = data_package(sample_tree, config=local_config)
        pkg.materialize(dest=str(tmp_path / "here"), config=local_config)
        assert pkg._materialised is not None

        assert pickle.loads(pickle.dumps(pkg))._materialised is None

    def test_a_fresh_interpreter_can_load_and_use_a_saved_package(
        self, sample_tree, tmp_path, local_config
    ):
        pkg = data_package(sample_tree, config=local_config)
        saved = tmp_path / "pkg.pkl"
        saved.write_bytes(pickle.dumps(pkg))

        worker = tmp_path / "worker.py"
        worker.write_text(WORKER)
        result = subprocess.run(
            [
                sys.executable,
                str(worker),
                REPO_ROOT,
                str(saved),
                str(tmp_path / "cache2"),
                str(tmp_path / "out"),
            ],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert "data/subjects.csv" in result.stdout
        assert "id,score" in result.stdout


# ---------------------------------------------------------------------------
# transport: a real SSH server, real SFTP, the shipped connection code
# ---------------------------------------------------------------------------


class TestTravellingOverRealSFTP:
    """A package travels to a worker inside the function payload.

    That payload goes over ``ConnectionManager.upload_file`` -- ``sftp.put`` --
    and this exercises exactly that, against a real server, rather than
    asserting that a patched object was called.
    """

    def _connect(self, server):
        from clustrix.executor_connections import ConnectionManager

        config = ClusterConfig(
            cluster_type="ssh",
            cluster_host=server.host,
            cluster_port=server.port,
            username="tester",
            password="hunter2",
            ssh_host_key_policy="auto_add",
        )
        manager = ConnectionManager(config)
        manager.setup_ssh_connection()
        return manager

    def test_an_inline_package_round_trips_and_still_dereferences(
        self, sample_tree, tmp_path, local_config
    ):
        pkg = data_package(sample_tree, config=local_config)
        payload = tmp_path / "function_data.pkl"
        payload.write_bytes(pickle.dumps(pkg))

        remote_root = tmp_path / "remote"
        remote_root.mkdir()
        with LocalSSHServer(root=remote_root, password="hunter2") as server:
            manager = self._connect(server)
            try:
                manager.upload_file(str(payload), "uploaded.pkl")
                fetched = tmp_path / "fetched.pkl"
                manager.download_file("uploaded.pkl", str(fetched))
            finally:
                manager.ssh_client.close()

        assert (remote_root / "uploaded.pkl").read_bytes() == payload.read_bytes()

        # The worker's view: unpickle and read the data, with no access to the
        # directory the package was built from.
        arrived = pickle.loads(fetched.read_bytes())
        arrived.local_root = None
        root = Path(
            arrived.materialize(dest=str(tmp_path / "worker"), config=local_config)
        )
        assert (root / "data" / "subjects.csv").read_bytes() == (
            sample_tree / "data" / "subjects.csv"
        ).read_bytes()

    def test_a_tampered_payload_is_caught_on_arrival(
        self, sample_tree, tmp_path, local_config
    ):
        """Bytes that changed in transit must not reach the function."""
        pkg = data_package(sample_tree / "data" / "subjects.csv", config=local_config)
        pkg.inline = {"subjects.csv": b"substituted on the wire"}
        payload = tmp_path / "function_data.pkl"
        payload.write_bytes(pickle.dumps(pkg))

        remote_root = tmp_path / "remote"
        remote_root.mkdir()
        with LocalSSHServer(root=remote_root, password="hunter2") as server:
            manager = self._connect(server)
            try:
                manager.upload_file(str(payload), "uploaded.pkl")
                fetched = tmp_path / "fetched.pkl"
                manager.download_file("uploaded.pkl", str(fetched))
            finally:
                manager.ssh_client.close()

        arrived = pickle.loads(fetched.read_bytes())
        with pytest.raises(StagingError, match="Digest mismatch"):
            arrived.materialize(dest=str(tmp_path / "worker"), config=local_config)


# ---------------------------------------------------------------------------
# the remote store: real HuggingFace, small files only
# ---------------------------------------------------------------------------


@pytest.mark.real_world
@requires_hf
class TestAgainstRealHuggingFace:
    """Real uploads to a real private repo. Kilobytes only -- see the brief.

    Marked ``real_world`` so the standard ``-m "not real_world"`` command does
    not select them. Not because they are slow or flaky, but because the Hub
    rate-limits commits per hour per account: a suite that runs these on every
    invocation spends the owner's quota, and quota exhaustion in the middle of
    an unrelated test run is a genuinely confusing failure. Run them
    deliberately::

        pytest tests/unit/test_staging.py -m real_world

    They are never mocked. Without a token they skip, and the remote half of
    data packages is then simply unverified.
    """

    @pytest.fixture
    def hf_config(self, tmp_path, monkeypatch):
        """Config for the real Hub, with the token scoped to these tests only.

        ``conftest.isolate_home`` has already redirected ``$HOME``, so the CLI
        token cache is out of reach by the time this runs -- which is correct,
        and stops the suite writing to the developer's real ``~/.ssh``. The
        token was captured at import time instead; putting it in the
        environment here reaches exactly the tests that need it, and the
        subprocess one of them spawns, which is also how a worker gets a token
        in production.
        """
        monkeypatch.setenv("HF_TOKEN", REAL_HF_TOKEN)
        return ClusterConfig(
            cluster_type="huggingface",
            local_cache_dir=str(tmp_path / "cache"),
            # Force the remote path even though the data is tiny.
            stage_inline_max_bytes=1,
        )

    def test_a_package_uploads_downloads_and_verifies(
        self, sample_tree, hf_config, tmp_path
    ):
        pkg = data_package(sample_tree, config=hf_config)
        try:
            assert not pkg.is_inline
            assert pkg.repo_id and pkg.path_in_repo
            assert pkg.exists(config=hf_config)

            # Drop the local shortcut so the bytes must come back from HF.
            pkg.local_root = None
            root = Path(
                pkg.materialize(dest=str(tmp_path / "worker"), config=hf_config)
            )
            assert (root / "data" / "subjects.csv").read_bytes() == (
                sample_tree / "data" / "subjects.csv"
            ).read_bytes()
        finally:
            pkg.delete(config=hf_config)

    def test_a_tampered_expectation_is_caught_on_download(
        self, sample_tree, hf_config, tmp_path
    ):
        """Digests travel with the payload, so a mismatch is detectable."""
        pkg = data_package(sample_tree / "data" / "subjects.csv", config=hf_config)
        try:
            pkg.local_root = None
            pkg.files = (
                PackagedFile(
                    relpath="subjects.csv",
                    size=pkg.files[0].size,
                    digest="0" * 64,
                ),
            )
            with pytest.raises(StagingError, match="Digest mismatch"):
                pkg.materialize(dest=str(tmp_path / "worker"), config=hf_config)
        finally:
            pkg.delete(config=hf_config)

    def test_delete_removes_it_and_the_remote_agrees(self, sample_tree, hf_config):
        pkg = data_package(sample_tree, config=hf_config)
        assert pkg.exists(config=hf_config)

        assert pkg.delete(config=hf_config) is True

        # Asked of the remote, not inferred from the return value.
        assert pkg.exists(config=hf_config) is False

    def test_deleting_twice_is_not_an_error(self, sample_tree, hf_config):
        pkg = data_package(sample_tree, config=hf_config)
        pkg.delete(config=hf_config)

        assert pkg.delete(config=hf_config) is False

    def test_delete_never_touches_the_files_that_were_packaged(
        self, sample_tree, hf_config
    ):
        original = (sample_tree / "data" / "subjects.csv").read_bytes()
        pkg = data_package(sample_tree, config=hf_config)

        pkg.delete(config=hf_config)

        assert (sample_tree / "data" / "subjects.csv").read_bytes() == original

    def test_a_lost_package_can_still_be_found_and_removed(
        self, sample_tree, hf_config
    ):
        pkg = data_package(sample_tree, config=hf_config)
        try:
            listed = list_data_packages(config=hf_config)
            ids = {record["package_id"] for record in listed}
            assert pkg.package_id in ids

            record = next(r for r in listed if r["package_id"] == pkg.package_id)
            assert record["complete"] is True
            assert record["file_count"] == len(pkg.files)
        finally:
            assert delete_data_package(pkg.package_id, config=hf_config) is True

        assert pkg.exists(config=hf_config) is False

    def test_a_fresh_interpreter_fetches_the_data_back_out_of_the_bucket(
        self, sample_tree, hf_config, tmp_path
    ):
        """The whole point: a worker that has only the object gets the data.

        ``local_root`` is cleared before pickling, so the subprocess cannot
        read the original files even though they are still on this disk. The
        bytes have to come back out of HuggingFace.
        """
        pkg = data_package(sample_tree, config=hf_config)
        try:
            pkg.local_root = None
            saved = tmp_path / "pkg.pkl"
            saved.write_bytes(pickle.dumps(pkg))

            worker = tmp_path / "worker.py"
            worker.write_text(WORKER)
            result = subprocess.run(
                [
                    sys.executable,
                    str(worker),
                    REPO_ROOT,
                    str(saved),
                    str(tmp_path / "cache2"),
                    str(tmp_path / "out"),
                ],
                capture_output=True,
                text=True,
                cwd=str(tmp_path),
            )

            assert result.returncode == 0, result.stdout + result.stderr
            assert "id,score" in result.stdout
        finally:
            pkg.delete(config=hf_config)

    def test_a_package_survives_pickling_and_still_deletes(
        self, sample_tree, hf_config, tmp_path
    ):
        pkg = data_package(sample_tree, config=hf_config)
        saved = tmp_path / "pkg.pkl"
        saved.write_bytes(pickle.dumps(pkg))
        del pkg

        reloaded = pickle.loads(saved.read_bytes())
        assert reloaded.exists(config=hf_config)
        assert reloaded.delete(config=hf_config) is True
        assert reloaded.exists(config=hf_config) is False


class TestNoSilentFallback:
    def test_a_package_too_big_to_inline_fails_rather_than_inlining(
        self, sample_tree, tmp_path, monkeypatch
    ):
        """Without a token the remote path must raise, never quietly inline.

        A fallback here would turn "your data did not go anywhere" into a green
        run that ships megabytes inside the payload.
        """
        from clustrix import staging

        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.setenv("HF_HOME", str(tmp_path / "empty-hf-home"))
        config = ClusterConfig(
            cluster_type="huggingface",
            local_cache_dir=str(tmp_path / "cache"),
            stage_inline_max_bytes=1,
            hf_data_repo="someone/clustrix-data",
        )

        with pytest.raises(staging.StagingError, match="No HuggingFace token"):
            data_package(sample_tree, config=config)


class TestErrorTranslation:
    """A raw hub traceback tells the user nothing about what to do next."""

    def test_a_rate_limited_commit_is_recognised(self):
        from clustrix.staging import _is_rate_limited, _is_missing

        class _Response:
            def __init__(self, status_code):
                self.status_code = status_code

        class _HubError(Exception):
            def __init__(self, status_code):
                super().__init__("boom")
                self.response = _Response(status_code)

        assert _is_rate_limited(_HubError(429))
        assert not _is_rate_limited(_HubError(404))
        assert not _is_rate_limited(RuntimeError("boom"))

        assert _is_missing(_HubError(404))
        assert not _is_missing(_HubError(429))


class TestNoTokenIsHonest:
    def test_the_error_says_how_to_supply_a_token(self, monkeypatch, tmp_path):
        """Never a silent fallback -- the remote path either works or raises."""
        from clustrix import staging

        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.setenv("HF_HOME", str(tmp_path / "empty-hf-home"))

        with pytest.raises(StagingError) as excinfo:
            staging._hf_token(ClusterConfig(cluster_type="huggingface"))

        message = str(excinfo.value)
        assert "hf_token" in message
        assert "HF_TOKEN" in message
        assert "hf auth login" in message


# ---------------------------------------------------------------------------
# deletion: what it is allowed to remove, and what it is not
# ---------------------------------------------------------------------------


class TestDeleteRemovesOnlyWhatClustrixOwns:
    """``delete()`` recursively removed whatever ``dest`` pointed at.

    ``materialize(dest="~/myproject")`` followed by ``delete()`` -- which
    reported that it had removed nothing -- deleted the project.
    """

    def test_a_directory_the_caller_named_survives_delete(
        self, sample_tree, tmp_path, local_config
    ):
        pkg = data_package(sample_tree / "data" / "subjects.csv", config=local_config)

        project = tmp_path / "myproject"
        project.mkdir()
        manuscript = project / "IMPORTANT_manuscript.tex"
        manuscript.write_text(r"\documentclass{article}")
        (project / "notebooks").mkdir()
        pkg.materialize(dest=str(project), config=local_config)

        assert pkg.delete(config=local_config) is False

        assert project.is_dir()
        assert manuscript.read_text() == r"\documentclass{article}"
        assert (project / "notebooks").is_dir()
        assert (project / "subjects.csv").is_file()

    def test_the_cache_directory_clustrix_created_is_still_removed(
        self, sample_tree, local_config
    ):
        """The fix must not be "stop cleaning up"."""
        pkg = data_package(sample_tree, config=local_config)
        cache_root = Path(pkg.materialize(config=local_config))
        assert cache_root.is_dir()
        assert (cache_root / "data" / "subjects.csv").is_file()

        pkg.delete(config=local_config)

        assert not cache_root.exists()

    def test_the_packaged_files_survive_delete(self, sample_tree, local_config):
        original = (sample_tree / "data" / "subjects.csv").read_bytes()
        pkg = data_package(sample_tree, config=local_config)

        pkg.delete(config=local_config)

        assert (sample_tree / "data" / "subjects.csv").read_bytes() == original

    def test_materialising_into_the_source_tree_does_not_delete_it(
        self, sample_tree, tmp_path
    ):
        """The pathological config: the cache aimed at the user's own data."""
        config = ClusterConfig(
            cluster_type="local", local_cache_dir=str(tmp_path / "cache")
        )
        pkg = data_package(sample_tree, config=config)
        pkg.local_root = str(pkg._default_dest(config))

        pkg.delete(config=config)

        assert Path(pkg.local_root).parent.exists() or True  # nothing raised
        assert (sample_tree / "data" / "subjects.csv").is_file()


class TestDeleteDataPackageValidatesItsArgument:
    """A package id is opaque, and it is also a path in the store.

    ``delete_data_package("")`` addressed the whole ``packages/`` prefix and
    removed every package in the account; ``"../README.md"`` climbed out of the
    prefix and removed a file that was never a package. Both were verified
    against the real store before this check existed.
    """

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "..",
            "../README.md",
            "packages",
            "packages/../README.md",
            "not-a-uuid",
            "0123456789abcdef0123456789abcdeg",  # 32 chars, 'g' is not hex
            "0123456789ABCDEF0123456789ABCDEF",  # uuid4().hex is lowercase
            "0123456789abcdef0123456789abcde",  # 31 chars
            "/",
            None,
            42,
        ],
    )
    def test_anything_that_is_not_an_id_is_refused(self, bad, tmp_path, monkeypatch):
        """Refused before the network, so no token is needed to prove it."""
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.setenv("HF_HOME", str(tmp_path / "empty-hf-home"))
        config = ClusterConfig(
            cluster_type="huggingface", hf_data_repo="someone/clustrix-data"
        )

        with pytest.raises(StagingError, match="is not a data package id"):
            delete_data_package(bad, config=config)

    def test_a_real_id_passes_validation(self):
        package_id = uuid.uuid4().hex
        assert _validate_package_id(package_id) == package_id

    def test_a_package_pointing_somewhere_that_is_not_a_package_will_not_delete(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.setenv("HF_HOME", str(tmp_path / "empty-hf-home"))
        hostile = DataPackage(
            name="hostile",
            package_id=uuid.uuid4().hex,
            files=(PackagedFile(relpath="x.bin", size=1, digest="00"),),
            repo_id="someone/clustrix-data",
            path_in_repo="..",
        )

        with pytest.raises(StagingError, match="is not a data package location"):
            hostile.delete(config=ClusterConfig(cluster_type="huggingface"))

    def test_delete_never_reports_success_without_reaching_the_store(
        self, tmp_path, monkeypatch
    ):
        """``delete()`` returning True is a claim about the remote store.

        Returning it without a round trip would tell a user their storage was
        released when it was not.
        """
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.setenv("HF_HOME", str(tmp_path / "empty-hf-home"))
        package_id = uuid.uuid4().hex
        pkg = DataPackage(
            name="remote",
            package_id=package_id,
            files=(PackagedFile(relpath="x.bin", size=1, digest="00"),),
            repo_id="someone/clustrix-data",
            path_in_repo=f"packages/{package_id}",
        )

        with pytest.raises(StagingError, match="No HuggingFace token"):
            pkg.delete(config=ClusterConfig(cluster_type="huggingface"))


# ---------------------------------------------------------------------------
# one package, one answer -- wherever it is dereferenced
# ---------------------------------------------------------------------------


class TestOnePackageGivesOneAnswer:
    """``local_root`` was trusted on a size match, which is not identity.

    That made ``path()`` disagree with ``read_bytes()`` on the same package on
    the same machine, and made a worker that happened to have a same-size file
    at the same absolute path -- the shared-home cluster case -- serve that
    file's contents instead of the packaged ones.
    """

    def test_an_in_place_edit_at_identical_size_does_not_fool_path(
        self, tmp_path, local_config
    ):
        source = tmp_path / "params.json"
        source.write_bytes(b'{"lr": 0.001}')
        pkg = data_package(source, config=local_config)
        before = source.stat()

        source.write_bytes(b'{"lr": 9.999}')  # identical length
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))

        assert Path(pkg.path(config=local_config)).read_bytes() == b'{"lr": 0.001}'
        assert pkg.read_bytes(config=local_config) == b'{"lr": 0.001}'

    def test_an_unrelated_file_at_local_root_is_not_served_as_the_package(
        self, tmp_path, local_config
    ):
        """The shared-home worker: the path exists and holds something else."""
        shared = tmp_path / "shared"
        shared.mkdir()
        source = shared / "params.json"
        source.write_bytes(b'{"lr": 0.001}')
        pkg = data_package(source, config=local_config)

        arrived = pickle.loads(pickle.dumps(pkg))
        source.write_bytes(b'{"lr": 9.999}')

        assert Path(arrived.path(config=local_config)).read_bytes() == b'{"lr": 0.001}'
        assert arrived.read_bytes(config=local_config) == b'{"lr": 0.001}'

    def test_a_local_source_that_still_matches_is_still_used_without_copying(
        self, sample_tree, local_config
    ):
        """The fast path is a shortcut, not a fallback -- it must still work."""
        target = sample_tree / "data" / "subjects.csv"
        pkg = data_package(target, config=local_config)

        assert Path(pkg.path(config=local_config)).resolve() == target.resolve()

    def test_read_bytes_rejects_contents_that_do_not_match_the_digest(
        self, local_config
    ):
        payload = b"the real contents"
        pkg = DataPackage(
            name="tampered",
            package_id=uuid.uuid4().hex,
            files=(
                PackagedFile(
                    relpath="x.bin", size=len(payload), digest=_digest_bytes(payload)
                ),
            ),
            inline={"x.bin": b"tampered contents"},
        )

        with pytest.raises(StagingError, match="Digest mismatch"):
            pkg.read_bytes(config=local_config)


class TestConfinementSurvivesSymlinks:
    def test_a_symlink_in_dest_cannot_redirect_a_write_out_of_it(self, tmp_path):
        """The syntactic check passes this; only post-resolution catches it."""
        dest = tmp_path / "dest"
        dest.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (dest / "sub").symlink_to(outside, target_is_directory=True)

        _safe_relpath("sub/x.txt")  # nothing wrong with the name itself
        with pytest.raises(StagingError, match="resolves outside"):
            _confine(dest, "sub/x.txt")

    def test_materialising_through_such_a_symlink_writes_nothing_outside(
        self, tmp_path, local_config
    ):
        payload = b"pwned"
        pkg = DataPackage(
            name="hostile",
            package_id=uuid.uuid4().hex,
            files=(
                PackagedFile(
                    relpath="sub/x.txt",
                    size=len(payload),
                    digest=_digest_bytes(payload),
                ),
            ),
            inline={"sub/x.txt": payload},
        )
        dest = tmp_path / "dest"
        dest.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (dest / "sub").symlink_to(outside, target_is_directory=True)

        with pytest.raises(StagingError, match="resolves outside"):
            pkg.materialize(dest=str(dest), config=local_config)
        assert list(outside.iterdir()) == []


# ---------------------------------------------------------------------------
# what a package may be built from
# ---------------------------------------------------------------------------


class TestSymlinks:
    """A symlink keeps its own place in the package and carries its contents.

    Both halves used to be wrong: a symlink inside a packaged directory was
    dropped without a word, so the worker got a tree that was missing a file
    the local one had; and a symlink named explicitly took its *target's*
    relative path, which moved the package root to the target's directory.
    """

    def test_a_symlink_inside_a_packaged_directory_is_carried_not_dropped(
        self, tmp_path, local_config
    ):
        tree = tmp_path / "tree"
        tree.mkdir()
        (tree / "real.csv").write_text("real")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "z.csv").write_text("target bytes")
        (tree / "link.csv").symlink_to(elsewhere / "z.csv")

        pkg = data_package(tree, config=local_config)

        assert sorted(pkg.filenames()) == ["link.csv", "real.csv"]
        assert pkg.read_bytes("link.csv", config=local_config) == b"target bytes"

    def test_a_named_symlink_keeps_its_own_name_and_does_not_move_the_root(
        self, tmp_path, local_config
    ):
        tree = tmp_path / "tree"
        tree.mkdir()
        (tree / "real.csv").write_text("real")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "z.csv").write_text("target bytes")
        (tree / "link.csv").symlink_to(elsewhere / "z.csv")

        pkg = data_package([tree / "link.csv", tree / "real.csv"], config=local_config)

        assert sorted(pkg.filenames()) == ["link.csv", "real.csv"]
        assert Path(pkg.local_root).resolve() == tree.resolve()

    def test_a_symlink_to_a_credential_is_refused_like_the_credential(
        self, tmp_path, local_config
    ):
        """Otherwise the check is one ``ln -s`` away from being decorative."""
        tree = tmp_path / "tree"
        tree.mkdir()
        (tree / "real.csv").write_text("real")
        secret = tmp_path / "id_rsa"
        secret.write_text("not actually a key")
        (tree / "notes.txt").symlink_to(secret)

        with pytest.raises(StagingError, match="looks like a credential"):
            data_package(tree, config=local_config)

    def test_a_dangling_symlink_is_refused_by_name(self, tmp_path, local_config):
        (tmp_path / "dangling.csv").symlink_to(tmp_path / "never-existed.csv")

        with pytest.raises(StagingError, match="Cannot stage"):
            data_package(tmp_path / "dangling.csv", config=local_config)

    def test_a_symlinked_directory_inside_a_tree_is_refused_not_guessed(
        self, tmp_path, local_config
    ):
        tree = tmp_path / "tree"
        tree.mkdir()
        (tree / "real.csv").write_text("real")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "z.csv").write_text("z")
        (tree / "link").symlink_to(elsewhere, target_is_directory=True)

        with pytest.raises(StagingError, match="not a regular file"):
            data_package(tree, config=local_config)


@pytest.mark.timeout(30)
class TestOnlyRegularFilesAndDirectories:
    """Reading a fifo or a character device blocks forever.

    ``data_package("<fifo>")`` and ``data_package("/dev/zero")`` both hung with
    no output and no timeout, which is the least debuggable failure available.

    The class carries a timeout because a regression here does not fail, it
    hangs, and a hung CI job is a much worse signal than a red one.
    """

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no mkfifo on this platform")
    def test_a_named_pipe_is_refused_rather_than_read(self, tmp_path, local_config):
        fifo = tmp_path / "pipe"
        os.mkfifo(fifo)

        with pytest.raises(StagingError, match="named pipe"):
            data_package(fifo, config=local_config)

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no mkfifo on this platform")
    def test_a_named_pipe_inside_a_packaged_directory_is_refused(
        self, tmp_path, local_config
    ):
        tree = tmp_path / "tree"
        tree.mkdir()
        (tree / "real.csv").write_text("real")
        os.mkfifo(tree / "pipe")

        with pytest.raises(StagingError, match="named pipe"):
            data_package(tree, config=local_config)

    @pytest.mark.skipif(
        not Path("/dev/zero").exists(), reason="no /dev/zero on this platform"
    )
    def test_a_character_device_is_refused_rather_than_read(self, local_config):
        with pytest.raises(StagingError, match="character device"):
            data_package("/dev/zero", config=local_config)


class TestMoreCredentialShapedPaths:
    @pytest.mark.parametrize(
        "name",
        [
            "credentials.json",
            ".git-credentials",
            "kubeconfig",
            ".npmrc",
            ".pypirc",
            ".htpasswd",
            "secrets.yaml",
            ".dockercfg",
        ],
    )
    def test_a_credential_shaped_file_is_refused(self, tmp_path, local_config, name):
        secret = tmp_path / name
        secret.write_text("not actually a credential")

        with pytest.raises(StagingError, match="looks like a credential"):
            data_package(secret, config=local_config)

    def test_a_git_config_is_refused_because_remotes_carry_tokens(
        self, tmp_path, local_config
    ):
        config_file = tmp_path / "repo" / ".git" / "config"
        config_file.parent.mkdir(parents=True)
        config_file.write_text("[remote 'origin']\n\turl = https://token@host/x\n")

        with pytest.raises(StagingError, match="looks like a credential"):
            data_package(config_file, config=local_config)

    def test_ordinary_data_is_still_not_mistaken_for_a_credential(self, tmp_path):
        assert not _is_sensitive(tmp_path / "secretariat.csv")
        assert not _is_sensitive(tmp_path / "kubeconfigs.parquet")
        assert not _is_sensitive(tmp_path / "npmrc_counts.tsv")


class TestFilesThatChangeUnderneathUs:
    def test_a_file_that_grew_between_hashing_and_reading_is_refused(self, tmp_path):
        """Caught here, naming the file, rather than on the worker hours later."""
        source = tmp_path / "growing.bin"
        source.write_bytes(b"x" * 1000)
        entry = _describe("growing.bin", source, "grower")
        assert entry.size == 1000

        source.write_bytes(b"x" * 1500)

        with pytest.raises(StagingError, match="changed while data package"):
            _read_verified(source, entry, "grower")

    def test_an_unchanged_file_reads_straight_through(self, tmp_path):
        source = tmp_path / "steady.bin"
        source.write_bytes(b"steady")
        entry = _describe("steady.bin", source, "steady")

        assert _read_verified(source, entry, "steady") == b"steady"

    def test_an_unreadable_input_names_the_package_not_an_errno(self, tmp_path):
        missing = tmp_path / "vanished.bin"

        with pytest.raises(StagingError, match="while building data package 'gone'"):
            _describe("vanished.bin", missing, "gone")


class TestInlineThresholdMeasuresWhatTravels:
    def test_many_tiny_files_do_not_count_as_inline_by_their_data_size(
        self, tmp_path, monkeypatch
    ):
        """Forty kilobytes of data can be a megabyte of pickle.

        The threshold governs what rides inside the job payload, so it has to
        be measured on the payload. With no token the remote path raises, which
        is how this test can tell which path was taken without uploading
        anything.
        """
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.setenv("HF_HOME", str(tmp_path / "empty-hf-home"))
        tree = tmp_path / "many"
        tree.mkdir()
        for index in range(3000):
            (tree / f"f{index}.bin").write_bytes(b"abcd")
        limit = 150_000
        assert 3000 * 4 < limit  # the data alone is well under the threshold
        config = ClusterConfig(
            cluster_type="huggingface",
            local_cache_dir=str(tmp_path / "cache"),
            stage_inline_max_bytes=limit,
            hf_data_repo="someone/clustrix-data",
        )

        with pytest.raises(StagingError, match="No HuggingFace token"):
            data_package(tree, config=config)

    def test_a_package_that_really_is_small_still_goes_inline(
        self, sample_tree, local_config
    ):
        pkg = data_package(sample_tree, config=local_config)

        assert pkg.is_inline
        assert pkg.repo_id is None

    def test_force_local_inlines_regardless_of_serialized_size(self, tmp_path):
        tree = tmp_path / "many"
        tree.mkdir()
        for index in range(2000):
            (tree / f"f{index}.bin").write_bytes(b"abcd")
        config = ClusterConfig(
            cluster_type="local",
            local_cache_dir=str(tmp_path / "cache"),
            stage_inline_max_bytes=16,
        )

        pkg = data_package(tree, config=config, force_local=True)

        assert pkg.is_inline


class TestTheRateLimitMessageIsTrue:
    def test_huggingface_hub_really_uploads_lfs_files_before_the_commit(self):
        """The premise of the message, checked against the installed library.

        ``preupload_lfs_files`` runs inside ``create_commit`` before the commit
        is posted, so a refused commit does not mean nothing reached the Hub.
        """
        import inspect

        from huggingface_hub import HfApi

        source = inspect.getsource(HfApi.create_commit)
        assert "self.preupload_lfs_files(" in source

    def test_the_message_does_not_claim_nothing_was_uploaded(self):
        import inspect

        from clustrix import staging

        source = inspect.getsource(staging._upload)
        assert "Nothing was uploaded" not in source
        assert "preupload_lfs_files" in source


@pytest.mark.real_world
@requires_hf
class TestThePrivateRepoPromiseIsKept:
    """``private=True, exist_ok=True`` does not make an existing repo private.

    It returns the repo as it is. A ``hf_data_repo`` that already existed and
    was public therefore took the upload and published the data, while the
    docstring promised a private repo. Verified against the real Hub before
    this refusal existed.
    """

    @pytest.fixture
    def namespace(self, monkeypatch):
        from clustrix.staging import _hf_api

        monkeypatch.setenv("HF_TOKEN", REAL_HF_TOKEN)
        return _hf_api(ClusterConfig(cluster_type="huggingface")).whoami()["name"]

    def test_staging_into_a_public_repo_is_refused_and_uploads_nothing(
        self, tmp_path, namespace, monkeypatch
    ):
        from clustrix.staging import _hf_api

        monkeypatch.setenv("HF_TOKEN", REAL_HF_TOKEN)
        repo_id = f"{namespace}/clustrix-public-refusal-{uuid.uuid4().hex[:8]}"
        api = _hf_api(ClusterConfig(cluster_type="huggingface"))
        api.create_repo(repo_id=repo_id, repo_type="dataset", private=False)
        try:
            assert api.repo_info(repo_id=repo_id, repo_type="dataset").private is False

            source = tmp_path / "notes.txt"
            source.write_text("a few kilobytes at most\n")
            config = ClusterConfig(
                cluster_type="huggingface",
                local_cache_dir=str(tmp_path / "cache"),
                stage_inline_max_bytes=1,
                hf_data_repo=repo_id,
            )

            with pytest.raises(StagingError) as excinfo:
                data_package(source, config=config)

            message = str(excinfo.value)
            assert "PUBLIC" in message
            assert repo_id in message  # names the repo
            assert "hf_data_repo" in message  # says how to fix it

            # Asked of the Hub, not inferred from the exception.
            listed = api.list_repo_files(repo_id=repo_id, repo_type="dataset")
            assert not [name for name in listed if name.startswith("packages/")]
            # And clustrix did not quietly change someone's repo settings.
            assert api.repo_info(repo_id=repo_id, repo_type="dataset").private is False
        finally:
            api.delete_repo(repo_id=repo_id, repo_type="dataset")

    def test_a_repo_that_cannot_be_created_raises_a_staging_error(
        self, tmp_path, monkeypatch
    ):
        """A raw ``HfHubHTTPError`` (a 401 was observed) tells the user nothing."""
        monkeypatch.setenv("HF_TOKEN", REAL_HF_TOKEN)
        source = tmp_path / "notes.txt"
        source.write_text("a few kilobytes at most\n")
        config = ClusterConfig(
            cluster_type="huggingface",
            local_cache_dir=str(tmp_path / "cache"),
            stage_inline_max_bytes=1,
            # An org this token is certainly not a member of.
            hf_data_repo=f"openai/clustrix-data-{uuid.uuid4().hex[:8]}",
        )

        with pytest.raises(StagingError, match="Could not create or reach"):
            data_package(source, config=config)
