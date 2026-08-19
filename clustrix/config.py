import json
import re
import yaml
import os
from pathlib import Path
from typing import Dict, Optional, Any
from dataclasses import dataclass, asdict, fields


@dataclass
class ClusterConfig:
    """Configuration settings for cluster execution."""

    # Authentication
    api_key: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    key_file: Optional[str] = None

    # Cluster settings
    # One of SUPPORTED_CLUSTER_TYPES (defined below the class, since a
    # dataclass body cannot reference a name it also defines). Every place
    # that offers a choice of backend -- the CLI, the notebook widget --
    # must read that tuple rather than keeping its own copy: the CLI was
    # missing "huggingface" entirely, so a working backend could not be
    # selected from the command line at all.
    cluster_type: str = "slurm"
    cluster_host: Optional[str] = None
    cluster_port: int = 22

    # HuggingFace Jobs settings. hf_hardware and hf_username are the older
    # widget-facing spellings; hf_jobs.py still reads them as fallbacks for
    # hf_flavor and hf_namespace, so they are kept. hf_sdk was a *Spaces*
    # concept (gradio/streamlit/static) and went with that backend.
    hf_hardware: Optional[str] = None
    hf_token: Optional[str] = None  # Required for authentication
    hf_username: Optional[str] = None
    # HuggingFace Jobs backend (cluster_type="huggingface"). The namespace is
    # usually an org rather than the personal account, which is often not on a
    # plan that can run jobs.
    hf_namespace: Optional[str] = None
    hf_flavor: Optional[str] = None  # defaults to cpu-basic
    hf_image: Optional[str] = None  # defaults to python:<local minor>-slim
    hf_job_timeout: Optional[str] = None  # e.g. "30m"
    # GPU flavors bill real money, so selecting one is an explicit act.
    hf_allow_gpu_flavors: bool = False

    # Resource defaults
    default_cores: int = 4
    default_memory: str = "8GB"
    default_time: str = "01:00:00"
    default_partition: Optional[str] = None
    default_queue: Optional[str] = None

    # Paths
    # Home-relative, not /tmp: on SLURM/PBS/SGE the compute node has its own
    # /tmp, so an environment built on the login node is simply absent at run
    # time and the job dies with exit 127 before writing any diagnostics.
    remote_work_dir: str = "~/.clustrix/jobs"
    local_work_dir: Optional[str] = None  # If None, uses current working directory
    local_cache_dir: str = "~/.clustrix/cache"
    conda_env_name: Optional[str] = None
    python_executable: str = "python"
    package_manager: str = "pip"  # pip, uv, or auto

    # Execution preferences
    auto_parallel: bool = True
    # NO EFFECT. Both were read only by the client-side GPU parallelization
    # path, which was deleted because it never called the decorated function:
    # it ran a hardcoded torch program per GPU and returned the traces of
    # random matrices as the user's result. They are kept so that existing
    # clustrix.yml files and configure(...) calls keep loading, and are listed
    # under "Settings that currently have no effect" in the configuration docs.
    # Parallelize across GPUs inside your own function instead.
    auto_gpu_parallel: bool = True
    max_parallel_jobs: int = 100
    max_gpu_parallel_jobs: int = 8
    job_poll_interval: int = 30
    cleanup_on_success: bool = True
    prefer_local_parallel: bool = False
    local_parallel_threshold: int = 1000  # Use local if iterations < threshold
    async_submit: bool = False  # Use asynchronous job submission
    use_two_venv: bool = True  # Use two-venv setup for cross-version compatibility
    # Seconds paramiko waits to establish an SSH connection. The OS default
    # is minutes, which turns an unreachable host into a hang rather than an
    # error.
    ssh_connect_timeout: int = 30
    venv_setup_timeout: int = 300  # Timeout for venv setup in seconds (5 minutes)

    # Enhanced Authentication Options
    use_env_password: bool = False  # Enable environment variable password
    password_env_var: str = ""  # Name of environment variable containing password
    cache_credentials: bool = True  # Cache credentials in memory
    credential_cache_ttl: int = 300  # Credential cache TTL in seconds (5 minutes)
    ssh_port: int = 22  # SSH port (for consistency with cluster_port)
    # Controls what clustrix does when a remote host's SSH key is not already
    # in your known_hosts files. "reject" (default, secure) refuses the
    # connection and tells you the exact ssh-keyscan command to add it.
    # "auto_add" opts into trusting unknown host keys automatically -- this
    # is insecure (vulnerable to machine-in-the-middle attacks) and must be
    # chosen deliberately; it is never the default. See clustrix.ssh_security.
    ssh_host_key_policy: str = "reject"

    # Advanced settings
    environment_variables: Optional[Dict[str, str]] = None
    module_loads: Optional[list] = None
    pre_execution_commands: Optional[list] = None

    # Cluster-specific package and setup configuration
    # The execution environment mirrors the local one by default: whatever the
    # local package manager reports (pip freeze, or the uv/conda equivalent) is
    # installed on the worker. A function that runs locally then runs remotely
    # without anyone listing its dependencies by hand.
    replicate_local_environment: bool = True
    # Names to leave out of that mirror -- platform-specific wheels that cannot
    # install on the cluster, or anything simply not needed there.
    excluded_packages: Optional[list] = None
    cluster_packages: Optional[list] = None  # Additional packages to install in VENV2
    venv_post_install_commands: Optional[list] = (
        None  # Commands to run after package installation
    )

    # GPU Detection and Support Configuration
    gpu_detection_enabled: bool = True  # Enable GPU detection in VENV1
    auto_gpu_packages: bool = (
        True  # Automatically install GPU-enabled packages in VENV2
    )
    cuda_version_preference: Optional[str] = (
        None  # Preferred CUDA version (e.g., "11.8", "12.1")
    )
    gpu_memory_fraction: float = 0.9  # Fraction of GPU memory to use per job
    prefer_gpu_execution: bool = True  # Prefer GPU nodes when available
    gpu_requirements: Optional[Dict[str, Any]] = None  # Specific GPU requirements
    rapids_ecosystem: bool = (
        False  # Install RAPIDS ecosystem packages (cuDF, cuML, etc.)
    )

    # Runtime venv information (set during execution)
    venv_info: Optional[dict] = None  # Information about created virtual environments

    def __repr__(self) -> str:
        """Render the config with credentials masked.

        The dataclass-generated ``__repr__`` printed every field verbatim, so
        a password or API token landed in any traceback, log line or notebook
        cell that displayed a config. ``save_to_file`` already refused to
        write these in plaintext; showing them on screen instead was not much
        better. Masked rather than omitted, so it stays obvious that a value
        is set.
        """
        parts = []
        for field_def in fields(self):
            value = getattr(self, field_def.name)
            if field_def.name in SECRET_FIELDS and value is not None:
                value = "***"
            elif field_def.name in SECRET_BEARING_MAPPINGS and isinstance(value, dict):
                value = {
                    k: ("***" if k not in _redact_secret_entries(value) else v)
                    for k, v in value.items()
                }
            parts.append(f"{field_def.name}={value!r}")
        return f"{type(self).__name__}({', '.join(parts)})"

    def __post_init__(self):
        if self.environment_variables is None:
            self.environment_variables = {}
        if self.module_loads is None:
            self.module_loads = []
        if self.pre_execution_commands is None:
            self.pre_execution_commands = []
        if self.cluster_packages is None:
            self.cluster_packages = []
        if self.excluded_packages is None:
            self.excluded_packages = []
        if self.venv_post_install_commands is None:
            self.venv_post_install_commands = []

        if self.ssh_host_key_policy not in ("reject", "auto_add"):
            raise ValueError(
                f"Invalid ssh_host_key_policy={self.ssh_host_key_policy!r}. "
                f"Valid values are 'reject' (default, secure) or 'auto_add' "
                f"(insecure, trusts unknown host keys automatically)."
            )

    def get_env_password(self) -> Optional[str]:
        """Get password from specified environment variable."""
        if self.use_env_password and self.password_env_var:
            return os.environ.get(self.password_env_var)
        return None

    def save_to_file(self, config_path: str, include_secrets: bool = False) -> None:
        """Save this configuration instance to a file.

        The file is created with 0600 permissions (owner read/write only)
        from the moment it exists -- the mode is set before any content is
        written, and re-applied even when overwriting a file that already
        exists with looser permissions, so there is never a window where a
        config file containing credentials is world- or group-readable.

        Secret-bearing fields (passwords, tokens, API keys, etc. -- see
        ``SECRET_FIELDS``) are omitted by default, since a saved config file
        is easy to accidentally commit, back up, or share. Pass
        ``include_secrets=True`` to write them anyway, e.g. for a config
        file you deliberately keep out of version control.
        """
        config_path_obj = Path(config_path)
        config_data = asdict(self)
        if not include_secrets:
            for key in SECRET_FIELDS:
                config_data.pop(key, None)
            for key in SECRET_BEARING_MAPPINGS:
                value = config_data.get(key)
                if isinstance(value, dict):
                    config_data[key] = _redact_secret_entries(value)

        _write_config_file_securely(config_path_obj, config_data)

    @classmethod
    def load_from_file(cls, config_path: str) -> "ClusterConfig":
        """Load configuration from a file and return a new instance."""
        config_path_obj = Path(config_path)
        if not config_path_obj.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        with open(config_path_obj, "r") as f:
            if config_path_obj.suffix.lower() in [".yml", ".yaml"]:
                config_data = yaml.safe_load(f)
            else:
                config_data = json.load(f)

        return cls(**config_data)


# Fields treated as secret-bearing when saving configuration to disk. Derived
# from field *names* rather than hand-listed, so a newly added credential
# field (a new cloud provider's API key, say) is covered automatically
# instead of silently leaking in plaintext until someone remembers to add it
# here. Same approach as scripts/verify_cluster_usecases.py's redaction.
#: Every backend ``ClusterExecutor`` can actually dispatch. This is the one
#: place the set is written down; the CLI's ``click.Choice`` and the notebook
#: widget's dropdown both read it. Offering a type the executor cannot run is
#: worse than not offering it, and omitting one it can run hides a feature.
SUPPORTED_CLUSTER_TYPES = (
    "local",
    "ssh",
    "slurm",
    "huggingface",
)

#: Backends clustrix used to carry code for and no longer implements, mapped
#: to the issue tracking their return. Every one of them was removed for the
#: same reason: it had never been run against real hardware, so nothing
#: justified the claim that it worked. Keeping the names here is what lets a
#: user with an older ``clustrix.yml`` get an answer instead of a guess --
#: without it, ``cluster_type: pbs`` and a stale ``k8s_namespace`` key both
#: come back through ``difflib`` pointed at some unrelated field.
REMOVED_CLUSTER_TYPES = {
    "pbs": 140,
    "sge": 141,
    "kubernetes": 142,
    "aws": 143,
    "gcp": 144,
    "azure": 145,
    "lambda_cloud": 146,
    "huggingface_spaces": None,
}

#: Settings that belonged to the removed backends, as (prefix or exact name)
#: -> (what it configured, tracking issue). Checked before the did-you-mean
#: path in :func:`load_config`.
_REMOVED_SETTINGS = (
    ("k8s_", "Kubernetes", 142),
    ("auto_provision_k8s", "Kubernetes", 142),
    ("aws_", "the AWS backend", 143),
    ("eks_cluster_name", "the AWS backend", 143),
    ("gcp_", "the GCP backend", 144),
    ("gke_cluster_name", "the GCP backend", 144),
    ("azure_", "the Azure backend", 145),
    ("aks_cluster_name", "the Azure backend", 145),
    ("lambda_", "the Lambda Cloud backend", 146),
    ("cloud_provider", "the cloud VM backends", None),
    ("cloud_region", "the cloud VM backends", None),
    ("cloud_auto_configure", "the cloud VM backends", None),
    ("cost_monitoring", "cloud cost monitoring", None),
    ("hf_sdk", "the HuggingFace Spaces SDK", None),
)


def _removed_setting_reason(name: str) -> Optional[str]:
    """Explain a setting that a removed backend used to own, or return None."""
    for key, what, issue in _REMOVED_SETTINGS:
        matches = name.startswith(key) if key.endswith("_") else name == key
        if matches:
            where = f" (see issue #{issue})" if issue else ""
            return f"{name} configured {what}, which has been removed{where}"
    return None


_SECRET_FIELD_PATTERN = re.compile(
    r"secret|token|password|api_key|access_key|_key$|client_id|tenant_id"
    r"|subscription_id",
    re.IGNORECASE,
)

# Two kinds of name match the pattern above without holding a secret: a
# boolean flag (``use_env_password``) and a field that holds the *name* of
# an environment variable rather than its value (``password_env_var``).
# Dropping those breaks the auth-fallback configuration round trip while
# protecting nothing.
_NOT_ACTUALLY_SECRET = re.compile(r"^use_|_env_var$", re.IGNORECASE)


def _is_secret_field(field_name: str, field_type: object) -> bool:
    if _NOT_ACTUALLY_SECRET.search(field_name):
        return False
    return bool(_SECRET_FIELD_PATTERN.search(field_name))


SECRET_FIELDS = {
    f.name for f in fields(ClusterConfig) if _is_secret_field(f.name, f.type)
}

#: Fields holding a mapping whose *values* may be secrets even though the
#: field name is innocuous. ``environment_variables`` commonly carries both
#: ``OMP_NUM_THREADS`` and ``AWS_SECRET_ACCESS_KEY``; dropping the whole
#: mapping would lose ordinary settings users expect to persist, so the
#: individual entries are filtered by the same name test instead.
SECRET_BEARING_MAPPINGS = frozenset({"environment_variables"})


def _redact_secret_entries(mapping: dict) -> dict:
    """Drop the entries of ``mapping`` whose *key* names a secret."""
    return {
        k: v
        for k, v in mapping.items()
        if not _SECRET_FIELD_PATTERN.search(str(k))
        or _NOT_ACTUALLY_SECRET.search(str(k))
    }


def _write_config_file_securely(config_path_obj: Path, config_data: dict) -> None:
    """Write ``config_data`` to ``config_path_obj`` with 0600 permissions.

    The mode is applied via os.open()'s mode argument (so a newly created
    file never exists at the default, wider permissions even momentarily)
    and re-applied with fchmod() before writing (so overwriting a
    pre-existing, more permissive file is also tightened) -- in both cases
    before any content is written, never after.

    POSIX permission bits are a POSIX concept. On Windows there is no
    ``os.fchmod`` before Python 3.13, and even where ``chmod`` exists it only
    toggles the read-only attribute rather than restricting who may read the
    file, so the 0600 hardening step is skipped there and the file inherits
    the directory's ACL. See ``docs/source/limitations.rst`` for what that
    means for Windows users who save credentials to a config file.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(str(config_path_obj), flags, 0o600)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        handle = os.fdopen(fd, "w")
    except BaseException:
        # Nothing owns the descriptor yet, so it would otherwise leak; on
        # Windows a leaked handle also makes the file undeletable.
        os.close(fd)
        raise
    with handle as f:
        if config_path_obj.suffix.lower() in [".yml", ".yaml"]:
            yaml.dump(config_data, f, default_flow_style=False)
        else:
            json.dump(config_data, f, indent=2)


# Global configuration instance
_config = ClusterConfig()


def configure(**kwargs) -> None:
    """
    Configure Clustrix settings.

    Args:
        **kwargs: Configuration parameters matching ClusterConfig fields
    """
    global _config  # noqa: F824

    # Update configuration with provided kwargs
    for key, value in kwargs.items():
        if hasattr(_config, key):
            setattr(_config, key, value)
        else:
            raise ValueError(f"Unknown configuration parameter: {key}")


def load_config(config_path: str) -> None:
    """
    Load configuration from a file (JSON or YAML).

    Args:
        config_path: Path to configuration file
    """
    global _config

    config_path_obj = Path(config_path)
    if not config_path_obj.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_path_obj, "r") as f:
        if config_path_obj.suffix.lower() in [".yml", ".yaml"]:
            config_data = yaml.safe_load(f)
        else:
            config_data = json.load(f)

    if not isinstance(config_data, dict):
        raise ValueError(
            f"{config_path} does not contain a configuration mapping "
            f"(parsed as {type(config_data).__name__})."
        )

    # An unknown key used to surface as a bare
    # "ClusterConfig.__init__() got an unexpected keyword argument
    # 'cleanup_remote_files'", which names the internals rather than the file
    # the user wrote, and stops at the first offender.
    known = {f.name for f in fields(ClusterConfig)}
    unknown = sorted(set(config_data) - known)
    if unknown:
        import difflib

        hints = []
        for name in unknown:
            # A setting a removed backend owned gets a real explanation. The
            # did-you-mean path below would otherwise match "k8s_namespace"
            # against some unrelated field and send the reader after it.
            removed = _removed_setting_reason(name)
            if removed:
                hints.append(removed)
                continue
            close = difflib.get_close_matches(name, known, n=1, cutoff=0.6)
            hints.append(f"{name}" + (f" (did you mean {close[0]}?)" if close else ""))
        raise ValueError(
            f"{config_path} contains unknown setting(s): {'; '.join(hints)}"
        )

    requested = config_data.get("cluster_type")
    if requested in REMOVED_CLUSTER_TYPES:
        issue = REMOVED_CLUSTER_TYPES[requested]
        where = f" It is tracked in issue #{issue}." if issue else ""
        raise ValueError(
            f"{config_path} requests cluster_type={requested!r}, which clustrix "
            f"no longer implements. It was removed because it had never been "
            f"verified against real hardware.{where} Supported types are: "
            f"{', '.join(SUPPORTED_CLUSTER_TYPES)}."
        )

    _config = ClusterConfig(**config_data)


def save_config(config_path: str, include_secrets: bool = False) -> None:
    """
    Save current configuration to a file.

    See :meth:`ClusterConfig.save_to_file` for the 0600-permissions and
    secret-redaction behavior this delegates to.

    Args:
        config_path: Path where to save configuration
        include_secrets: Write secret-bearing fields (passwords, tokens,
            API keys, etc.) in plaintext. Default False.
    """
    _config.save_to_file(config_path, include_secrets=include_secrets)


CONFIG_DIR_ENV_VAR = "CLUSTRIX_CONFIG_DIR"


def get_config_dir() -> Path:
    """Return the directory clustrix reads and writes user configuration in.

    Defaults to ``~/.clustrix``. Setting ``CLUSTRIX_CONFIG_DIR`` redirects it,
    which matters in three situations: containers and CI images where ``$HOME``
    is not writable or not persistent, machines shared by several projects, and
    tests. Without an override the notebook widget's "save configuration"
    button writes into the developer's own ``~/.clustrix`` during a test run,
    silently editing real cluster profiles.
    """
    override = os.environ.get(CONFIG_DIR_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".clustrix"


def get_config() -> ClusterConfig:
    """Get current configuration."""
    return _config


# Try to load configuration from default locations
def _load_default_config():
    """Load configuration from default locations."""
    default_paths = []
    try:
        config_dir = get_config_dir()
    except RuntimeError:
        # Path.home() raises when the home directory cannot be determined --
        # e.g. a Windows service account or a scrubbed environment with no
        # USERPROFILE. Discovering a user config file is best effort, so this
        # must not make ``import clustrix`` fail; the working-directory
        # candidates below are still searched.
        pass
    else:
        default_paths += [
            config_dir / "config.yml",
            config_dir / "config.yaml",
            config_dir / "config.json",
        ]
    default_paths += [
        Path.cwd() / "clustrix.yml",
        Path.cwd() / "clustrix.yaml",
        Path.cwd() / "clustrix.json",
    ]

    for path in default_paths:
        if path.exists():
            try:
                load_config(str(path))
                break
            except Exception:
                continue


# Load default configuration on import
_load_default_config()
