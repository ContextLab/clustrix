import json
import logging
import re
import secrets as _secrets
import threading
import yaml
import os
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict, fields

logger = logging.getLogger(__name__)


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

    # Data staging (clustrix/staging.py). A data package below
    # stage_inline_max_bytes rides inside the package object itself and needs
    # no remote store at all; above it, the contents go to a private
    # HuggingFace dataset repo -- hf_data_repo overrides where, and defaults
    # to "<hf_namespace>/clustrix-data".
    #
    # The two size bands above that are about not surprising anyone:
    # stage_warn_bytes logs before a slow transfer, and stage_max_bytes
    # refuses outright, because a silent multi-hour upload is indistinguishable
    # from a hang. Nothing staged is ever reclaimed automatically -- deletion
    # is always an explicit act by the user, so there is no TTL or size cap on
    # the store itself here by design.
    hf_data_repo: Optional[str] = None
    stage_inline_max_bytes: int = 1 * 1024 * 1024  # 1 MB
    stage_warn_bytes: int = 100 * 1024 * 1024  # 100 MB
    stage_max_bytes: int = 5 * 1024 * 1024 * 1024  # 5 GB

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
    # Seconds to keep polling a submitted job before giving up. Without a
    # bound, a job that never reaches a terminal state -- held by the
    # scheduler, stuck in a node-drain loop, a queue that never clears --
    # hangs the caller forever with no way out but Ctrl-C. 24 hours is
    # deliberately generous, because a real HPC queue wait legitimately runs
    # into hours; set it to None to restore the unbounded wait.
    job_wait_timeout: Optional[int] = 86400
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

        validate_cluster_type(self.cluster_type)

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
            config_data = strip_secret_fields(config_data)

        write_config_file_securely(config_path_obj, config_data)

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


def validate_cluster_type(cluster_type: str, source: str = "cluster_type") -> None:
    """Reject a backend clustrix cannot run, saying which kind of wrong it is.

    Three outcomes rather than two: a supported type passes, a type clustrix
    knows about but does not implement is named along with why and where it is
    tracked, and anything else is an ordinary typo. Collapsing the middle case
    into the last one is what left ``cluster_type: pbs`` looking like a
    spelling mistake.
    """
    if cluster_type in SUPPORTED_CLUSTER_TYPES:
        return

    supported = ", ".join(SUPPORTED_CLUSTER_TYPES)
    if cluster_type in REMOVED_CLUSTER_TYPES:
        issue = REMOVED_CLUSTER_TYPES[cluster_type]
        where = f" Support for it is tracked in issue #{issue}." if issue else ""
        raise ValueError(
            f"{source}={cluster_type!r} is not implemented. Clustrix ships no "
            f"backend for it, because none has been verified against real "
            f"hardware of that kind, so there is no code path that would run "
            f"your function there.{where} Supported types are: {supported}."
        )

    raise ValueError(
        f"{source}={cluster_type!r} is not a supported cluster type. "
        f"Supported types are: {supported}."
    )


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


def strip_secret_fields(config_data: dict) -> dict:
    """Return ``config_data`` with every credential-bearing entry removed.

    One implementation of "what may not reach disk", so that a second
    persistence path cannot quietly disagree with
    :meth:`ClusterConfig.save_to_file`. ``clustrix/profile_manager.py``
    used to serialise ``asdict(config)`` directly and therefore wrote
    passwords and API tokens in plaintext, in a file that
    :meth:`ClusterConfig.save_to_file` would have withheld them from.

    Both the whole-field cases (``SECRET_FIELDS``) and the entries inside
    a secret-bearing mapping (``SECRET_BEARING_MAPPINGS``) are handled.
    """
    stripped = {k: v for k, v in config_data.items() if k not in SECRET_FIELDS}
    for key in SECRET_BEARING_MAPPINGS:
        value = stripped.get(key)
        if isinstance(value, dict):
            stripped[key] = _redact_secret_entries(value)
    return stripped


def write_text_securely(path: Path, text: str, *, append: bool = False) -> None:
    """Write ``text`` to ``path`` without ever exposing it to other users.

    ``path.write_text(...)`` followed by ``path.chmod(0o600)`` looks
    equivalent and is not: the file exists, with the credentials already in
    it, at ``0o666 & ~umask`` for the whole window between the two calls.
    With the default umask that is mode 0644 -- world readable -- and any
    other local process can win that race (issue #111).

    What this guarantees, exactly:

    * **Default (``append=False``).** The secret is written into a
      brand-new inode that this call created, in the destination's own
      directory, and that inode is then ``os.replace()``-d into position.
      The scratch file is created with ``O_CREAT | O_EXCL | O_NOFOLLOW``
      and mode ``0o600`` under a name nothing else can guess, so it is
      never wider than ``0o600 & ~umask`` at any instant. ``fchmod()`` on
      the descriptor we exclusively own pins the mode at exactly 0600
      regardless of umask, before any content is written.

      Writing into a scratch file rather than into ``path`` itself is what
      makes this safe in three separate ways:

      1. *It cannot lose data.* An earlier version unlinked ``path`` and
         then created it afresh. If the create failed -- ENOSPC, or an
         EEXIST because something was planted in the gap -- the original
         file was already gone and nothing had been written in its place.
         ``os.replace()`` is atomic: either the new content is in position
         or the old file is untouched, and a failure anywhere before it
         leaves the destination exactly as it was.
      2. *It closes the descriptor window.* Reusing a pre-existing 0666
         inode (``O_TRUNC``) leaves it at 0666 between ``os.open()`` and
         ``os.fchmod()``, and a process that opens it during that window
         keeps a readable descriptor after the mode is narrowed --
         measured, and it really does read the secret back. Nothing can
         have a descriptor on an inode that did not exist until now.
      3. *It disposes of the symlink case.* ``path`` being a symlink used
         to mean the secret was written to the link's *target* and the
         target was chmodded. ``os.replace()`` replaces the link itself,
         leaving the target untouched.

      The scratch file is removed if anything fails, so a failed write
      leaves neither a partial file in position nor litter beside it.
    * **``append=True``.** The content is appended, so an existing file
      cannot be replaced and its mode is left alone -- this call does not
      own it. All that is guaranteed is that a file *this call creates* is
      0600 from the instant it exists. This mode exists for
      ``~/.ssh/config`` and ``~/.ssh/known_hosts``: neither holds a secret,
      both must keep the content already in them, and both are commonly a
      symlink into a dotfiles repository, so ``O_NOFOLLOW`` is deliberately
      not applied and no ``chmod`` is performed on a file the user manages.

    Neither mode is atomic against an attacker who can create files in the
    containing directory; they fail loudly instead of writing into
    somebody else's file.

    Windows caveat, shared with ``clustrix.config.write_config_file_securely``:
    ``os.fchmod`` does not exist there before Python 3.13, ``os.O_NOFOLLOW``
    does not exist at all, and ``chmod`` only toggles the read-only
    attribute rather than restricting who may read, so on Windows the file
    inherits the directory's ACL.
    """
    if append:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            handle = os.fdopen(fd, "a", encoding="utf-8")
        except BaseException:
            # Nothing owns the descriptor yet, so it would otherwise leak;
            # on Windows a leaked handle also makes the file undeletable.
            os.close(fd)
            raise
        with handle as f:
            f.write(text)
        return

    # A name in the destination's own directory: os.replace() is only
    # atomic within a filesystem, and /tmp is frequently a different one.
    # The random component means a scratch path cannot be predicted and
    # pre-created by another local process.
    scratch = path.parent / f".{path.name}.{_secrets.token_hex(8)}.tmp"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)  # absent on Windows
    )
    fd = os.open(str(scratch), flags, 0o600)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        handle = os.fdopen(fd, "w", encoding="utf-8")
    except BaseException:
        os.close(fd)
        _discard(scratch)
        raise

    try:
        with handle as f:
            f.write(text)
        os.replace(scratch, path)
    except BaseException:
        # The destination is untouched; drop the half-written scratch file
        # rather than leaving a copy of the secret beside it.
        _discard(scratch)
        raise


def _discard(path: Path) -> None:
    """Remove ``path``, ignoring the case where it is already gone."""
    try:
        os.unlink(path)
    except OSError:
        pass


def write_config_file_securely(config_path_obj: Path, config_data: dict) -> None:
    """Write ``config_data`` to ``config_path_obj`` with 0600 permissions.

    Renders first and hands the text to :func:`write_text_securely`, which
    is the one implementation of "put this on disk without ever exposing
    it". This function used to carry its own copy, and the copy had drifted
    into being wrong in two ways: ``O_TRUNC`` on a pre-existing inode left
    the file at its old, wider mode between ``os.open`` and ``os.fchmod``,
    so a process that opened it during that window kept a readable
    descriptor after the mode was narrowed; and without ``O_NOFOLLOW`` a
    symlink at ``config_path_obj`` meant the configuration was written to
    the link's target and the target was chmodded.

    Rendering to a string first is also what keeps the window shut: nothing
    can fail halfway through serialisation with a descriptor already open
    on the destination.

    See :func:`write_text_securely` for the Windows caveat -- the 0600
    hardening is a POSIX concept and is skipped there.
    """
    if config_path_obj.suffix.lower() in [".yml", ".yaml"]:
        rendered = yaml.dump(config_data, default_flow_style=False)
    else:
        rendered = json.dumps(config_data, indent=2)
    write_text_securely(config_path_obj, rendered)


# Global configuration instance.
#
# Constructing it is pure: ``__post_init__`` fills in mutable defaults and
# validates two fields, and opens no file, socket or subprocess. Reading the
# user's *configuration file* is the part that must not happen at import --
# see ``_ensure_default_config_loaded`` at the bottom of this module.
_config = ClusterConfig()


class ConfigFileError(RuntimeError):
    """A configuration file was found in a standard location and is unusable.

    Raised on first use of the configuration rather than at import, and
    deliberately not swallowed: a file the user wrote that clustrix cannot
    read is an instruction it cannot carry out, and continuing on built-in
    defaults would run their job somewhere other than where they said.
    """


def configure(**kwargs) -> None:
    """
    Configure Clustrix settings.

    Args:
        **kwargs: Configuration parameters matching ClusterConfig fields
    """
    global _config  # noqa: F824

    # The file is the layer underneath these keywords (defaults -> file ->
    # runtime), so it has to be in place before they are applied on top --
    # otherwise a later get_config() would run the search and overwrite them.
    _ensure_default_config_loaded()

    # Validate everything before applying anything: a rejected keyword used
    # to leave the earlier ones already written to the live config, so a
    # failed configure() call still changed the process's behaviour.
    for key in kwargs:
        if hasattr(_config, key):
            continue
        removed = _removed_setting_reason(key)
        if removed:
            raise ValueError(removed)
        raise ValueError(f"Unknown configuration parameter: {key}")

    if "cluster_type" in kwargs:
        # setattr below does not re-run __post_init__, so without this a
        # removed backend reaches the executor and fails there instead --
        # after connect(), i.e. after an SSH round trip to a host that was
        # never going to be used.
        validate_cluster_type(kwargs["cluster_type"])

    for key, value in kwargs.items():
        setattr(_config, key, value)


def load_config(config_path: str) -> None:
    """
    Load configuration from a file (JSON or YAML).

    Runs under ``_DEFAULT_CONFIG_LOCK``, which is not decoration. The lazy
    search of the standard locations (see :func:`_ensure_default_config_loaded`)
    also rebinds ``_config``, and it now runs on whichever thread happens to
    touch the configuration first rather than during the import. Without this
    lock the two writers interleave: a thread that entered the search *before*
    an explicit ``load_config`` can finish *after* it and rebind ``_config``
    to the file it found in ``~/.clustrix`` -- so the explicitly loaded file is
    accepted, reported as loaded, and then thrown away. That is exactly the
    defect class this module is being fixed for, so it does not get to be
    reintroduced by the fix. The lock is an ``RLock`` because the search
    itself calls this function.

    Holding it across the parse as well as the assignment means the winner is
    the last caller to *enter*, not the last to *finish*; a slow large file
    cannot land on top of a small one loaded after it.

    Args:
        config_path: Path to configuration file
    """
    with _DEFAULT_CONFIG_LOCK:
        _load_config_locked(config_path)


def _load_config_locked(config_path: str) -> None:
    """Body of :func:`load_config`; callers must hold ``_DEFAULT_CONFIG_LOCK``."""
    global _config, _default_config_loaded

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

    if "cluster_type" in config_data:
        validate_cluster_type(
            config_data["cluster_type"], source=f"{config_path}: cluster_type"
        )

    _config = ClusterConfig(**config_data)
    # An explicit load replaces the configuration wholesale, so the search of
    # the standard locations has nothing left to contribute. Marking it done
    # stops a later get_config() from discarding what was just loaded.
    _default_config_loaded = True


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
    _ensure_default_config_loaded()
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
    """Get current configuration.

    This is where the search of the standard locations for a user
    configuration file actually happens, the first time anything asks. Every
    read of the singleton goes through this function, and nothing anywhere in
    the repository binds ``_config`` by name, so deferring the search to here
    is complete. That second half is not an assertion of good intentions: it
    is checked by
    ``tests/unit/test_import_has_no_side_effects.py::test_nothing_binds_the_singleton_by_name``,
    which found two by-name importers the first time it was run.
    """
    _ensure_default_config_loaded()
    return _config


def _default_config_candidates() -> List[Path]:
    """The paths searched for a user configuration file, in priority order."""
    candidates: List[Path] = []
    try:
        config_dir = get_config_dir()
    except RuntimeError as exc:
        # Path.home() raises when the home directory cannot be determined --
        # e.g. a Windows service account or a scrubbed environment with no
        # USERPROFILE. The working-directory candidates below are still
        # searched, but say so: silently searching three of six locations is
        # how a config file that is definitely there appears not to be.
        logger.warning(
            "Could not determine a configuration directory (%s), so the "
            "per-user location was not searched. Set %s to point at it.",
            exc,
            CONFIG_DIR_ENV_VAR,
        )
    else:
        candidates += [
            config_dir / "config.yml",
            config_dir / "config.yaml",
            config_dir / "config.json",
        ]
    try:
        cwd = Path.cwd()
    except OSError as exc:
        # getcwd() fails for real: a directory deleted out from under a
        # long-running process, or one the process may no longer read. The
        # per-user candidates above are unaffected, so the search continues
        # with what it can still reach -- having said which half it skipped.
        logger.warning(
            "Could not determine the current working directory (%s), so it "
            "was not searched for a clustrix configuration file.",
            exc,
        )
    else:
        candidates += [
            cwd / "clustrix.yml",
            cwd / "clustrix.yaml",
            cwd / "clustrix.json",
        ]
    return candidates


def _load_default_config() -> None:
    """Adopt the first configuration file found in the standard locations.

    Two different failures used to be indistinguishable from the outside, and
    both presented as "there is no configuration file":

    * A candidate that could not be *stat*ed. ``Path.exists()`` answers False
      for ENOENT but propagates EACCES, and that call sat outside the try --
      so a ``~/.clustrix`` the process could not read made ``import clustrix``
      raise ``PermissionError`` from four frames inside a private function.
    * A candidate that was found and then failed to load -- truncated YAML, a
      typo'd setting name -- which was skipped in silence. The process then
      ran on built-in defaults while the user believed their file was in
      force, and that is the expensive one: a ``cluster_host`` that never took
      effect means the job ran somewhere other than where it was told to.

    The two are now told apart. "I could not look there" is a warning and the
    search moves on, because there is still a correct answer to be had from
    the remaining candidates. "I looked, found your file, and cannot use it"
    raises, because there is not.
    """
    for path in _default_config_candidates():
        try:
            found = path.exists()
        except OSError as exc:
            logger.warning(
                "Could not check for a clustrix configuration file at %s (%s). "
                "Any settings in that location are NOT in effect.",
                path,
                exc,
            )
            continue
        if not found:
            continue
        try:
            load_config(str(path))
        except Exception as exc:
            raise ConfigFileError(
                f"The clustrix configuration file {path} was found but could "
                f"not be loaded: {exc}. Its settings are NOT in effect. Fix "
                f"the file, move it aside, or load a different one with "
                f"clustrix.config.load_config(path)."
            ) from exc
        logger.debug("Loaded clustrix configuration from %s", path)
        return


_default_config_loaded = False
_default_config_loading = False
_DEFAULT_CONFIG_LOCK = threading.RLock()


def _reset_default_config_lock_after_fork() -> None:
    """Make the lock and the in-progress flag mean something in a forked child.

    ``fork`` copies the memory of the calling thread only. If any *other*
    thread held ``_DEFAULT_CONFIG_LOCK`` at that instant -- which is precisely
    the window the lazy search opened, because the search now runs on whichever
    thread touches the configuration first and holds the lock for its whole
    duration -- then the child inherits a lock that is recorded as held by a
    thread that does not exist in the child and can never release it. The
    child's first ``get_config()`` blocks forever. It inherits
    ``_default_config_loading = True`` for the same reason, set by that same
    absent thread.

    This is not hypothetical for this package: ``LocalExecutor`` runs work in a
    ``ProcessPoolExecutor``, and ``fork`` is a real start method (the default
    on Linux). A worker whose first act is to read the configuration would
    hang rather than fail.

    A forked child is single-threaded at this point, so nothing can be
    contending: replacing the lock outright is safe, and it is the only
    available repair -- an inherited held lock has no owner left to release it.
    ``_default_config_loaded`` is deliberately *not* touched. If the parent had
    finished, the child inherits both the flag and the loaded ``_config`` and
    is consistent; if it had not, the flag is already False and the child
    simply redoes the search itself.
    """
    global _DEFAULT_CONFIG_LOCK, _default_config_loading
    _DEFAULT_CONFIG_LOCK = threading.RLock()
    _default_config_loading = False


if hasattr(os, "register_at_fork"):  # not available on Windows
    os.register_at_fork(after_in_child=_reset_default_config_lock_after_fork)


def _ensure_default_config_loaded() -> None:
    """Search the standard locations once, on first use rather than on import.

    ``import clustrix`` used to read the user's home directory and the current
    working directory as a side effect of the import statement. Two things
    were wrong with that. It made importing a library do I/O nobody had asked
    for yet -- including picking up a ``./clustrix.yml`` belonging to whatever
    directory the process happened to start in -- and it put a whole class of
    failure (an unreadable ``~/.clustrix``) inside an import, where there is
    no caller in a position to handle it.

    The singleton itself stays eager: ``_config = ClusterConfig()`` allocates
    an object and touches nothing. Only the file read moved. That split is
    what makes this safe, because every read of the singleton goes through
    :func:`get_config`: nothing in the repository does
    ``from .config import _config``, so there is no route by which a caller
    can observe the pre-search object. A by-name importer would also be
    holding the wrong object after any :func:`load_config`, which *rebinds*
    the module attribute. The property is enforced by
    ``test_nothing_binds_the_singleton_by_name`` rather than asserted here --
    it was stated in this docstring before it was true, and a test fixture and
    a script were both binding it by name at the time.

    The lock makes concurrent first calls do the search exactly once, and two
    separate flags are needed to keep that correct:

    ``_default_config_loaded`` is the *published* answer, and it is set only
    after the search has finished. Setting it first -- to guard against
    re-entrancy -- is a race, and a measured one: a second thread takes the
    unlocked fast path at the top, sees the flag already true, and returns the
    singleton as it stood *before* the file was applied. Half the threads then
    hold a configuration with no ``cluster_host``.

    ``_default_config_loading`` is the re-entrancy guard instead. It is only
    ever read with the lock held, and the lock is held for the whole search,
    so the only thread that can observe it true is the one that set it.

    Neither flag sticks on failure: an unusable configuration file keeps
    failing rather than failing once and then quietly reporting built-in
    defaults ever after.
    """
    global _default_config_loaded, _default_config_loading
    if _default_config_loaded:
        return
    with _DEFAULT_CONFIG_LOCK:
        if _default_config_loaded or _default_config_loading:
            return
        _default_config_loading = True
        try:
            _load_default_config()
        finally:
            _default_config_loading = False
        _default_config_loaded = True
