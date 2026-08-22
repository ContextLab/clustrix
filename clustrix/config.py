import collections.abc as collections_abc
import contextlib
import contextvars
import json
import logging
import re
import secrets as _secrets
import stat
import threading
import warnings
import yaml
import os
from pathlib import Path
from typing import (
    Any,
    Dict,
    FrozenSet,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Tuple,
    get_args,
    get_origin,
)
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
    # An existing conda environment on the cluster to run jobs in. It wins
    # over environment replication -- see job_execution_lines() -- and is
    # the standing-configuration spelling of @cluster(environment=...).
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
            elif field_def.name in UNCLASSIFIABLE_FIELDS and isinstance(value, dict):
                # Every value, not the ones whose key name looks secret: the
                # names are the user's, so ``GITHUB_PAT`` and
                # ``SSH_PASSPHRASE`` are as likely as ``AWS_SECRET_ACCESS_KEY``
                # and neither is recognisable. The names stay visible, so the
                # repr still says what is configured.
                value = {k: "***" for k in value}
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
        validate_conda_env_name(self.conda_env_name)

        # A ``cluster_host`` that is truthy but not a usable hostname is
        # rejected here rather than carried. ``normalize_hostname`` is the one
        # comparison the provenance record and every credential check are
        # built on, and it answers ``""`` for anything that is not a non-empty
        # string -- so a host it cannot normalise is a host that cannot be
        # recorded as tainted and cannot be matched against a credential.
        # ``set_config_source`` skipped such a value silently, which let it
        # slip past the record entirely: PyYAML parses ``cluster_host:
        # 0x7f000001`` as the *int* 2130706433, and an int is exactly the kind
        # of "truthy, unnormalisable" value that was never written down and so
        # was laundered to ``runtime`` by the next rebuild. Failing closed at
        # construction closes that off at the only point every route passes
        # through, and gives the user an error naming their own file instead
        # of a refusal much later.
        if self.cluster_host and not normalize_hostname(self.cluster_host):
            raise ValueError(
                f"cluster_host={self.cluster_host!r} is not a usable hostname. "
                f"It must be a non-empty string; note that YAML parses an "
                f"unquoted 0x7f000001 or 1e5 as a number, so quote a hostname "
                f"that could be read as one."
            )

        # Where this configuration came from. A ``ClusterConfig(...)`` call is
        # somebody's Python, so the default is the trusted end of the scale --
        # but only when nobody is currently reading a *file*. A config built
        # from parsed file content is not a config the user constructed in
        # Python however it is spelled, so every loader declares itself with
        # ``config_built_from_file`` and this picks the declaration up. See
        # ``CONFIG_SOURCE_*`` below.
        #
        # ``record_host=False`` because this is a *construction*, and what it
        # can establish is bounded by that. "Something untrusted is being read
        # nearby, so distrust this object" costs one refusal and is reversible
        # by building another object. "This hostname was named by a file, so
        # refuse it process-wide forever" is a far stronger claim with no way
        # back, and only the loader that opened the file can make it -- which
        # it does, explicitly. See ``_HOSTS_NAMED_BY_UNTRUSTED_SOURCES``.
        set_config_source(self, _source_being_read(), record_host=False)

    def save_to_file(self, config_path: str, include_secrets: bool = False) -> None:
        """Save this configuration instance to a file.

        The file is created with 0600 permissions (owner read/write only)
        from the moment it exists -- the mode is set before any content is
        written, and re-applied even when overwriting a file that already
        exists with looser permissions, so there is never a window where a
        config file containing credentials is world- or group-readable.

        Secret-bearing fields (passwords, tokens, API keys, etc. -- see
        ``SECRET_FIELDS``) are omitted by default, since a saved config file
        is easy to accidentally commit, back up, or share. So is every
        mapping field -- ``environment_variables``, ``gpu_requirements`` and
        ``venv_info`` -- whose keys and values are the user's, so nothing
        tells ``OMP_NUM_THREADS=4`` from ``GITHUB_PAT=<a token>``
        (see ``UNCLASSIFIABLE_FIELDS``). Pass ``include_secrets=True`` to
        write all of it anyway, e.g. for a config file you deliberately keep
        out of version control.

        **The source this configuration came from is written down too**, and
        only when it is an untrusted one. Route 12 was the widget's Save
        laundering a repository's configuration into ``~/.clustrix``; this
        method is the second writer of exactly the same file, and its
        precondition is only that the caller names the destination --
        ``clustrix config --config-file ~/.clustrix/config.yml`` inside a
        cloned repository, or ``get_config().save_to_file(...)``. What a save
        writes IS a credential decision one restart later, so the record is a
        property of the write path rather than of one button. See
        :data:`CONFIG_SOURCES_KEY` for the key and
        :func:`config_source_for_saved_entry` for the downgrade-only rule
        that stops it becoming a laundering route in its own right.
        """
        write_config_file_securely(
            Path(config_path), config_document(self, include_secrets=include_secrets)
        )

    @classmethod
    def load_from_file(cls, config_path: str) -> "ClusterConfig":
        """Load configuration from a file and return a new instance.

        ``explicit-file``, like :func:`load_config`: the caller named the
        path, and naming a path is the choice the automatic search does not
        have. Declared rather than left to ``__post_init__``'s default,
        because the default is ``runtime`` and this content came off a disk.
        """
        config_path_obj = Path(config_path)
        if not config_path_obj.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        with open(config_path_obj, "r") as f:
            if config_path_obj.suffix.lower() in [".yml", ".yaml"]:
                config_data = yaml.safe_load(f)
            else:
                config_data = json.load(f)

        return cls.from_file_content(
            config_data, CONFIG_SOURCE_EXPLICIT_FILE, origin=str(config_path_obj)
        )

    @classmethod
    def from_file_content(
        cls,
        mapping: Mapping[str, Any],
        source: str,
        *,
        origin: Optional[str] = None,
    ) -> "ClusterConfig":
        """The only supported way to build a config out of parsed file bytes.

        **Provenance is an argument, not ambient context.** Every loader in
        the tree used to construct ``ClusterConfig(**parsed)`` and *remember*
        to wrap it in :func:`config_built_from_file`; ``ProfileManager`` did
        not, so a profile store shipped by a repository came back stamped
        ``runtime`` -- the trusted end of the scale -- and the victim's
        ``SSH_PASSWORD`` reached the repository's host. Here the source is a
        required parameter: ``from_file_content(mapping)`` is a ``TypeError``
        and there is nothing to forget. Because it is an argument rather than
        a context variable, it also survives being handed to another thread,
        which a ``ContextVar`` declaration does not.

        The declaration is kept as well as the stamp, because they answer
        different questions: the stamp records where *this object* came from,
        and the declaration is what any other ``ClusterConfig`` built from
        this file's content gets -- including ones built by code this calls.

        A file may also carry clustrix's own earlier record of where it came
        from, under :data:`CONFIG_SOURCES_KEY`. That record is clustrix's
        bookkeeping rather than a declared field, so it is removed before
        validation -- leaving it in would reject every file either writer
        produced -- and it may only *lower* the source the caller established,
        never raise it: see :func:`config_source_for_saved_entry`.

        ``origin`` names the file in error messages and is cosmetic;
        ``source`` is the security-relevant one.
        """
        # Work on a copy: the mapping belongs to the caller, and removing the
        # bookkeeping key from the caller's dict would make a second load of
        # the same content validate differently than the first.
        content = dict(mapping)
        recorded = content.pop(CONFIG_SOURCES_KEY, None)
        effective = config_source_for_saved_entry(source, recorded)
        where = origin or f"the {source} configuration"
        _validate_config_mapping(content, where)
        with config_built_from_file(effective):
            config = cls(**content)
        # The loader opened the file, so it -- unlike ``__post_init__``,
        # which infers -- may write the hostname into the process-wide
        # record. See ``set_config_source``.
        set_config_source(config, effective)
        return config


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


def validate_conda_env_name(
    value: Optional[str], source: str = "conda_env_name"
) -> None:
    """Refuse a ``conda_env_name`` that is not an environment name.

    Run here, and from ``configure()``, so the claim that a path is "refused
    at configuration time" is true. It was not: ``configure(conda_env_name=
    "/scratch/envs/prod")`` was accepted and the refusal came from
    ``resolve_named_environment`` at submission -- after the job directory had
    been created on the cluster, the signing key written and the pickle
    uploaded. The rules themselves are ``utils.validate_environment_name``'s,
    imported late because ``utils`` imports this module.

    An unset or empty value is not a name and not an error: it means "no
    environment was named", which is the default.
    """
    if value is None or not str(value).strip():
        return
    from .utils import validate_environment_name

    validate_environment_name(source, str(value).strip())


def _validate_config_mapping(mapping: Mapping[str, Any], origin: str) -> None:
    """Reject settings ``ClusterConfig`` does not have, naming the file.

    An unknown key used to surface as a bare
    "ClusterConfig.__init__() got an unexpected keyword argument
    'cleanup_remote_files'", which names the internals rather than the file
    the user wrote, and stops at the first offender.
    """
    known = {f.name for f in fields(ClusterConfig)}
    unknown = sorted(set(mapping) - known)
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
        raise ValueError(f"{origin} contains unknown setting(s): {'; '.join(hints)}")

    if "cluster_type" in mapping:
        validate_cluster_type(mapping["cluster_type"], source=f"{origin}: cluster_type")

    if "conda_env_name" in mapping:
        # Named by the file, so the refusal names the file: a bare message
        # would send the user hunting for the configure() call that did not
        # write it.
        validate_conda_env_name(
            mapping["conda_env_name"], source=f"{origin}: conda_env_name"
        )


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

#: Every name ``_is_secret_field`` will answer for. The classifier is sound
#: over the ``ClusterConfig`` fields it was written for and over nothing
#: else, so this is the domain, enforced rather than described.
DECLARED_FIELD_NAMES = frozenset(f.name for f in fields(ClusterConfig))


def _is_secret_field(field_name: str) -> bool:
    """Classify one *declared* ``ClusterConfig`` field. Not for other keys.

    The exemptions matter and are narrow. ``^use_`` describes the boolean
    ``use_env_password`` and ``_env_var$`` describes ``password_env_var``,
    which holds the *name* of an environment variable rather than its value;
    dropping either breaks the auth-fallback round trip while protecting
    nothing. Neither is a statement about names in general, and applying
    them to arbitrary keys was a hole: a user-chosen environment variable
    called ``USE_PASSWORD`` was exempted by a rule about a flag it has
    nothing to do with.

    That domain restriction used to be expressed by resolving the exemption
    regex against the declared field names once, into a ``NOT_SECRET_FIELDS``
    frozenset. It was **vacuous**: this function has exactly one caller, the
    ``SECRET_FIELDS`` comprehension immediately below, which only ever passes
    declared field names -- so the frozen set was equal to the regex by
    construction and replacing one with the other changed nothing. A mutation
    test confirmed it (mutant M10, "unfreeze ``^use_``", survived), and a
    guard that cannot fail is worse than no guard, because the reader thinks
    it is protected. The ``USE_PASSWORD`` hole was closed by
    ``UNCLASSIFIABLE_FIELDS`` withholding ``environment_variables`` whole,
    not by the freeze.

    So the restriction is enforced instead of asserted: a name that is not a
    declared field is refused rather than classified. There is no correct
    answer for one -- ``strip_secret_fields`` uses ``PERSISTABLE_KEYS`` to
    exclude it long before this could be asked -- and returning ``False``
    for it is how the exemption escaped its domain in the first place.
    """
    if field_name not in DECLARED_FIELD_NAMES:
        raise ValueError(
            f"{field_name!r} is not a ClusterConfig field, and this "
            f"classifier is only sound over the fields it was written for. "
            f"A key from outside the dataclass is excluded by "
            f"PERSISTABLE_KEYS; it must not be handed an exemption here."
        )
    if _NOT_ACTUALLY_SECRET.search(field_name):
        return False
    return bool(_SECRET_FIELD_PATTERN.search(field_name))


SECRET_FIELDS = {name for name in DECLARED_FIELD_NAMES if _is_secret_field(name)}

#: The one key a clustrix configuration file carries that is not a
#: ``ClusterConfig`` field: the label the notebook widget shows in its
#: dropdown, which it writes and reads back (see
#: ``EnhancedClusterConfigWidget._initialize_configs``). Named here so that
#: the allowlist below is the file format's own vocabulary rather than the
#: dataclass's by accident.
CONFIG_FILE_METADATA_KEYS = frozenset({"name"})

#: Every key a configuration file may contain. Nothing else is written,
#: because nothing else can be read back: ``ClusterConfig.load_from_file``
#: does ``cls(**config_data)``, ``ProfileManager`` filters to the declared
#: fields, and ``configure()`` ignores what it does not know. An unknown key
#: is therefore dead weight on the way in and pure risk on the way out --
#: the widget hands ``strip_secret_fields`` whatever a previously saved file
#: happened to contain, and ``aws_secret_access_key``, ``client_secret``,
#: ``private_key`` and ``token`` all reached disk verbatim because they were
#: not ``ClusterConfig`` fields and so were not in ``SECRET_FIELDS``.
#:
#: This is an allowlist, not another list of forbidden spellings: it is
#: derived from the dataclass, so it cannot fall behind it, and a key nobody
#: has thought of is excluded by default rather than included by default.
PERSISTABLE_KEYS = frozenset(
    {f.name for f in fields(ClusterConfig)} | CONFIG_FILE_METADATA_KEYS
)


def _is_opaque_mapping(field_type: object) -> bool:
    """Whether a declared field holds a mapping whose *keys* are not ours.

    A mapping field on ``ClusterConfig`` is a hole in every name-based
    classifier, because the names inside it are the user's rather than the
    dataclass's. ``Optional[...]`` and other unions are unwrapped.

    **What counts is the abstract interface, not ``dict``.** The first
    version of this asked ``issubclass(origin, dict)``, which is the same
    mistake in miniature that name-matching was: it enumerated one spelling
    of the thing rather than describing the thing. ``Dict[str, str]`` and a
    bare ``dict`` were caught; ``Mapping[str, str]``,
    ``MutableMapping[str, str]`` and ``Any`` were not, and a field annotated
    ``Optional[Mapping[str, str]]`` holding ``{"api_key": ...}`` reached
    disk verbatim -- exactly the failure this function exists to prevent.
    ``collections.abc.Mapping`` is the interface all of those spellings
    name, and ``dict`` is a subclass of it, so this is strictly wider.

    ``Any`` and ``object`` are opaque for a different reason: they do not
    constrain the value at all, so the value *may* be a mapping and nothing
    here can rule it out. So is an annotation left as a string -- what
    ``from __future__ import annotations`` does to every annotation in a
    module -- which cannot be inspected without resolving it. Both are
    withheld rather than guessed at, on the same fail-closed rule the rest
    of this module follows: an unclassifiable field is not a safe field.
    """
    candidates = [field_type, *get_args(field_type)]
    for candidate in candidates:
        if candidate is Any or candidate is object:
            return True
        if isinstance(candidate, str):
            # An unresolved (stringised) annotation. Resolving it here would
            # need the defining module's namespace; withholding the field is
            # the answer that cannot leak.
            return True
        origin = get_origin(candidate) or candidate
        if isinstance(origin, type) and issubclass(origin, collections_abc.Mapping):
            return True
    return False


#: Fields whose *values* are chosen by the user and therefore cannot be
#: classified at all -- withheld whole, with the loss announced;
#: ``include_secrets=True`` writes them.
#:
#: ``environment_variables`` is the case that made the rule: nothing
#: distinguishes ``OMP_NUM_THREADS=4`` from ``GITHUB_PAT=<a token>`` by name
#: or by shape, and the previous rule -- judge each entry by its key name --
#: let ``SSH_PASSPHRASE``, ``GITHUB_PAT``, ``DATABASE_URL`` (with the
#: password in the URL) and ``USE_PASSWORD`` through.
#:
#: **Derived, not listed.** It was a literal ``{"environment_variables"}``,
#: and the rule it stood for applies word for word to the other two mapping
#: fields, which were not in it: ``gpu_requirements={"api_key": ...}`` and
#: ``venv_info={"token": ...}`` reached disk verbatim, because
#: ``strip_secret_fields`` looks at top-level keys and does not descend.
#:
#: Recursing into them was the other candidate fix and is the wrong one: it
#: would classify nested keys by *name*, which is precisely the approach
#: that failed above and that issue #167 replaced with an allowlist. A
#: nested ``{"license_blob": <a token>}`` defeats recursion and does not
#: defeat this. Deriving the set from the field types instead means a
#: mapping field added later is withheld from the day it is added rather
#: than from the day somebody remembers it.
UNCLASSIFIABLE_FIELDS = frozenset(
    f.name for f in fields(ClusterConfig) if _is_opaque_mapping(f.type)
)


def strip_secret_fields(config_data: dict) -> dict:
    """Return only the keys of ``config_data`` that may be written to disk.

    One implementation of "what may reach disk", so that a second
    persistence path cannot quietly disagree with
    :meth:`ClusterConfig.save_to_file`. ``clustrix/profile_manager.py``
    used to serialise ``asdict(config)`` directly and therefore wrote
    passwords and API tokens in plaintext, in a file that
    :meth:`ClusterConfig.save_to_file` would have withheld them from.

    A key survives when all three hold:

    * it is a key the configuration file format defines
      (``PERSISTABLE_KEYS``) -- callers such as the notebook widget pass
      arbitrary dictionaries loaded from disk, and a key the format does
      not define cannot be read back but can certainly carry a credential;
    * it is not a declared credential field (``SECRET_FIELDS``);
    * it is not a field whose values the user chooses and clustrix
      therefore cannot classify (``UNCLASSIFIABLE_FIELDS``).
    """
    return {
        k: v
        for k, v in config_data.items()
        if k in PERSISTABLE_KEYS
        and k not in SECRET_FIELDS
        and k not in UNCLASSIFIABLE_FIELDS
    }


def config_document(config: "ClusterConfig", include_secrets: bool = False) -> dict:
    """The document ``config`` is written to disk as.

    One answer to "what does a configuration look like in a file", so that a
    second writer cannot quietly disagree with the first. Both halves of it
    are security properties and both were learned the hard way:

    * **What may be written.** :func:`strip_secret_fields` -- passwords,
      tokens and every mapping field whose values clustrix cannot classify
      are left out unless ``include_secrets``.
    * **Where it came from.** :data:`CONFIG_SOURCES_KEY`, and only when the
      source is an untrusted one. Route 12 was the notebook widget's Save
      copying a repository's configuration into ``~/.clustrix`` and the next
      process reading it back as the user's own; ``ClusterConfig.save_to_file``
      and ``ProfileManager.export_profile`` write the same file to a path the
      caller names, which is a weaker precondition for the same laundering.
      What a save writes IS a credential decision one restart later, so the
      record belongs to the write path rather than to any one caller.

    A *trusted* source is deliberately not recorded. It would be re-derived
    identically from the file's own location, and a record that could raise
    trust is exactly the laundering route the key exists to close -- see
    :func:`config_source_for_saved_entry`, which ignores one. Absence
    therefore keeps meaning "a human wrote this file", which is what keeps a
    hand-written ``~/.clustrix/config.yml`` trusted.
    """
    config_data = asdict(config)
    if not include_secrets:
        config_data = strip_secret_fields(config_data)
    source = get_config_source(config)
    if source in UNTRUSTED_CONFIG_SOURCES:
        config_data[CONFIG_SOURCES_KEY] = source
    return config_data


def write_text_securely(path: Path, text: str, *, append: bool = False) -> None:
    """Write ``text`` to ``path`` without ever exposing it to other users.

    ``path.write_text(...)`` followed by ``path.chmod(0o600)`` looks
    equivalent and is not: the file exists, with the credentials already in
    it, at ``0o666 & ~umask`` for the whole window between the two calls.
    With the default umask that is mode 0644 -- world readable -- and any
    other local process can win that race (issue #111).

    What this guarantees, exactly:

    * **Default** (``append=False``). The secret is written into a
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
    * **Append** (``append=True``). The content is appended, so an existing file
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


# ---------------------------------------------------------------------------
# Where a configuration came from
# ---------------------------------------------------------------------------
#
# ``cluster_host`` is the identity of the party a stored password is about to
# be handed to, so "who chose this hostname" is a security question and not
# bookkeeping. It has to be answerable because the search of the standard
# locations includes ``./clustrix.yml`` -- a file belonging to whatever
# directory the process happens to be run from. Cloning a repository that
# ships one is enough to choose the hostname, and until this existed the
# credential layer could not tell that apart from a hostname the user put in
# ``~/.clustrix/config.yml`` themselves.
#
# The provenance rides on the config object rather than on a module global
# because callers hold their own instances: ``ClusterExecutor(config)`` takes
# whatever it is given, and a global would answer for the singleton instead.
# It is a plain attribute, deliberately not a dataclass field: ``fields()``,
# ``asdict()``, ``__eq__`` and therefore ``PERSISTABLE_KEYS`` and
# ``save_to_file`` are all unchanged by it, so nothing persists it and nothing
# can set it from a file.

#: Set in Python -- ``ClusterConfig(...)`` or ``configure(cluster_host=...)``.
CONFIG_SOURCE_RUNTIME = "runtime"

#: ``load_config(path)``: the caller named the file, so the caller chose it.
CONFIG_SOURCE_EXPLICIT_FILE = "explicit-file"

#: Found in the clustrix configuration directory (``~/.clustrix``, or
#: ``CLUSTRIX_CONFIG_DIR``). Writing a file there is a deliberate act.
CONFIG_SOURCE_USER_CONFIG_DIR = "user-config-dir"

#: Found as ``./clustrix.{yml,yaml,json}``. **Not trusted.** Nobody chose
#: this file by being in the directory; ``git clone && cd`` is enough.
CONFIG_SOURCE_WORKING_DIRECTORY = "working-directory"

#: Found in a configuration directory named by ``CLUSTRIX_CONFIG_DIR``
#: rather than in the default ``~/.clustrix``. **Not trusted.** The whole
#: argument for trusting the configuration directory is that putting a file
#: in ``~/.clustrix`` is a deliberate act by the person whose home directory
#: it is. That argument does not survive the directory itself being named by
#: an environment variable: environment variables are ambient, inherited
#: state, and a repository-shipped ``.envrc``, ``Makefile`` or devcontainer
#: definition sets one for every process run inside the checkout. Redirected
#: to a directory it ships, a repository chooses ``cluster_host`` again --
#: the same defect as ``./clustrix.yml``, one level of indirection away, and
#: reproduced end to end (a password exported as ``SSH_PASSWORD`` in the
#: user's own shell reached a host of the repository's choosing).
#:
#: The redirect itself keeps working, because containers, CI images and
#: shared machines need it; what it no longer does is *vouch* for a hostname.
#: A user who genuinely keeps their configuration somewhere else authorises
#: the host where authorisation is not a round trip: ``SSH_HOST`` in the
#: credential file, which names the party that may receive the secret. Once
#: this source has named a hostname in a process, handing that hostname back
#: through ``configure`` or ``load_config`` does *not* clear it -- see
#: :data:`_HOSTS_NAMED_BY_UNTRUSTED_SOURCES` -- and the warning raised when a
#: redirected file is adopted says exactly that.
CONFIG_SOURCE_REDIRECTED_CONFIG_DIR = "redirected-config-dir"

#: A profile restored from a store that never recorded where its profiles
#: came from. **Not trusted**, and it is a statement of *ignorance* rather
#: than of provenance -- which is exactly why it is a source of its own
#: rather than being folded into ``redirected-config-dir``.
#:
#: Provenance began to be written into the profile store only once
#: ``profile_manager.PROFILE_SOURCES_KEY`` existed. Every store written
#: before that says nothing, and ``ProfileManager._persist()`` fires from
#: seven mutators -- one of them merely selecting a profile -- so a bundle a
#: repository shipped had already been copied into ``~/.clustrix`` and was
#: indistinguishable there from a profile the user built. Resolving that
#: silence to "wherever the file now sits" is what made the fix protect
#: nobody who was already affected: it re-derived ``user-config-dir``,
#: trusted, and released the credential.
#:
#: So silence fails closed, which is the same rule
#: :func:`get_config_source` applies to a config carrying no record at all.
#: Two things follow from it being *ignorance*:
#:
#: * it does not taint the hostname process-wide. ``set_config_source`` is
#:   passed ``record_host=False`` for it, for the reason that parameter
#:   exists -- the source was inferred, not read -- and a permanent record
#:   would leave a user whose only offence is an old store unable to use
#:   their own cluster from anywhere in the process, and unable to undo it.
#: * it is *replaceable*, unlike a recorded untrusted source. A store
#:   recording this value is read as silence again rather than as a verdict,
#:   so naming the store to ``ProfileManager.load_from_file`` -- the user
#:   saying "these profiles are mine" about a file they identified -- still
#:   resolves it, and the store then records a real answer.
#:
#: What it does not do is undo the laundering that already happened: a
#: profile a repository put in a pre-fix store can still be re-trusted by an
#: explicit ``configure()`` naming its host. Nothing in the file can tell us
#: it was not the user's, and inventing certainty either way would be worse
#: than saying so.
CONFIG_SOURCE_UNRECORDED_PROVENANCE = "unrecorded-provenance"

#: The sources that count as "the user configured this". Everything not
#: listed is untrusted, so a source nobody has thought of yet fails closed.
TRUSTED_CONFIG_SOURCES = frozenset(
    {
        CONFIG_SOURCE_RUNTIME,
        CONFIG_SOURCE_EXPLICIT_FILE,
        CONFIG_SOURCE_USER_CONFIG_DIR,
    }
)

#: The sources that do not. Named as a set of its own rather than left as
#: "whatever is not trusted", because :func:`set_config_source` has to
#: record *which* untrusted source named a hostname.
UNTRUSTED_CONFIG_SOURCES = frozenset(
    {
        CONFIG_SOURCE_WORKING_DIRECTORY,
        CONFIG_SOURCE_REDIRECTED_CONFIG_DIR,
        CONFIG_SOURCE_UNRECORDED_PROVENANCE,
    }
)

#: Every source there is. A new one has to be added here *and* decided about
#: above, so it cannot become trusted by being forgotten.
CONFIG_SOURCES = TRUSTED_CONFIG_SOURCES | UNTRUSTED_CONFIG_SOURCES


#: Top-level key in a saved configuration file recording the source the
#: configuration carried when it was written -- per configuration name in
#: the widget's multi-configuration shape, and as a bare string in the
#: flat single-configuration file ``ClusterConfig.save_to_file`` writes.
#:
#: Route 12. ``_on_save_config`` writes into :func:`get_config_dir`, and
#: :func:`detect_config_files` infers trust from exactly that directory. So
#: pressing Save on a configuration the widget had *found* in a cloned
#: repository copied it to ``~/.clustrix/config.yml``, and the next session's
#: widget re-derived the source from where the file now was --
#: ``user-config-dir``, trusted -- and released the credential. The in-memory
#: invariant held the whole time: every sidecar was still valid when Save
#: returned, and the laundering happened on disk, one restart later.
#:
#: The precondition is attacker-controlled, because the filename comes from
#: the configuration's own ``name``: ``""`` and ``Config`` both save as
#: ``config.yml`` and ``clustrix`` saves as ``clustrix.yml``, all three of
#: which :func:`detect_config_files` looks for. And a save writes *every*
#: configuration in the dropdown, including ones the user never selected and
#: never looked at, verbatim -- so a repository shipping a second entry rides
#: along with the one the user meant to keep.
#:
#: This is the same defect ``profile_manager.PROFILE_SOURCES_KEY`` closes for
#: the profile store, and it takes the same answer for the same reason:
#: persist the source rather than refuse to persist the configuration, so a
#: user who deliberately keeps a project-local configuration keeps it -- and
#: keeps the refusal that goes with it. Preserving the configuration and *why
#: it is refused* is the honest pair.
CONFIG_SOURCES_KEY = "config_sources"


def config_name_from_document(key: Any) -> str:
    """The configuration name a key in a parsed YAML/JSON mapping stands for.

    A configuration name is a *string* everywhere clustrix uses one: it is a
    dropdown option, it is sorted against other names, and it is the key the
    provenance record is looked up under. A YAML key is not always a string.
    YAML 1.1 resolves ``on:``, ``off:``, ``yes:`` and ``no:`` to booleans,
    ``null:`` to ``None`` and ``2:`` to an int, so a configuration file
    containing any of them handed the widget a ``self.configs`` whose keys
    could not be compared with each other -- ``sorted()`` raised
    ``TypeError: '<' not supported between instances of 'str' and 'bool'``
    and the widget failed at construction, or Save failed outright and the
    user lost the configuration they had just edited. A file that does this
    can be shipped by a cloned repository, so it is a denial of service with
    an attacker-controlled precondition, and it is fixed here -- at the one
    boundary where document keys become names -- rather than by teaching
    each ``sorted()`` call to tolerate mixed types.

    Coercing rather than dropping the entry is deliberate: dropping would
    silently lose a configuration the user can see in their own file, and
    ``str(True)`` at least says what YAML actually made of what they wrote.
    """
    return key if isinstance(key, str) else str(key)


def recorded_config_source(recorded: Any, name: str) -> Any:
    """The source ``name``'s entry claims, out of a whole file's record.

    The record is written by clustrix and read back from a file anybody may
    have edited, so its *shape* is untrusted too. A record that is not a
    mapping is not a record about ``name`` in particular; it is an
    unrecognised value, and every entry in the file inherits it so that
    :func:`config_source_for_saved_entry` can fail it closed. Returning
    ``None`` there would let a one-character edit -- ``config_sources: x`` --
    erase the record for every configuration in the file.
    """
    if recorded is None:
        return None
    if isinstance(recorded, dict):
        # Through the same coercion the configuration names themselves went
        # through, or a record written under ``on:`` would not be found under
        # the name ``"True"`` that the configuration ended up with -- and a
        # missing record reads as absence, which is *trusted*.
        return {
            config_name_from_document(key): value for key, value in recorded.items()
        }.get(name)
    return recorded


def config_source_for_saved_entry(file_source: str, recorded: Any) -> str:
    """The source a saved configuration gets: ``file_source``, or worse.

    A persisted source may only ever *downgrade*, exactly as in
    :func:`clustrix.profile_manager._restored_profile_source`. That asymmetry
    is the whole security property and it is what makes writing the source
    down safe at all: if a file could raise its own trust by saying so, this
    key would be the laundering route it exists to close.

    So:

    * an untrusted recorded source is believed, whatever the file's location
      says. A configuration that came out of a working directory stays from a
      working directory after Save copies it into ``~/.clustrix``.
    * a *trusted* recorded source is ignored and the file's own location
      stands, so a repository cannot promote a configuration it ships by
      writing ``explicit-file`` beside it.
    * anything clustrix does not recognise -- a truncated file, a hand-edited
      one, an attacker's invention -- is treated as a redirect rather than
      raised on. Refusing to load would cost the user every configuration in
      the file; refusing to *trust* costs one credential release.

    **Absence is trusted here, and that is the deliberate difference from the
    profile store.** A profile store is only ever written by clustrix, so
    silence there means a version that did not record and has to fail closed.
    A configuration file is a file *users write by hand* -- moving settings
    into ``~/.clustrix/config.yml`` is the remedy ``_load_default_config``'s
    own warning names -- so silence here means "a human put this here", which
    is the trusted case and must stay trusted. That is also what keeps an
    explicit adoption available: the widget records the source when *it*
    moves a configuration, and a user who moves one themselves records
    nothing and is believed.
    """
    if recorded is None:
        return file_source
    if not isinstance(recorded, str) or recorded not in CONFIG_SOURCES:
        return CONFIG_SOURCE_REDIRECTED_CONFIG_DIR
    if recorded in UNTRUSTED_CONFIG_SOURCES:
        return recorded
    return file_source


def normalize_hostname(hostname: object) -> str:
    """The comparable form of a hostname, or ``""`` if there isn't one.

    Case is not significant in DNS and a trailing dot only marks a name as
    already absolute, so ``HPC.Example.Edu.`` and ``hpc.example.edu`` are
    the same host and must compare equal. Anything that is not a non-empty
    string -- ``None``, a stray ``0``, whitespace -- normalises to ``""``,
    which every caller then refuses outright.

    Lives here rather than in ``auth_methods`` (which imported it from a
    private name) because the provenance record below compares hostnames
    too, and two normalisations would be two different answers to "is this
    the same host".
    """
    if not isinstance(hostname, str):
        return ""
    return hostname.strip().rstrip(".").lower()


#: Every hostname an untrusted source has named in this process, mapped to
#: the source that named it.
#:
#: **Why a value-keyed record and not just the per-object attribute.** The
#: attribute answers "where did *this object* come from", and every route
#: that builds a *new* ``ClusterConfig`` from an old one's field values
#: therefore resets it to ``runtime``, which is the trusted end of the
#: scale. Two such routes were live:
#:
#: * ``dataclasses.replace(cfg, ...)`` -- it calls ``cls(**fields)``, so
#:   ``__post_init__`` runs again on the copy and the copy is trusted.
#: * The notebook widget's Apply button -- ``configure(**asdict(cfg))``
#:   round-trips the config it auto-loaded from ``./clustrix.yml`` straight
#:   back through the function that means "the user typed this".
#:
#: Neither is exotic and the second needs no adversary at all. What both
#: have in common is that the *hostname is unchanged*: it is still the
#: string an untrusted file supplied, and passing it through a function call
#: is not evidence that anybody chose it. So the record is keyed by the
#: hostname rather than by object identity, and it survives ``replace``,
#: ``asdict`` round trips, copies, and any route nobody has thought of --
#: because none of them change the one thing that matters, which is who
#: gets the password.
#:
#: The cost is a false refusal: a user whose ``./clustrix.yml`` names the
#: same host they then type themselves is refused, because those two are
#: genuinely indistinguishable. That is a failure in the safe direction and
#: the refusal message names the fix.
#:
#: Append-only within a process, and there is deliberately no public way to
#: clear it -- a "forget that this was untrusted" API is just the laundering
#: route again with a friendlier name.
#:
#: **Which is why nothing may be written here on a guess.** "This
#: *construction* could not prove where it came from, so treat the object as
#: untrusted" is a cheap, reversible, per-object judgement. "This *hostname*
#: was named by a file, so refuse it process-wide forever" is a much stronger
#: claim, and because there is no way back from it, it may only follow from a
#: construction that is *actually inside* a declared file read. The
#: process-wide fallback in :func:`_source_being_read` is a guess -- it fires
#: for any construction anywhere in the process that happens to overlap an
#: unrelated untrusted read -- and it therefore reaches
#: :func:`set_config_source` with ``record_host=False``.
#:
#: Before that separation, twelve threads doing nothing but
#: ``ClusterConfig(cluster_host=...)`` while twelve others opened and closed
#: an unrelated working-directory read had 96,739 of 96,740 constructions
#: over-tainted, and -- far worse -- the hostname stayed refused after the
#: overlap ended, with no API able to clear it. A momentary benign overlap
#: permanently denying the documented workflow is how a security control gets
#: ripped out; a suspended generator reproduces it with no threads at all.
_HOSTS_NAMED_BY_UNTRUSTED_SOURCES: Dict[str, str] = {}


#: The source ``ClusterConfig.__post_init__`` stamps while a loader is
#: reading a file. ``runtime`` outside any loader, which is the truth there:
#: a bare ``ClusterConfig(...)`` is somebody's Python.
#:
#: A ``ContextVar`` rather than a module global so that two threads (or two
#: asyncio tasks) loading configuration at once cannot see each other's
#: declaration; each thread starts from its own empty context, and a
#: declaration leaking across would be a *trusted* config built while some
#: other thread happened to be reading an untrusted file, or the reverse.
#:
#: **Which direction is dangerous.** A fresh context is *safe* for a thread
#: that is not loading anything and *unsafe* for a loader that delegates the
#: construction out of its own block -- to a ``Thread``, a
#: ``ThreadPoolExecutor``, a ``ProcessPoolExecutor``. The delegate does not
#: inherit the declaration, so it reads the default, ``runtime``, which is
#: the *trusted* end of the scale: a file the loader distrusts would come
#: back marked as somebody's Python. It holds across everything that stays
#: inside one context -- nesting, exception unwind, a generator yielding
#: mid-block, ``await``, ``create_task``, ``fork``, and every rebuild route
#: (``copy``, ``deepcopy``, ``pickle``, ``replace``,
#: ``ClusterConfig(**asdict(...))``).
#:
#: :data:`_UNTRUSTED_LOADS_IN_FLIGHT` closes the thread half of that, and
#: ``tests/unit/test_a_cloned_repository_cannot_take_your_password.py``
#: fails if a shipped loader ever starts delegating across a *process*
#: boundary, which no mechanism inside one interpreter can follow.
_CONFIG_SOURCE_BEING_READ: contextvars.ContextVar[str] = contextvars.ContextVar(
    "clustrix_config_source_being_read", default=CONFIG_SOURCE_RUNTIME
)

#: Untrusted reads currently in progress *anywhere in this process*, by
#: source, with a count because two can overlap. Deliberately a module
#: global -- shared by every thread -- and deliberately consulted only when
#: the calling context declares nothing, so it does not cost the ContextVar
#: its precision. See :func:`_source_being_read`.
#:
#: "Anywhere in this process" has to mean every untrusted read there is, and
#: for a while it did not: the automatic search in :func:`_load_default_config`
#: reached the file through :func:`load_config`, whose own declaration is
#: ``explicit-file`` -- trusted, so nothing was recorded here -- and fixed the
#: provenance up afterwards with :func:`set_config_source`. The guard
#: therefore covered ``ProfileManager._restore`` and the widget's Load button
#: and gave *zero* cover to the working-directory and redirected searches,
#: which are the reads it was built for. That search now declares itself
#: around the whole read.
#:
#: Consulting it is a *guess* about a construction that declared nothing, so
#: what it may conclude is bounded: untrusted for that one object, never a
#: write to :data:`_HOSTS_NAMED_BY_UNTRUSTED_SOURCES`.
_UNTRUSTED_LOADS_IN_FLIGHT: Dict[str, int] = {}
_UNTRUSTED_LOADS_LOCK = threading.Lock()


def _source_being_read() -> str:
    """The source to stamp on a ``ClusterConfig`` being constructed now.

    The declaration of the calling context when there is one, and that is a
    fact: this construction is lexically (or dynamically) inside a loader's
    block, so its values came off a disk.

    When there is not -- which is the honest answer for a script constructing
    a config, and the *wrong* one for work a loader handed to another thread
    -- an untrusted read in flight elsewhere in the process wins, because the
    two cases are indistinguishable from here and only one of them is safe to
    guess at. Under-distrusting costs the cluster password.

    Both answers are about *this object* and nothing wider. Neither the
    declaration nor the fallback is grounds for writing the hostname into
    :data:`_HOSTS_NAMED_BY_UNTRUSTED_SOURCES`, which is why the caller passes
    ``record_host=False``: the declaration outlives its own frame when a
    generator suspends inside the block, and the fallback is a guess about
    every construction anywhere in the process. Only a loader holding the
    file it read may make the permanent claim.
    """
    declared = _CONFIG_SOURCE_BEING_READ.get()
    if declared != CONFIG_SOURCE_RUNTIME:
        return declared
    with _UNTRUSTED_LOADS_LOCK:
        if not _UNTRUSTED_LOADS_IN_FLIGHT:
            return CONFIG_SOURCE_RUNTIME
        # Sorted so the answer does not depend on dict insertion order when
        # two untrusted reads overlap. Both are untrusted, so which one is
        # named changes the message and not the decision.
        return sorted(_UNTRUSTED_LOADS_IN_FLIGHT)[0]


@contextlib.contextmanager
def config_built_from_file(source: str) -> Iterator[None]:
    """Every ``ClusterConfig`` built inside this block came from a file.

    **Why this exists.** ``__post_init__`` stamped ``runtime``
    unconditionally, and ``runtime`` is trusted. That is right for
    ``ClusterConfig(cluster_host=...)`` typed in a script and wrong for
    every ``ClusterConfig(**parsed_file_content)`` in the tree -- and the
    profile store is one of those. A repository shipping an ``.envrc`` that
    sets ``CLUSTRIX_CONFIG_DIR`` plus a ``profiles/profiles.yml`` under it
    needed no ``config.yml`` at all: nothing was tainted, no warning fired,
    ``ProfileManager`` handed back a config marked ``runtime``, and the
    victim's exported ``SSH_PASSWORD`` reached the repository's host. Same
    class as the original defect, through a door the fix did not watch.

    So the rule is about the *content*, not about the loader: a config built
    from bytes that were on a disk is not a config the user typed, whichever
    function did the reading. A loader declares which kind of file it is
    reading and everything constructed inside the block is stamped with it,
    including objects built by code the loader calls.

    ``source`` must be one of :data:`CONFIG_SOURCES`; an unknown one raises
    rather than being recorded, so a typo cannot invent a source that is
    neither trusted nor untrusted.
    """
    if source not in CONFIG_SOURCES:
        raise ValueError(
            f"Unknown configuration source: {source!r}. "
            f"Known sources are {sorted(CONFIG_SOURCES)}."
        )
    token = _CONFIG_SOURCE_BEING_READ.set(source)
    # Recorded process-wide as well, so that a construction this loader
    # delegates to another thread -- which starts from an empty context and
    # would otherwise read the trusted default -- still comes out untrusted.
    untrusted = source in UNTRUSTED_CONFIG_SOURCES
    if untrusted:
        with _UNTRUSTED_LOADS_LOCK:
            _UNTRUSTED_LOADS_IN_FLIGHT[source] = (
                _UNTRUSTED_LOADS_IN_FLIGHT.get(source, 0) + 1
            )
    try:
        yield
    finally:
        if untrusted:
            with _UNTRUSTED_LOADS_LOCK:
                remaining = _UNTRUSTED_LOADS_IN_FLIGHT.get(source, 0) - 1
                if remaining > 0:
                    _UNTRUSTED_LOADS_IN_FLIGHT[source] = remaining
                else:
                    _UNTRUSTED_LOADS_IN_FLIGHT.pop(source, None)
        _CONFIG_SOURCE_BEING_READ.reset(token)


def set_config_source(
    config: ClusterConfig, source: str, *, record_host: bool = True
) -> None:
    """Record where ``config`` was read from.

    An untrusted source additionally taints the hostname it named, for the
    reasons set out on :data:`_HOSTS_NAMED_BY_UNTRUSTED_SOURCES` -- unless
    ``record_host`` is False, which means the caller inferred the source
    rather than being told it. Such a caller may mark *this object*
    untrusted, because that is a judgement about one construction and costs
    one refusal; it may not write the hostname into a record that has no way
    back, because that is a claim about every future use of the name. Every
    loader in the tree knows which file it opened and so leaves the default
    alone; the only caller that passes False is ``__post_init__``, whose
    source is inferred rather than known.

    **A ``record_host=False`` mark does not survive ``dataclasses.replace``,
    and that is the point rather than a hole.** ``replace`` rebuilds the
    object, ``__post_init__`` runs again, and with no untrusted read in
    flight the second time it infers ``runtime``. Making the mark survive
    would mean writing the *hostname* down permanently on the strength of a
    guess -- the claim this parameter exists to refuse, and the one measured
    over-tainting 96,739 of 96,740 constructions with no way back.

    What makes that safe is that the guess never fires on the attacker's own
    config. Every loader in the tree calls this function *itself*, with
    ``record_host`` left True, so a config actually built from a file has its
    hostname in :data:`_HOSTS_NAMED_BY_UNTRUSTED_SOURCES` and stays refused
    through ``replace``, ``asdict`` round trips and any rebuild --
    ``tests/unit/test_a_cloned_repository_cannot_take_your_password.py``
    asserts exactly that. The guess covers *other* configs, constructed
    elsewhere in the process while that read happens to be open, and for
    those the rebuild's answer is the accurate one.
    """
    if source not in CONFIG_SOURCES:
        raise ValueError(
            f"Unknown configuration source: {source!r}. "
            f"Known sources are {sorted(CONFIG_SOURCES)}."
        )
    # ``setattr`` rather than an attribute assignment, and no annotation on
    # the class, because either would make this a *declared* attribute --
    # and an annotated one in a dataclass body is a field, which is exactly
    # what it must never be: a field is persisted, so a hostile config file
    # could declare itself trusted. It has no class-level default either, so
    # an object that lost the attribute reads as untrusted rather than
    # falling back to a trusted class value. See ``get_config_source``.
    setattr(config, "_clustrix_config_source", source)
    if record_host:
        record_discovered_hostname(getattr(config, "cluster_host", None), source)


def record_discovered_hostname(hostname: object, source: str) -> None:
    """Record that a file clustrix *found* -- rather than a person -- named this host.

    The single public name for the claim, so that code which reads a
    hostname out of file content without building a ``ClusterConfig`` can
    make it too. The ``%%clusterfy`` widget is exactly that: it carries raw
    dicts from the files it globbed, which is why route 5 tainted nothing.

    A trusted source records nothing -- the record exists to describe hosts
    nobody chose. There is deliberately no way to *un*-record: "forget that
    this was untrusted" is the laundering route with a friendlier name.
    Only a caller holding the file it read may call this; a caller that
    merely *inferred* a source passes ``record_host=False`` to
    :func:`set_config_source` and gets to mark one object, not one name
    forever.
    """
    if source not in CONFIG_SOURCES:
        raise ValueError(
            f"Unknown configuration source: {source!r}. "
            f"Known sources are {sorted(CONFIG_SOURCES)}."
        )
    if source not in UNTRUSTED_CONFIG_SOURCES:
        return
    host = normalize_hostname(hostname)
    if host:
        _HOSTS_NAMED_BY_UNTRUSTED_SOURCES.setdefault(host, source)


def source_that_named_hostname(hostname: object) -> Optional[str]:
    """The untrusted source that named ``hostname`` in this process, if any.

    The read side of :data:`_HOSTS_NAMED_BY_UNTRUSTED_SOURCES`, and the only
    fact about a hostname's provenance that no caller can supply: it is
    written by the loaders, keyed by the name rather than by any object, and
    there is no way to clear it. :func:`get_config_source` consults it for
    ``config.cluster_host``; a connection to some *other* host -- which the
    auth chain drives routinely -- needs to ask about that host instead, and
    this is how.

    ``None`` means "nothing in this process recorded that a file named it",
    which is not the same as "somebody chose it" and must never be read as
    trust on its own.
    """
    return _HOSTS_NAMED_BY_UNTRUSTED_SOURCES.get(normalize_hostname(hostname))


def get_config_source(config: object) -> str:
    """Where ``config``'s ``cluster_host`` came from.

    Typed ``object`` rather than ``ClusterConfig`` because the whole point
    of the fallback below is objects that are not well-formed ones: a
    config restored by ``pickle``, one whose attribute was overwritten, a
    mapping the notebook widget carries. Promising a ``ClusterConfig`` here
    would make the annotation disagree with the docstring, and callers
    would have to cast to ask the question this exists to answer.

    Falls back to the *untrusted* answer for an object that somehow has no
    record -- one restored by ``pickle``, say, which does not run
    ``__post_init__``. An absent value must never read as "trusted", which
    is the same rule ``hostname_matches`` applies to an absent hostname.

    A hostname an untrusted source named earlier in this process keeps that
    source no matter what the object's own attribute says, so a config
    rebuilt from one -- by ``dataclasses.replace``, by
    ``configure(**asdict(cfg))`` -- reports where the *hostname* came from
    rather than where the object did. That is the question the credential
    layer is asking.
    """
    recorded = getattr(
        config, "_clustrix_config_source", CONFIG_SOURCE_WORKING_DIRECTORY
    )
    if recorded in TRUSTED_CONFIG_SOURCES:
        laundered = _HOSTS_NAMED_BY_UNTRUSTED_SOURCES.get(
            normalize_hostname(getattr(config, "cluster_host", None))
        )
        if laundered:
            return laundered
    return recorded


def config_source_is_trusted(config: object) -> bool:
    """Whether ``config`` came from somewhere the user chose.

    ``object``, for the reason :func:`get_config_source` is: an object that
    never ran ``__post_init__`` is exactly the input this has to be able to
    judge.

    A ``cluster_host`` that is truthy but does not normalise is refused
    whatever its recorded source says. ``__post_init__`` rejects such a
    value outright, so this is the second lock rather than the first: an
    object that never ran it -- one restored by ``pickle``, one whose field
    was overwritten by ``setattr`` -- must not be trusted on the strength of
    a hostname the record could not key on and no credential check could
    compare against. Unrecordable is exactly the state the taint map cannot
    describe, and "the map has nothing on it" may not read as "it is fine".
    """
    host = getattr(config, "cluster_host", None)
    if host and not normalize_hostname(host):
        return False
    return get_config_source(config) in TRUSTED_CONFIG_SOURCES


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

    Runs under ``_DEFAULT_CONFIG_LOCK`` for the same reason
    :func:`load_config` does, and the lock has to cover the ``setattr`` loop
    at the bottom, not just the search above it. The loop reads the module
    global ``_config`` on every iteration, and :func:`load_config` *rebinds*
    it. With the loop unlocked, a ``load_config`` landing in the middle of it
    left the keywords already applied written to the object that was just
    discarded and the rest written to the new one -- a torn write in which
    neither writer won, reported as success. Measured:
    ``configure(cluster_host=..., username=..., cluster_port=...,
    remote_work_dir=...)`` returned with ``cluster_host`` silently reverted to
    the file's value and the other three applied.

    So each of the two writers is now atomic with respect to the other, and
    the loser loses whole: whichever acquires the lock second sees a
    consistent configuration and writes a consistent one. What this does
    *not* do is make ``configure`` beat a ``load_config`` that acquires the
    lock after it -- an explicit file load replaces the configuration
    wholesale, by design, and that includes replacing keywords set before it.
    The documented precedence (defaults -> file -> runtime keywords) is about
    the layering within one sequence of calls, not about which of two
    concurrent writers wins; the only guarantee across threads is that
    neither call is observed half-applied.

    Args:
        **kwargs: Configuration parameters matching ClusterConfig fields
    """
    global _config  # noqa: F824

    with _DEFAULT_CONFIG_LOCK:
        # The file is the layer underneath these keywords (defaults -> file
        # -> runtime), so it has to be in place before they are applied on
        # top -- otherwise a later get_config() would run the search and
        # overwrite them. Re-entrant: this is the same lock, and it is an
        # RLock.
        _ensure_default_config_loaded()

        # Validate everything before applying anything: a rejected keyword
        # used to leave the earlier ones already written to the live config,
        # so a failed configure() call still changed the process's behaviour.
        for key in kwargs:
            if hasattr(_config, key):
                continue
            removed = _removed_setting_reason(key)
            if removed:
                raise ValueError(removed)
            raise ValueError(f"Unknown configuration parameter: {key}")
        # Validate everything before applying anything: a rejected keyword used
        # to leave the earlier ones already written to the live config, so a
        # failed configure() call still changed the process's behaviour.
        #
        # Against the declared *fields*, not ``hasattr(_config, key)``. Every
        # attribute a ClusterConfig happens to carry answered True to that,
        # including the provenance record itself: ``configure(
        # _clustrix_config_source="runtime")`` was accepted and marked the
        # config trusted, which is the one thing a caller must never be able to
        # assert about itself. ``load_config`` and ``ClusterConfig(**yaml)``
        # already reject every spelling of it; this was the last way in.
        # DECLARED_FIELD_NAMES is the existing derivation of "what a field is" --
        # a fourth spelling of ``{f.name for f in fields(ClusterConfig)}`` is how
        # these drift apart.
        for key in kwargs:
            if key in DECLARED_FIELD_NAMES:
                continue
            removed = _removed_setting_reason(key)
            if removed:
                raise ValueError(removed)
            if key.startswith("_"):
                # The name the user sees here is one they never typed: it is an
                # internal attribute that ``configure(**config.__dict__)`` swept
                # up. ``__dict__`` on a dataclass instance is every attribute the
                # object carries, fields and internals alike; ``asdict()`` is the
                # declared fields and nothing else, which is what a caller
                # round-tripping a config actually means. Refusing without saying
                # so sends them looking for a setting that does not exist.
                raise ValueError(
                    f"Unknown configuration parameter: {key} -- this is an "
                    f"internal attribute, not a setting, so this call is "
                    f"probably configure(**config.__dict__). Pass "
                    f"configure(**dataclasses.asdict(config)) instead: asdict() "
                    f"yields the declared fields and nothing else."
                )
            raise ValueError(f"Unknown configuration parameter: {key}")

        if "cluster_host" in kwargs:
            # Same rule as ``__post_init__``, which ``setattr`` below does not
            # re-run: a host the one normaliser cannot make sense of can neither
            # be recorded as tainted nor compared against a credential, so it may
            # not be written to the live config at all.
            host = kwargs["cluster_host"]
            if host and not normalize_hostname(host):
                raise ValueError(
                    f"cluster_host={host!r} is not a usable hostname. It must be "
                    f"a non-empty string."
                )

        if "cluster_type" in kwargs:
            # setattr below does not re-run __post_init__, so without this a
            # removed backend reaches the executor and fails there instead --
            # after connect(), i.e. after an SSH round trip to a host that was
            # never going to be used.
            validate_cluster_type(kwargs["cluster_type"])

        if "conda_env_name" in kwargs:
            # Same reason, and the same setattr: without this the refusal
            # happens at submission, with the job directory already created
            # on the cluster and the pickle already uploaded.
            validate_conda_env_name(kwargs["conda_env_name"])

        # Bound once. Even under the lock, re-reading the global on every
        # iteration would make this loop depend on _config not being rebound
        # mid-loop, which is the property the lock exists to provide rather
        # than one to lean on twice.
        target = _config
        for key, value in kwargs.items():
            setattr(target, key, value)

        if "cluster_host" in kwargs:
            # An explicit configure() call is the user's own Python, so it
            # replaces whatever a file had said -- including a ./clustrix.yml
            # that had been picked up from the working directory.
            #
            # It replaces it for *this object*. Whether the resulting host is
            # then trusted is get_config_source's answer, not this one: a
            # hostname an untrusted file already named in this process stays
            # untrusted however many times it is handed back through here.
            # See _HOSTS_NAMED_BY_UNTRUSTED_SOURCES.
            set_config_source(_config, CONFIG_SOURCE_RUNTIME)


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

    if "cluster_host" in kwargs:
        # An explicit configure() call is the user's own Python, so it
        # replaces whatever a file had said -- including a ./clustrix.yml
        # that had been picked up from the working directory.
        #
        # It replaces it for *this object*. Whether the resulting host is
        # then trusted is get_config_source's answer, not this one: a
        # hostname an untrusted file already named in this process stays
        # untrusted however many times it is handed back through here. See
        # _HOSTS_NAMED_BY_UNTRUSTED_SOURCES.
        set_config_source(_config, CONFIG_SOURCE_RUNTIME)


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
    the last caller to *acquire the lock*, not the last to finish; a slow
    large file cannot land on top of a small one loaded after it. Note the
    correction: "the last caller to enter" was the wording here, and it was
    false against a :func:`configure` competitor, which took no lock at all
    and so could not queue behind anything. It does now.

    ``explicit-file``: the caller named the path, and naming a path is the
    choice the automatic search does not have. A provenance record in the
    file itself can only lower that, never raise it -- see
    :func:`config_source_for_saved_entry`.

    Args:
        config_path: Path to configuration file
    """
    with _DEFAULT_CONFIG_LOCK:
        _load_config_locked(config_path)


def _load_config_locked(
    config_path: str, file_source: str = CONFIG_SOURCE_EXPLICIT_FILE
) -> str:
    """Body of :func:`load_config`; callers must hold ``_DEFAULT_CONFIG_LOCK``.

    The search of the standard locations comes through here too, naming the
    source its candidate's location carries -- one locked body for both
    doors, which is what makes an explicit load queue behind an in-flight
    search instead of racing it.
    """
    return _load_config_file(config_path, file_source)


def _load_config_file(config_path: str, file_source: str) -> str:
    """Read ``config_path`` into the live config and return its source.

    One reader for both doors, because they must agree about the provenance
    record: :func:`load_config`, where the caller named the path, and
    :func:`_load_default_config`, where clustrix found the file by searching
    the standard locations. ``file_source`` is what the *location* says; the
    return value is what the file gets after its own record has been allowed
    to downgrade it. Callers must hold ``_DEFAULT_CONFIG_LOCK``.

    Unknown settings, a removed backend name and an unusable
    ``conda_env_name`` are refused by :meth:`ClusterConfig.from_file_content`
    (and by ``__post_init__`` under it), naming this file either way.
    """
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

    # Where the file is, unless the file itself records somewhere worse.
    # ``from_file_content`` performs the same computation before it stamps
    # the object; this copy decides what the *search* is told so that an
    # adopted working-directory file is announced as what it really is.
    effective = config_source_for_saved_entry(
        file_source, config_data.get(CONFIG_SOURCES_KEY)
    )
    _config = ClusterConfig.from_file_content(
        config_data, effective, origin=str(config_path)
    )
    # An explicit load replaces the configuration wholesale, so the search of
    # the standard locations has nothing left to contribute. Marking it done
    # stops a later get_config() from discarding what was just loaded.
    _default_config_loaded = True
    return effective


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


def default_config_dir() -> Path:
    """``~/.clustrix`` -- the location no environment variable chose."""
    return Path.home() / ".clustrix"


def config_source_for_discovered_path(path: Any) -> str:
    """The provenance of a configuration file clustrix *found* on its own.

    For files nobody named: the automatic search of the standard locations,
    and the profile store, which ``ProfileManager`` reloads by itself at
    construction. A path a caller passed in is ``explicit-file`` instead --
    naming it is the choice -- so this is not the classifier for
    :func:`load_config`.

    Inside ``~/.clustrix`` is ``user-config-dir``, because putting a file
    there is a deliberate act by the person whose home directory it is.
    **Everywhere else is** ``redirected-config-dir``, untrusted, and that
    includes a directory a ``ProfileManager(config_dir=...)`` caller chose:
    over-distrusting a directory costs a refusal the message explains, while
    under-distrusting one costs the password.

    Compared after resolving symlinks on both sides, so a ``~/.clustrix``
    that is itself a symlink -- or a ``CLUSTRIX_CONFIG_DIR`` pointing at one
    -- is the *same* directory as the thing it points at rather than a
    redirect. Redirecting it that way needs write access to the home
    directory, at which point provenance is not the problem. ``..``, ``//``
    and a trailing slash resolve away for the same reason.
    """
    return (
        CONFIG_SOURCE_USER_CONFIG_DIR
        if _default_config_dir_relation(path) in ("same", "inside")
        else CONFIG_SOURCE_REDIRECTED_CONFIG_DIR
    )


def _default_config_dir_relation(path: Any) -> str:
    """``"same"``, ``"inside"`` or ``"outside"`` ``~/.clustrix``.

    The single symlink-resolving comparison behind both
    :func:`config_source_for_discovered_path` and
    :func:`config_dir_is_default`; a second spelling of "is this the default
    directory" would be a second answer to it.
    """
    try:
        default = Path(os.path.realpath(default_config_dir()))
        resolved = Path(os.path.realpath(path))
    except (OSError, RuntimeError, TypeError, ValueError):
        # Path.home() raises when there is no home directory to compare
        # against. Unable to establish that the path is the default one is
        # not the same as having established that it is.
        return "outside"
    if resolved == default:
        return "same"
    if default in resolved.parents:
        return "inside"
    return "outside"


def config_dir_is_default() -> bool:
    """Whether :func:`get_config_dir` is still ``~/.clustrix`` itself.

    ``CLUSTRIX_CONFIG_DIR`` set to the default path is not a redirect: it
    names the same directory, and containers and test harnesses set it that
    way routinely. A *subdirectory* of ``~/.clustrix`` is not the default
    directory, even though a file discovered inside one is still the user's
    own -- these are different questions and this is the narrower.
    """
    return _default_config_dir_relation(get_config_dir()) == "same"


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

    Call this each time you need the configuration rather than holding on to
    what it returns: ``load_config`` *rebinds* the singleton to a new
    ``ClusterConfig``, so a reference taken earlier keeps the values it had
    then and silently stops tracking the live configuration. (``configure``
    mutates in place, so a held reference does follow that one -- which is
    exactly what makes the difference easy to miss.)
    """
    _ensure_default_config_loaded()
    return _config


def _default_config_candidates() -> List[Tuple[Path, str]]:
    """The paths searched for a user configuration file, in priority order.

    Each candidate carries the source its *location* establishes; a record
    inside the file may still lower it -- see ``_load_config_file``. A
    ``~/.clustrix`` the process cannot read and a deleted working directory
    are reported rather than silently skipped: silently searching three of
    six locations is how a config file that is definitely there appears not
    to be.
    """
    candidates: List[Tuple[Path, str]] = []
    try:
        config_dir = get_config_dir()
    except RuntimeError as exc:
        # Path.home() raises when the home directory cannot be determined --
        # e.g. a Windows service account or a scrubbed environment with no
        # USERPROFILE. The working-directory candidates below are still
        # searched, but say so.
        logger.warning(
            "Could not determine a configuration directory (%s), so the "
            "per-user location was not searched. Set %s to point at it.",
            exc,
            CONFIG_DIR_ENV_VAR,
        )
        config_dir_source = None
    else:
        config_dir_source = (
            CONFIG_SOURCE_USER_CONFIG_DIR
            if config_dir_is_default()
            else CONFIG_SOURCE_REDIRECTED_CONFIG_DIR
        )
        candidates += [
            (config_dir / "config.yml", config_dir_source),
            (config_dir / "config.yaml", config_dir_source),
            (config_dir / "config.json", config_dir_source),
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
            (cwd / "clustrix.yml", CONFIG_SOURCE_WORKING_DIRECTORY),
            (cwd / "clustrix.yaml", CONFIG_SOURCE_WORKING_DIRECTORY),
            (cwd / "clustrix.json", CONFIG_SOURCE_WORKING_DIRECTORY),
        ]
    return candidates


def _read_config_bundle(path: Path) -> Optional[Tuple[int, List[str]]]:
    """The profile count and names if ``path`` is a widget profile bundle.

    The notebook widget's Save writes a *bundle* -- one mapping of profile
    name to a settings dict -- into the same standard locations this search
    reads flat configurations from. With strict loading, such a file raises
    ``ConfigFileError`` on its profile names, which meant pressing Save
    bricked the next ``import clustrix``'s first ``get_config()``. That is
    not an acceptable answer to a file this project itself wrote, so the
    bundle shape is detected deliberately and declined instead (#159, merge
    decision (a)).

    The shape test is strict in both directions. A file holding even one
    ``ClusterConfig`` field name is a flat configuration -- possibly with a
    typo'd key beside it, and raising that error is ``load_config``'s job,
    not this function's. And every value must be a mapping, so a flat file
    whose keys are *all* wrong (``cluster_hots: x``) is not waved past as a
    bundle either; it raises as the typo it is. A file that does not parse
    here is left for ``load_config`` to report properly.
    """
    # Regular files only: the shape test exists to peek before committing,
    # and peeking is allowed to be cheap. A FIFO or device object is not a
    # profile bundle by construction, and opening one here could block the
    # search thread indefinitely -- the loader below already has whatever
    # semantics the file type implies.
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            return None
    except OSError:
        return None
    try:
        with open(path) as handle:
            if path.suffix.lower() in (".yml", ".yaml"):
                parsed = yaml.safe_load(handle)
            else:
                parsed = json.load(handle)
    except Exception:
        return None
    if not isinstance(parsed, dict) or not parsed:
        return None
    field_names = {field.name for field in fields(ClusterConfig)}
    if any(key in field_names for key in parsed):
        return None
    if not all(isinstance(value, dict) for value in parsed.values()):
        return None
    return len(parsed), sorted(str(key) for key in parsed)


def _load_default_config() -> None:
    """Adopt the first configuration file found in the standard locations.

    Three things about a candidate are told apart here, because each used to
    present as "there is no configuration file":

    * A candidate that could not be *stat*ed. ``Path.exists()`` answers False
      for ENOENT but propagates EACCES -- an unreadable ``~/.clustrix`` made
      ``import clustrix`` raise from four frames inside a private function.
      "I could not look there" is a warning and the search moves on, because
      the remaining candidates can still produce a correct answer.

    * A candidate that was found and then failed to load -- truncated YAML, a
      typo'd setting name. The process would have run on built-in defaults
      while the user believed their file was in force, which is the expensive
      failure: a ``cluster_host`` that never took effect means the job ran
      somewhere other than where it was told to. That raises instead (#123).

    * A file holding the notebook widget's profile bundle rather than a flat
      configuration. Adopting one profile out of several would pick for the
      user and raising would brick the first ``get_config()`` for anyone who
      ever pressed Save, so the bundle shape is declined, named, and the
      search moves on (#159, merge decision (a)).

    The candidates are not equally trustworthy and are no longer treated as
    if they were. ``./clustrix.yml`` is there because of where the process
    happens to be running -- ``git clone`` followed by ``cd`` is all it takes
    for a repository to supply one -- so each carries its location's source,
    adopting one is announced, and the credential layer asks
    (:func:`config_source_is_trusted`) before handing a stored password to
    the ``cluster_host`` a file named.
    """
    for path, source in _default_config_candidates():
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
        bundle = _read_config_bundle(path)
        if bundle is not None:
            count, names = bundle
            logger.warning(
                "The clustrix configuration file %s holds %d named profile(s) "
                "(%s), not a flat configuration, so none of them was adopted; "
                "its settings are NOT in effect. Load one with %%clustrix "
                "config <name> in a notebook, or ProfileManager().get_profile"
                "(<name>) followed by clustrix.configure(...).",
                path,
                count,
                ", ".join(names),
            )
            continue
        try:
            # Declared around the whole read as well as inside it:
            # ``_load_config_file`` declares the source it computes for the
            # object it builds, and that inner declaration rightly wins for
            # that object; this outer one is what puts the read into
            # ``_UNTRUSTED_LOADS_IN_FLIGHT`` for its whole duration, without
            # which the thread guard gave the working-directory and
            # redirected searches -- the two reads it exists for -- no cover
            # at all.
            with config_built_from_file(source):
                effective = _load_config_locked(str(path), source)
        except Exception as exc:
            raise ConfigFileError(
                f"The clustrix configuration file {path} was found but could "
                f"not be loaded: {exc}. Its settings are NOT in effect. Fix "
                f"the file, move it aside, or load a different one with "
                f"clustrix.config.load_config(path)."
            ) from exc
        logger.debug("Loaded clustrix configuration from %s", path)
        if effective != source:
            warnings.warn(
                f"clustrix is treating the configuration file {path} as "
                f"{effective} rather than {source}, because the file "
                f"records having been copied there from somewhere nobody "
                f"chose. Stored credentials are NOT offered to a "
                f"cluster_host chosen this way. If these settings are "
                f"yours, remove the "
                f"'{CONFIG_SOURCES_KEY}' line from {path} -- a file you "
                f"wrote records nothing, and a file that records nothing "
                f"is yours -- and start a new process.",
                stacklevel=2,
            )
        elif source == CONFIG_SOURCE_WORKING_DIRECTORY:
            warnings.warn(
                f"clustrix adopted the configuration file {path} because "
                f"it is in the current working directory, not because "
                f"anyone asked for it. Stored credentials are NOT offered "
                f"to a cluster_host chosen this way, and that is settled "
                f"for the life of this process: naming the same host "
                f"again later does not undo it, because a value handed "
                f"back through a function call is not evidence that "
                f"anyone chose it. If this file is yours, either set "
                f"SSH_HOST in the credential file "
                f"({get_config_dir() / '.env'}) to the host that may "
                f"receive the secret, or move these settings into the "
                f"clustrix configuration directory (config.yml), remove "
                f"{path}, and start a new process.",
                stacklevel=2,
            )
        elif source == CONFIG_SOURCE_REDIRECTED_CONFIG_DIR:
            warnings.warn(
                f"clustrix adopted the configuration file {path} because "
                f"${CONFIG_DIR_ENV_VAR} points at {get_config_dir()}, not "
                f"because it is in your ~/.clustrix. An environment "
                f"variable is inherited from whatever started this "
                f"process, so stored credentials are NOT offered to a "
                f"cluster_host chosen this way, and that is settled for "
                f"the life of this process: naming the same host again "
                f"later does not undo it. If this file is yours, either "
                f"set SSH_HOST in the credential file to the host that "
                f"may receive the secret, or move these settings into "
                f"~/.clustrix/config.yml, unset ${CONFIG_DIR_ENV_VAR}, "
                f"and start a new process.",
                stacklevel=2,
            )
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
