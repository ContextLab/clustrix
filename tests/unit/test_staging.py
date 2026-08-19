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
from pathlib import Path

import pytest

import clustrix
from clustrix.config import ClusterConfig
from clustrix.staging import (
    DataPackage,
    PackagedFile,
    StagingError,
    _digest_bytes,
    _digest_file,
    _is_sensitive,
    _safe_relpath,
    data_package,
    delete_data_package,
    list_data_packages,
    materialize_packages,
)

from tests.ssh_server import LocalSSHServer

REPO_ROOT = str(Path(clustrix.__file__).resolve().parents[1])


def _hf_token_available():
    """A usable token, or None. Never invents one."""
    from clustrix.hf_jobs import _token_from_hf_cli_cache

    return os.environ.get("HF_TOKEN") or _token_from_hf_cli_cache()


requires_hf = pytest.mark.skipif(
    _hf_token_available() is None,
    reason=(
        "No HuggingFace token (HF_TOKEN or `hf auth login`). The remote-store "
        "half of data packages is UNVERIFIED without one; it is not mocked."
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
        self, sample_tree, local_config
    ):
        pkg = data_package(sample_tree, config=local_config)
        blob = pickle.dumps(pkg)

        # A token embedded in the object would make a saved package a secret
        # on disk. The only bytes in here are the user's own data.
        token = _hf_token_available()
        if token:
            assert token.encode() not in blob
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

    @pytest.fixture(autouse=True)
    def _isolated_known_hosts(self, tmp_path, monkeypatch):
        """Keep ``auto_add`` away from the real ``~/.ssh/known_hosts``.

        ``ssh_security._load_known_hosts`` expanduser's that path, and paramiko's
        AutoAddPolicy then *saves* back to whatever file was loaded -- rewriting
        the whole thing, not appending. Pointing HOME at tmp_path means these
        tests cannot add to, or truncate, the developer's real file. See the
        defect reported alongside this work.
        """
        home = tmp_path / "home"
        (home / ".ssh").mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))

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
    def hf_config(self, tmp_path):
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
