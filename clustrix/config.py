import json
import re
import secrets as _secrets
import yaml
import os
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Tuple
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
#: place the set is written down; the CLI's ``click.Choice`` and *both*
#: notebook widgets' dropdowns read it -- ``notebook_magic_widget`` spelled
#: the four values out until #165, which is exactly the drift this comment
#: claimed was impossible. Offering a type the executor cannot run is
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


# Global configuration instance
_config = ClusterConfig()


def configure(**kwargs) -> None:
    """
    Configure Clustrix settings.

    Args:
        **kwargs: Configuration parameters matching ClusterConfig fields
    """
    global _config  # noqa: F824

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


def config_field_names() -> FrozenSet[str]:
    """Every name :func:`configure` will accept.

    Derived from the dataclass rather than listed, because a list is only
    correct until the next field is added and nothing makes it fail loudly
    when it stops being.
    """
    return frozenset(field.name for field in fields(ClusterConfig))


def split_config_kwargs(
    data: Mapping[str, Any],
    bookkeeping: Iterable[str] = (),
    reset_fields: Iterable[str] = (),
) -> Tuple[Dict[str, Any], List[str]]:
    """Split a saved configuration into what :func:`configure` accepts, and
    the names it does not.

    :func:`configure` rejects an unknown keyword on purpose -- a silently
    ignored setting is worse than a rejected one -- so a caller holding a
    dict that mixes settings with its own bookkeeping (a profile's ``name``,
    say) has to do the separating itself. This is that separation, in one
    place, so the widgets cannot drift apart on what a configuration key is.

    ``bookkeeping`` names the keys the caller knows are not settings and
    means to drop. Anything else that is not a field comes back in the
    second return value instead of vanishing: a key nobody recognises is
    either a stale profile written by an older clustrix or a control wired
    to a name that no longer exists, and both deserve to be said out loud
    rather than dropped on the floor.

    ``reset_fields`` names the fields the caller *owns*: every one of them is
    seeded with its :class:`ClusterConfig` default before ``data`` is laid on
    top, so a control the user cleared clears the live setting instead of
    leaving the previous configuration's value standing. Without it a caller
    that drops empty values -- which both widgets do, so a blank box does not
    overwrite a setting with an empty string -- can never say "unset this",
    and a profile the user chose as ``local`` inherits the last profile's
    ``cluster_host``. Fields outside this set are not touched at all, so
    settings with no control anywhere survive an Apply. A name in
    ``reset_fields`` that is not a field is reported rather than reset: it is
    a control wired to a name that no longer exists.
    """
    accepted = config_field_names()
    known_extras = set(bookkeeping)
    owned = list(reset_fields)
    defaults = asdict(ClusterConfig())
    kwargs = {name: defaults[name] for name in owned if name in accepted}
    kwargs.update({key: value for key, value in data.items() if key in accepted})
    unrecognised = sorted(
        {
            key
            for key in list(data) + owned
            if key not in accepted and key not in known_extras
        }
    )
    return kwargs, unrecognised


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

    if "cluster_type" in config_data:
        validate_cluster_type(
            config_data["cluster_type"], source=f"{config_path}: cluster_type"
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
