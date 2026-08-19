"""Tests for ClusterConfig.save_to_file / save_config permissions and secret
redaction (#111 item 5).

``save_to_file`` used to write via a plain ``open(path, "w")`` with no mode
and no exclusion of secret-bearing fields, so any password, token, or API
key on the config landed in a 0644 (world-readable) file. These tests create
real files on a real filesystem and stat() them for real -- no mocked
filesystem, no mocked dataclass -- because the entire defect was about what
actually lands on disk and with what real permission bits.
"""

import json
import stat

import pytest
import yaml

from clustrix.config import SECRET_FIELDS, ClusterConfig


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def secret_bearing_config():
    return ClusterConfig(
        cluster_type="ssh",
        cluster_host="cluster.example.edu",
        username="researcher",
        password="fake-password-for-this-test",
        api_key="sk-fake-key-abcdef123456",
        aws_secret_access_key="AKIAABCDEFSECRETVALUE",  # nosec
        hf_token="hf_thisisasecrettoken",  # nosec
    )


@pytest.mark.parametrize("suffix", [".json", ".yml"])
def test_save_to_file_creates_file_mode_0600(tmp_path, secret_bearing_config, suffix):
    config_path = tmp_path / f"clustrix{suffix}"
    secret_bearing_config.save_to_file(str(config_path))

    assert config_path.exists()
    mode = _mode(config_path)
    assert mode == 0o600, (
        f"Expected mode 0o600, got {oct(mode)} for {config_path}. "
        f"A saved clustrix config can contain passwords/tokens and must "
        f"never be group- or world-readable."
    )


@pytest.mark.parametrize("suffix", [".json", ".yml"])
def test_save_to_file_omits_secrets_by_default(tmp_path, secret_bearing_config, suffix):
    config_path = tmp_path / f"clustrix{suffix}"
    secret_bearing_config.save_to_file(str(config_path))

    raw_text = config_path.read_text()
    for secret_value in (
        "hunter2-super-secret",
        "sk-real-looking-secret-abcdef123456",
        "AKIAABCDEFSECRETVALUE",
        "hf_thisisasecrettoken",
    ):
        assert secret_value not in raw_text, (
            f"Secret value {secret_value!r} was written in plaintext to "
            f"{config_path} despite include_secrets defaulting to False."
        )

    if suffix == ".json":
        loaded = json.loads(raw_text)
    else:
        loaded = yaml.safe_load(raw_text)

    for field in SECRET_FIELDS:
        assert field not in loaded or not loaded[field], (
            f"Secret field {field!r} present with a truthy value in the "
            f"saved file: {loaded.get(field)!r}"
        )

    # Non-secret fields must round-trip normally.
    assert loaded["cluster_host"] == "cluster.example.edu"
    assert loaded["username"] == "researcher"
    assert loaded["cluster_type"] == "ssh"


def test_save_to_file_include_secrets_true_writes_plaintext(
    tmp_path, secret_bearing_config
):
    config_path = tmp_path / "clustrix_with_secrets.json"
    secret_bearing_config.save_to_file(str(config_path), include_secrets=True)

    raw_text = config_path.read_text()
    assert "fake-password-for-this-test" in raw_text
    assert "sk-fake-key-abcdef123456" in raw_text

    # Even with secrets included, the mode must still be 0600 -- opting into
    # writing secrets must never also opt into a wider file mode.
    assert _mode(config_path) == 0o600


def test_load_from_file_round_trips_after_default_save(tmp_path, secret_bearing_config):
    """Saving (with secrets redacted) and reloading must not raise, and the
    reloaded config must have the non-secret fields intact and the secret
    fields reset to their dataclass defaults (i.e. the user must supply
    credentials again through some other channel).
    """
    config_path = tmp_path / "clustrix.json"
    secret_bearing_config.save_to_file(str(config_path))

    reloaded = ClusterConfig.load_from_file(str(config_path))
    assert reloaded.cluster_host == "cluster.example.edu"
    assert reloaded.username == "researcher"
    assert reloaded.password is None
    assert reloaded.api_key is None
    assert reloaded.aws_secret_access_key is None
    assert reloaded.hf_token is None


def test_overwriting_a_preexisting_world_readable_file_is_tightened(
    tmp_path, secret_bearing_config
):
    """A config file that already exists at loose permissions (e.g. created
    by an older clustrix version, or by hand) must be tightened to 0600 on
    the very next save -- os.open()'s mode argument alone does NOT do this
    for a pre-existing file, since it only applies at creation time. This
    is a regression test for exactly that gap.
    """
    config_path = tmp_path / "clustrix.json"
    config_path.write_text("{}")
    config_path.chmod(0o644)
    assert _mode(config_path) == 0o644, "Test setup failed to produce a 0644 file"

    secret_bearing_config.save_to_file(str(config_path))

    assert _mode(config_path) == 0o600, (
        "save_to_file did not tighten permissions on a pre-existing "
        "0644 file -- it left it group/world readable while writing "
        "config content into it."
    )


def test_save_config_module_function_matches_save_to_file(tmp_path, monkeypatch):
    """clustrix.config.save_config (the module-level convenience function)
    shares the exact same underlying write path as save_to_file, and must
    exhibit the same 0600 + redaction behavior -- it had the identical bug.
    """
    import clustrix.config as config_module

    real_config = ClusterConfig(
        cluster_host="module-level.example.edu",
        username="modtest",
        password="fake-module-password",
    )
    monkeypatch.setattr(config_module, "_config", real_config)

    config_path = tmp_path / "module_saved.json"
    config_module.save_config(str(config_path))

    assert _mode(config_path) == 0o600
    raw_text = config_path.read_text()
    assert "module-secret-value" not in raw_text
    loaded = json.loads(raw_text)
    assert loaded["cluster_host"] == "module-level.example.edu"


def test_secret_fields_derived_from_dataclass_covers_known_credential_names():
    """SECRET_FIELDS must be computed from the dataclass field names (so a
    newly added credential field is covered automatically), not a hand-kept
    list that can silently fall out of date. Spot-check known credential
    fields are present.
    """
    for expected in (
        "password",
        "api_key",
        "aws_secret_access_key",
        "aws_access_key_id",
        "azure_client_secret",
        "gcp_service_account_key",
        "lambda_api_key",
        "hf_token",
    ):
        assert expected in SECRET_FIELDS, (
            f"{expected!r} should be classified as a secret field but "
            f"SECRET_FIELDS is {sorted(SECRET_FIELDS)}"
        )
    # Sanity: fields that are not credentials must not be swept up.
    for not_expected in (
        "cluster_host",
        "username",
        "cluster_type",
        "ssh_port",
        # Not secrets despite matching on name: a boolean flag, and a
        # field holding the NAME of an environment variable rather than
        # its value. Dropping these broke the auth-fallback round trip
        # while protecting nothing.
        "use_env_password",
        "password_env_var",
        # A mapping, filtered entry-by-entry rather than dropped whole --
        # see test_environment_variables_are_filtered_not_dropped.
        "environment_variables",
    ):
        assert not_expected not in SECRET_FIELDS


def test_environment_variables_are_filtered_not_dropped(tmp_path):
    """`environment_variables` usually holds a mix.

    Dropping the whole mapping protected the credentials in it but also
    threw away ordinary settings the user expects to persist -- so saving
    and reloading a config silently lost OMP_NUM_THREADS. Each entry is
    judged on its own key name instead.
    """
    config = ClusterConfig(
        cluster_host="cluster.example.edu",
        username="researcher",
        environment_variables={
            "OMP_NUM_THREADS": "8",
            "MY_PIPELINE_STAGE": "preprocess",
            "AWS_SECRET_ACCESS_KEY": "fake-aws-secret-value",
            "HF_TOKEN": "fake-hf-token-value",
        },
    )

    config_path = tmp_path / "envvars.json"
    config.save_to_file(str(config_path))
    raw_text = config_path.read_text()

    assert "fake-aws-secret-value" not in raw_text
    assert "fake-hf-token-value" not in raw_text

    reloaded = ClusterConfig.load_from_file(str(config_path))
    assert reloaded.environment_variables == {
        "OMP_NUM_THREADS": "8",
        "MY_PIPELINE_STAGE": "preprocess",
    }


def test_environment_variable_secrets_survive_include_secrets(tmp_path):
    """Opting in must write the whole mapping, not the filtered version."""
    config = ClusterConfig(
        cluster_host="cluster.example.edu",
        environment_variables={
            "OMP_NUM_THREADS": "8",
            "AWS_SECRET_ACCESS_KEY": "fake-aws-secret-value",
        },
    )
    config_path = tmp_path / "envvars_with_secrets.json"
    config.save_to_file(str(config_path), include_secrets=True)

    assert "fake-aws-secret-value" in config_path.read_text()
    assert _mode(config_path) == 0o600

    reloaded = ClusterConfig.load_from_file(str(config_path))
    assert reloaded.environment_variables == config.environment_variables


def test_repr_masks_credentials_but_keeps_ordinary_fields():
    """A config used to print its own password into any traceback or log.

    save_to_file already refused to write credentials in plaintext; showing
    them on screen instead was barely an improvement. Masked rather than
    omitted, so it stays visible that a value is set at all.
    """
    config = ClusterConfig(
        cluster_host="hpc.example.edu",
        username="researcher",
        password="fake-password-value",
        hf_token="fake-hf-token-value",
        api_key="sk-fake-api-value",
        environment_variables={
            "OMP_NUM_THREADS": "8",
            "AWS_SECRET_ACCESS_KEY": "fake-aws-value",
        },
    )

    rendered = repr(config)

    for secret in (
        "fake-password-value",
        "fake-hf-token-value",
        "sk-fake-api-value",
        "fake-aws-value",
    ):
        assert secret not in rendered, f"{secret!r} leaked through repr()"

    # It must still be a useful repr.
    assert "hpc.example.edu" in rendered
    assert "researcher" in rendered
    assert "OMP_NUM_THREADS" in rendered
    assert "password='***'" in rendered
