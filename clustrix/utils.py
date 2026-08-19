import ast
import collections
import hashlib
import hmac
import logging
import contextlib
import os
import re
import shlex
import threading
import sys
import pickle
import inspect
import importlib
import json
import functools
import subprocess
from importlib import metadata as importlib_metadata
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
import dill  # type: ignore
import cloudpickle  # type: ignore

from .config import ClusterConfig

logger = logging.getLogger(__name__)


class PayloadAuthenticationError(RuntimeError):
    """A file fetched from a job directory did not verify against its key.

    Distinct from every other RuntimeError in the collection paths because it
    must never be swallowed by a broad ``except Exception``: the whole point
    of the check is that the caller is told, loudly, that something it was
    about to unpickle is not what the job wrote.
    """


def verify_signed_payload(
    payload: bytes, tag: Optional[str], key: Optional[str], what: str
) -> None:
    """Refuse ``payload`` unless its HMAC matches the per-job key.

    The one implementation behind every check in clustrix. ``result.pkl`` and
    ``error.pkl`` are both deserialized with dill, and dill.loads executes
    code, so both have to clear the same bar -- a job that merely *fails* must
    not be a cheaper way onto the submitting machine than a job that succeeds.

    Args:
        payload: The exact bytes that would be handed to the deserializer.
        tag: The hex digest that came back with them, if any.
        key: The per-job signing key recorded at submission, if any.
        what: Human-readable identification used in the error messages.

    Raises:
        PayloadAuthenticationError: if the key is missing, the tag is missing,
            or the tag does not match. Every one of those is a refusal: an
            unverifiable payload is indistinguishable from a forged one.
    """
    if not key:
        raise PayloadAuthenticationError(
            f"No result-signing key is recorded for {what}, so what it "
            "produced cannot be authenticated. Refusing to deserialize it: "
            "loading a pickle executes code. Re-run the job from this "
            "process, which records a key at submission."
        )

    tag = (tag or "").strip()
    if not tag:
        raise PayloadAuthenticationError(
            f"{what} produced a payload with no signature. Refusing to "
            "deserialize it: loading a pickle executes code, and an unsigned "
            "payload cannot be told apart from a file someone else wrote "
            "into the job directory."
        )

    expected = hmac.new(key.encode(), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(tag, expected):
        raise PayloadAuthenticationError(
            f"{what} payload failed its integrity check. Refusing to " "deserialize it."
        )


#: Characters a value may contain if it is going to be pasted into a generated
#: job script *without* quoting -- scheduler directives and ``module load``
#: lines, which stop meaning what they mean the moment quotes appear in them.
#: Anything outside this set is shell (or directive) syntax, so it is refused
#: rather than mangled.
_SHELL_SAFE_FRAGMENT = re.compile(r"^[A-Za-z0-9._:/=+,@%-]+$")

#: A POSIX shell variable name. ``export`` needs the name unquoted, so the
#: name itself can only be validated.
_ENV_VAR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_shell_fragment(config_key: str, value: Any) -> str:
    """Refuse a config value that cannot be safely pasted in unquoted.

    Used only where quoting would break the feature: a ``module load`` line,
    a ``#SBATCH``/``#PBS``/``#$`` directive body, the name in ``export
    NAME=...``. Everywhere else the value is quoted with ``shlex.quote``
    instead, which needs no allowlist.

    Args:
        config_key: The configuration field being checked, named in the error
            so the user knows which setting to fix.
        value: The value itself.

    Returns:
        The value as a string, when it is safe.

    Raises:
        ValueError: naming ``config_key`` and the offending value.
    """
    text = str(value)
    if not _SHELL_SAFE_FRAGMENT.match(text):
        raise ValueError(
            f"clustrix config {config_key}={text!r} cannot be used: it is "
            "written into a generated job script at a place that must stay "
            "unquoted (a scheduler directive or a module-load line), so a "
            "shell metacharacter there would run as a command. Allowed "
            "characters are letters, digits and . _ : / = + , @ % -"
        )
    return text


def validate_env_var_name(name: str) -> str:
    """Refuse an environment variable name that is not a shell identifier.

    ``export FOO=bar; touch /tmp/pwn=1`` is a valid dict key and an injection.
    The value beside it is quoted, but the name cannot be.
    """
    if not _ENV_VAR_NAME.match(str(name)):
        raise ValueError(
            f"clustrix config environment_variables has an invalid name "
            f"{name!r}. A shell variable name must start with a letter or "
            "underscore and contain only letters, digits and underscores."
        )
    return str(name)


def _evaluate_literal_range(range_expression: str):
    """Evaluate a ``range(...)`` expression, but only from literal arguments.

    The previous implementation called ``eval()`` on text sliced out of the
    user's source, with a comment admitting it was dangerous. It also could not
    tell "this range is range(0, 10)" from "I could not work this out", because
    failure fell through to a hardcoded ``range(10)``.

    Returns the range when every argument is a literal integer, and ``None``
    when the expression depends on anything only known at run time (a variable,
    ``len(data)``, an attribute). ``None`` means "do not parallelize", never
    "assume ten".
    """
    tree = ast.parse(range_expression, mode="eval")
    call = tree.body
    if not isinstance(call, ast.Call):
        return None
    if not isinstance(call.func, ast.Name) or call.func.id != "range":
        return None
    if call.keywords:
        return None

    bounds = []
    for argument in call.args:
        value = ast.literal_eval(argument)
        if not isinstance(value, int) or isinstance(value, bool):
            return None
        bounds.append(value)
    if not 1 <= len(bounds) <= 3:
        return None
    return range(*bounds)


def detect_loops(func: Callable, args: tuple, kwargs: dict) -> Optional[Dict[str, Any]]:
    """
    Analyze function to detect parallelizable loops.

    Args:
        func: Function to analyze
        args: Function arguments
        kwargs: Function keyword arguments

    Returns:
        Dictionary with loop information or None if no loops detected
    """

    try:
        source = inspect.getsource(func)
        tree = ast.parse(source)

        class LoopVisitor(ast.NodeVisitor):
            def __init__(self):
                self.loops = []

            def visit_For(self, node):
                # Analyze for loops
                if isinstance(node.target, ast.Name):
                    loop_info = {
                        "type": "for",
                        "variable": node.target.id,
                        "iterable": (
                            ast.unparse(node.iter)
                            if hasattr(ast, "unparse")
                            else "unknown"
                        ),
                    }
                    self.loops.append(loop_info)
                self.generic_visit(node)

            def visit_While(self, node):
                # Analyze while loops
                loop_info = {
                    "type": "while",
                    "condition": (
                        ast.unparse(node.test) if hasattr(ast, "unparse") else "unknown"
                    ),
                }
                self.loops.append(loop_info)
                self.generic_visit(node)

        visitor = LoopVisitor()
        visitor.visit(tree)

        if visitor.loops:
            # Return info about the first loop for now
            # In practice, you'd want more sophisticated analysis
            loop = visitor.loops[0]
            if loop["type"] == "for" and "range(" in loop["iterable"]:
                # The range has to be known exactly, because it decides how the
                # work is split. Guessing it silently changes the answer: the
                # previous fallback here substituted range(10), so a loop over
                # range(1000) whose bounds could not be read was chunked as ten
                # iterations and the caller got a tenth of the work back with no
                # error. If the range cannot be determined, refuse to
                # parallelize -- running the loop whole is always correct.
                range_str = loop["iterable"]
                start = range_str.find("range(")
                end = range_str.find(")", start)
                if start == -1 or end == -1:
                    return None
                range_part = range_str[start : end + 1]
                try:
                    range_obj = _evaluate_literal_range(range_part)
                except Exception:
                    logger.info(
                        "Not parallelizing this loop: its range %r could not be "
                        "evaluated without running the function.",
                        range_part,
                    )
                    return None
                if range_obj is None:
                    return None
                loop["range"] = range_obj

                return loop

        return None

    except Exception:
        # If analysis fails, assume no parallelizable loops
        return None


def make_portable_function(source: str, name: str) -> Callable:
    """Build a function that can be shipped to a worker without clustrix.

    dill pickles a function belonging to an importable module BY REFERENCE --
    a few dozen bytes that merely name the module -- so the worker has to
    import that module to load it. That is fine for the user's own code, which
    lives in ``__main__``, but it is fatal for helper functions clustrix ships
    itself: a bare container has no clustrix, and the job dies with
    ``ModuleNotFoundError: No module named 'clustrix'`` before it runs a line.

    Compiling the source into a fresh namespace gives the function
    ``__module__ = None`` and no reference to anything importable, so dill
    serializes it by value and the worker needs nothing installed.

    Args:
        source: Source of a single top-level function.
        name: The function's name within that source.

    Returns:
        The compiled function, safe to serialize for a bare worker.
    """
    namespace: Dict[str, Any] = {}
    exec(source, namespace)
    return namespace[name]


def _is_local_module(module: Any) -> bool:
    """True when `module` lives in the user's project rather than an install.

    Anything under the standard library or a site-packages directory is
    installed on the worker too, because the execution environment mirrors the
    local one. Anything else -- a sibling file, a package in the working tree --
    exists only on this machine and has to travel with the function.

    A PEP 420 namespace package has no ``__file__`` at all; its location is in
    ``__path__``. Reading only ``__file__`` classified every namespace package
    as installed, so ``nspkg`` was left out of the payload and its children
    were never even looked at -- the worker died on ``import nspkg``.
    """
    name = getattr(module, "__name__", "")
    path = getattr(module, "__file__", None)
    if not path:
        entries = list(getattr(module, "__path__", None) or [])
        path = entries[0] if entries else None
    # __main__ is already serialized by value. clustrix is the machinery
    # running the job, not part of the user's function; embedding a checkout of
    # it would bloat every payload for nothing.
    if (
        not path
        or name == "__main__"
        or name == "clustrix"
        or name.startswith("clustrix.")
    ):
        return False
    resolved = os.path.realpath(path)
    for root in _INSTALLED_ROOTS:
        if resolved.startswith(root):
            return False
    return True


def _installed_roots() -> tuple:
    """Directories whose contents are installed rather than project-local."""
    import site
    import sysconfig

    roots = set()
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        path = sysconfig.get_paths().get(key)
        if path:
            roots.add(os.path.realpath(path) + os.sep)
    try:
        for path in site.getsitepackages():
            roots.add(os.path.realpath(path) + os.sep)
    except AttributeError:  # virtualenvs without getsitepackages
        pass
    user_site = getattr(site, "getusersitepackages", None)
    if user_site:
        roots.add(os.path.realpath(user_site()) + os.sep)
    return tuple(sorted(roots))


_INSTALLED_ROOTS = _installed_roots()


#: Hard ceiling on the object graph walked when looking for project-local
#: modules. This is a memory backstop, not a work budget: the walk either
#: finishes or the submission is refused. A walk that stopped early and let
#: submission continue shipped a payload the worker could not load, announced
#: only by a log line the user never saw.
_MAX_WALK_NODES = 2_000_000

#: Values that can never carry a module reference, so never worth enqueueing.
_SCALAR_TYPES = (bool, int, float, complex, str, bytes, bytearray, type(None))

#: Builtin containers walked through rather than followed by type. Any other
#: container -- including a project-local subclass of one of these -- must have
#: its class embedded, or the worker cannot rebuild the instance.
_BUILTIN_CONTAINERS = (tuple, list, set, frozenset, dict)


class WalkTooLargeError(RuntimeError):
    """The argument graph was too large to check for project-local modules."""


def _is_scalar(value: Any) -> bool:
    return type(value) in _SCALAR_TYPES


def _attribute_values(current: Any) -> List[Any]:
    """Values held on an instance, through ``__dict__`` and through ``__slots__``.

    An object's attributes are the commonest way a project-local class reaches
    the payload -- a config or wrapper object holding a project-local instance.
    Following only ``type(current)`` missed every one of them, and the worker
    failed on ``import`` of a package the user never passed directly.
    """
    values: List[Any] = []
    instance_dict = getattr(current, "__dict__", None)
    if isinstance(instance_dict, dict):
        values.extend(instance_dict.values())
    for klass in type(current).__mro__:
        slots = klass.__dict__.get("__slots__")
        if isinstance(slots, str):
            slots = (slots,)
        for slot in slots or ():
            try:
                values.append(getattr(current, slot))
            except AttributeError:
                pass
    return values


def _walk_referenced_modules(obj: Any) -> Tuple[Dict[str, Any], Set[str]]:
    """Modules `obj` reaches, split into project-local and installed.

    A function that calls `mypkg.helpers.clean` serializes that call by
    *reference* -- dill and cloudpickle both store importable objects as
    "import mypkg.helpers; get clean" -- and the worker, which has no mypkg,
    fails with ModuleNotFoundError. Naming those modules lets cloudpickle embed
    them instead. Parent packages come along because `mypkg.helpers` cannot be
    rebuilt without `mypkg`.

    The installed half is returned too, because a payload can just as easily
    reach into a package that IS installed here but cannot be installed on the
    cluster (an editable checkout, a private VCS URL). That is refused at
    submit time rather than discovered on the worker.

    Raises:
        WalkTooLargeError: if the graph exceeds ``_MAX_WALK_NODES``. Truncating
            silently is what shipped unloadable payloads.
    """
    found: Dict[str, Any] = {}
    installed: Set[str] = set()
    seen: set = set()
    queue = [obj]
    budget = _MAX_WALK_NODES

    while queue:
        current = queue.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))

        budget -= 1
        if budget < 0:
            raise WalkTooLargeError(
                f"Gave up checking the arguments for project-local code after "
                f"{_MAX_WALK_NODES} objects. clustrix cannot tell whether this "
                "payload needs modules that do not exist on the cluster, and "
                "will not submit a job that may fail on import. Pass the bulk "
                "of this data through a file on shared storage instead of as "
                "an argument."
            )

        # Only functions carry a real __globals__ dict. Reading the attribute
        # off a class yields the member descriptor from `types.FunctionType`,
        # which is not a mapping.
        namespace = None
        if inspect.isfunction(current):
            namespace = current.__globals__
            # Names bound in an enclosing scope live in closure cells, not in
            # globals. `from mypkg.util import helper` inside a function makes
            # `helper` a cell, and a walk of globals alone never sees it.
            for cell in current.__closure__ or ():
                try:
                    queue.append(cell.cell_contents)
                except ValueError:
                    pass  # an empty cell in a recursive definition
        elif inspect.ismodule(current):
            namespace = vars(current)
        elif inspect.isclass(current):
            namespace = dict(vars(current))

        module_name = None
        if inspect.ismodule(current):
            module_name = getattr(current, "__name__", None)
        elif inspect.isfunction(current) or inspect.isclass(current):
            module_name = getattr(current, "__module__", None)
        elif type(current) not in _BUILTIN_CONTAINERS:
            # An argument is usually an instance, not a class. Its class is
            # what has to travel, so follow the type. `isinstance` was wrong
            # here: a project-local class subclassing dict, list or tuple is a
            # container AND needs embedding, and testing isinstance skipped it.
            # For a local NamedTuple that failure was silent -- the instance
            # arrived as a plain tuple-alike with none of its methods.
            module_name = getattr(type(current), "__module__", None)
            queue.append(type(current))
            queue.extend(v for v in _attribute_values(current) if not _is_scalar(v))
            if isinstance(current, functools.partial):
                # partial keeps its target in a slot of the C type, invisible
                # to both __dict__ and __mro__ __slots__.
                queue.append(current.func)
                queue.extend(v for v in current.args if not _is_scalar(v))
                queue.extend(
                    v for v in (current.keywords or {}).values() if not _is_scalar(v)
                )
            elif inspect.ismethod(current):
                queue.append(current.__func__)
                queue.append(current.__self__)

        if module_name:
            module = sys.modules.get(module_name)
            if module is not None:
                if _is_local_module(module):
                    # Register the whole chain: mypkg.helpers needs mypkg.
                    parts = module_name.split(".")
                    for depth in range(1, len(parts) + 1):
                        name = ".".join(parts[:depth])
                        parent = sys.modules.get(name)
                        if (
                            parent is not None
                            and name not in found
                            and _is_local_module(parent)
                        ):
                            found[name] = parent
                    if namespace is None:
                        namespace = vars(module)
                else:
                    installed.add(module_name.split(".")[0])

        # Arguments arrive wrapped in the args tuple and kwargs dict, so the
        # instances that matter are one or more containers deep. Scalars are
        # never enqueued: a million-element list of ints would otherwise cost a
        # million entries in `seen`, on every submission, for nothing.
        if isinstance(current, (tuple, list, set, frozenset)):
            queue.extend(v for v in current if not _is_scalar(v))
        elif isinstance(current, dict):
            queue.extend(v for v in current.keys() if not _is_scalar(v))
            queue.extend(v for v in current.values() if not _is_scalar(v))

        if namespace:
            for value in list(namespace.values()):
                if inspect.isfunction(value) or inspect.isclass(value):
                    queue.append(value)
                elif inspect.ismodule(value) and _is_local_module(value):
                    queue.append(value)

    return found, installed


def _referenced_local_modules(obj: Any) -> List[Any]:
    """Project-local modules `obj` reaches; see :func:`_walk_referenced_modules`."""
    return list(_walk_referenced_modules(obj)[0].values())


#: cloudpickle's by-value registry is process-global, so registering around a
#: dump is a read-modify-write on shared state. AsyncClusterExecutor serializes
#: on a ThreadPoolExecutor, where two unsynchronized threads both snapshot the
#: registry before either registers, and the first to finish unregisters the
#: module out from under the second -- whose payload then silently reverts to
#: by-reference and fails on the cluster. One lock, and a refcount so
#: overlapping users share a single registration.
_BY_VALUE_LOCK = threading.RLock()
_BY_VALUE_REFCOUNTS: Dict[str, int] = {}


@contextlib.contextmanager
def _pickled_by_value(modules: List[Any]):
    """Have cloudpickle embed `modules` instead of importing them remotely.

    Modules the caller registered themselves are left exactly as found.
    """
    if not hasattr(cloudpickle, "list_registry_pickle_by_value"):
        # Guaranteed by the cloudpickle>=2.0 requirement. If it is missing we
        # cannot tell our registrations from the user's, and unregistering
        # theirs would silently change how *their* code serializes.
        raise RuntimeError(
            "cloudpickle is too old to embed project-local modules safely; "
            "clustrix requires cloudpickle>=2.0."
        )

    held = []
    with _BY_VALUE_LOCK:
        preexisting = {
            getattr(m, "__name__", m)
            for m in cloudpickle.list_registry_pickle_by_value()
        }
        for module in modules:
            name = getattr(module, "__name__", None)
            if not name:
                continue
            if name in preexisting and name not in _BY_VALUE_REFCOUNTS:
                continue  # the user registered this one; not ours to touch
            if _BY_VALUE_REFCOUNTS.get(name):
                _BY_VALUE_REFCOUNTS[name] += 1
                held.append(name)
                continue
            cloudpickle.register_pickle_by_value(module)
            _BY_VALUE_REFCOUNTS[name] = 1
            held.append(name)
    try:
        yield
    finally:
        with _BY_VALUE_LOCK:
            for name in held:
                remaining = _BY_VALUE_REFCOUNTS.get(name, 0) - 1
                if remaining > 0:
                    _BY_VALUE_REFCOUNTS[name] = remaining
                    continue
                _BY_VALUE_REFCOUNTS.pop(name, None)
                module = sys.modules.get(name)
                if module is not None:
                    cloudpickle.unregister_pickle_by_value(module)


def _unpicklable_location(
    obj: Any, description: str, seen: Optional[Set[int]] = None, depth: int = 0
) -> Optional[str]:
    """Name the object that cloudpickle cannot serialize, and say where it is.

    The old message asserted that the offender was "held at module level" and
    told the user to move it inside a function. When the offender was in a
    CLOSURE the user had already done exactly that, and the advice was
    nonsense. Worse, the message named every project-local module in the
    payload -- including the caller's own driver module -- so one unpicklable
    module-level object made every ``@cluster`` call in the project look
    broken. This walks to the actual culprit instead.

    Returns:
        A description of where the offending object lives, or None if nothing
        narrower than `description` could be pinned down.
    """
    if depth > 12:
        return None
    if seen is None:
        seen = set()
    if id(obj) in seen:
        return None
    seen.add(id(obj))

    children: List[Tuple[str, Any]] = []
    if inspect.isfunction(obj):
        where = f"{obj.__name__}() in module {obj.__module__}"
        for name, cell in zip(obj.__code__.co_freevars, obj.__closure__ or ()):
            try:
                children.append(
                    (f"closure variable {name!r} of {where}", cell.cell_contents)
                )
            except ValueError:
                pass
        for name in obj.__code__.co_names:
            if name in obj.__globals__:
                children.append(
                    (
                        f"module-level name {name!r} used by {where}",
                        obj.__globals__[name],
                    )
                )
    elif inspect.ismodule(obj):
        for name, value in list(vars(obj).items()):
            children.append((f"module-level name {name!r} in {obj.__name__}", value))
    elif inspect.isclass(obj):
        for name, value in list(vars(obj).items()):
            children.append((f"class attribute {obj.__name__}.{name}", value))
    elif isinstance(obj, dict):
        for key, value in list(obj.items()):
            children.append((f"{description} -> key {key!r}", value))
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for index, value in enumerate(obj):
            children.append((f"{description} -> item {index}", value))
    else:
        instance_dict = getattr(obj, "__dict__", None)
        if isinstance(instance_dict, dict):
            for name, value in list(instance_dict.items()):
                children.append(
                    (
                        f"attribute {name!r} of a {type(obj).__name__} in {description}",
                        value,
                    )
                )

    for child_description, child in children:
        if _is_scalar(child) or inspect.ismodule(child):
            continue
        try:
            cloudpickle.dumps(child, protocol=4)
        except Exception:
            narrower = _unpicklable_location(child, child_description, seen, depth + 1)
            if narrower:
                return narrower
            kind = f"{type(child).__module__}.{type(child).__name__}"
            return f"the {child_description}, which holds a {kind}"
    return None


def _refuse_unreproducible_packages(installed_modules: Set[str]) -> None:
    """Refuse a payload that reaches into a package the cluster cannot install.

    An editable checkout or a private VCS install has a version, but pinning
    it would install some unrelated package of the same name -- or nothing at
    all. Shipping the job anyway means waiting for the scheduler only to get a
    ModuleNotFoundError, so say it here, naming the package.
    """
    if not installed_modules:
        return
    owners = unreproducible_module_owners()
    offenders = sorted(owners[name] for name in installed_modules if name in owners)
    if not offenders:
        return
    listed = "; ".join(offenders)
    raise RuntimeError(
        "This function uses package(s) that cannot be installed on the "
        f"cluster: {listed}. clustrix mirrors your environment with "
        "`pip install name==version`, which for these would install something "
        "other than what you are running. Publish the package, vendor the code "
        "into your project directory so clustrix can send it by value, or list "
        "it in `excluded_packages` if the remote job genuinely does not need it."
    )


def _dumps_by_value(obj: Any) -> bytes:
    """Serialize `obj` so a fresh interpreter can rebuild it without imports.

    Tries the richest serializer first and degrades. Every fallback still
    produces bytes; if none can, the exception propagates rather than shipping
    a payload that will fail remotely with an unrelated error.
    """
    # Modules from the user's own project do not exist on the worker, so
    # anything reaching into them must be embedded rather than imported.
    # A failure to walk is not swallowed: not knowing what a payload needs is
    # not the same as knowing it needs nothing.
    local_modules_map, installed_modules = _walk_referenced_modules(obj)
    local_modules = list(local_modules_map.values())
    _refuse_unreproducible_packages(installed_modules)
    if local_modules:
        # No silent degradation here. Falling back to a by-reference payload
        # would produce exactly the ModuleNotFoundError this branch exists to
        # prevent -- minutes later, on the cluster, naming a module the user
        # can plainly see on their own disk.
        try:
            with _pickled_by_value(local_modules):
                try:
                    return cloudpickle.dumps(obj, protocol=4)
                except Exception as exc:
                    where = _unpicklable_location(obj, "the submitted payload")
                    raise RuntimeError(
                        "Cannot serialize this job: "
                        + (
                            f"{where} cannot be pickled ({exc})."
                            if where
                            else f"something it reaches cannot be pickled ({exc})."
                        )
                        + " Locks, open files, sockets and database handles cannot "
                        "cross to a worker. Create it where it is used instead of "
                        "capturing it, or install the package on the cluster so "
                        "the worker imports it rather than receiving a copy."
                    ) from exc
        except RuntimeError:
            raise
        except Exception as exc:
            names = ", ".join(
                sorted(getattr(m, "__name__", "?") for m in local_modules)
            )
            raise RuntimeError(
                f"Cannot send your local module(s) [{names}] to the cluster: {exc}."
            ) from exc

    try:
        return dill.dumps(obj, protocol=4, recurse=True)
    except Exception:
        pass
    try:
        return dill.dumps(obj, protocol=4)
    except Exception:
        pass
    try:
        return cloudpickle.dumps(obj, protocol=4)
    except Exception:
        return pickle.dumps(obj, protocol=4)


def serialize_function(func: Callable, args: tuple, kwargs: dict) -> Dict[str, Any]:
    """
    Serialize function and all its dependencies.

    Args:
        func: Function to serialize
        args: Function arguments
        kwargs: Function keyword arguments

    Returns:
        Dictionary containing serialized function and metadata
    """

    # Get current environment info
    requirements = get_environment_requirements()

    # Try to get function source code for better cross-Python compatibility
    func_source = None
    try:
        func_source = inspect.getsource(func)
    except Exception:
        # Cannot get source code - this is common for dynamically defined functions
        pass

    # Serialize the function by VALUE, including everything it refers to.
    #
    # `dill.dumps(func)` alone captures closure cells but NOT `func.__globals__`,
    # so a function that calls a module-level helper or reads a module-level
    # constant serializes fine and then dies on the worker with
    # `NameError: name '_helper' is not defined`. `recurse=True` walks the
    # globals the body actually names and bundles them. It can fail on objects
    # that refuse deep traversal, so the plain form remains the fallback.
    func_bytes = _dumps_by_value(func)

    # Arguments need the same treatment: stdlib pickle stores a class by
    # qualified name, so passing an instance of a class defined in the caller's
    # __main__ fails on the worker with "Can't get attribute 'Point'".
    args_bytes = _dumps_by_value(args)
    kwargs_bytes = _dumps_by_value(kwargs)

    # Get function metadata
    func_info = {
        "name": func.__name__,
        "module": func.__module__,
        "file": inspect.getfile(func) if hasattr(func, "__file__") else None,
        "source": None,
    }

    try:
        func_info["source"] = inspect.getsource(func)
    except Exception:
        pass

    return {
        "function": func_bytes,
        "function_source": func_source,
        "args": args_bytes,
        "kwargs": kwargs_bytes,
        "requirements": requirements,
        "func_info": func_info,
        "python_version": sys.version,
        "working_directory": os.getcwd(),
    }


def deserialize_function(func_data: Union[bytes, Dict[str, Any]]) -> tuple:
    """
    Deserialize function data back to function, args, and kwargs.

    Args:
        func_data: Serialized function data (bytes or dict)

    Returns:
        Tuple of (function, args, kwargs)
    """
    if isinstance(func_data, bytes):
        # Simple pickle format
        return pickle.loads(func_data)
    elif isinstance(func_data, dict):
        # Dictionary format from serialize_function
        try:
            func = dill.loads(func_data["function"])
        except Exception:
            func = cloudpickle.loads(func_data["function"])

        # dill, to match _dumps_by_value -- args may carry classes defined in
        # the caller's __main__, which stdlib pickle can only store by name.
        args = dill.loads(func_data["args"])
        kwargs = dill.loads(func_data["kwargs"])

        return func, args, kwargs
    else:
        raise ValueError("Invalid function data format")


#: Distributions that are never mirrored onto the worker. clustrix is the
#: machinery that runs the job, not part of the user's environment: the worker
#: gets its serialization dependencies explicitly, and a checkout of clustrix
#: pinned to a local editable path would fail to install anyway.
_NEVER_REPLICATED = frozenset({"clustrix"})

#: Packages the worker cannot run without, whatever the local environment says.
_ESSENTIAL_PACKAGES = ("cloudpickle", "dill")


def _canonical_package_name(name: str) -> str:
    """PEP 503 normalised form, so ``zope.interface`` and ``zope-interface`` match."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _source_checkout_path(dist: Any) -> Optional[str]:
    """Where this distribution's source tree is, if it is only a checkout.

    ``setup.py develop`` and older editable installs leave a bare ``.egg-info``
    inside the project directory and nothing in site-packages, so there is no
    ``direct_url.json`` to give the game away. A ``.dist-info`` in an unusual
    prefix is a perfectly ordinary install and is NOT this; the tell is the
    ``PKG-INFO`` that only egg-info metadata carries, combined with a location
    outside every installed root.
    """
    try:
        if dist.read_text("PKG-INFO") is None:
            return None
        location = os.path.realpath(str(dist.locate_file("")))
    except Exception:  # pragma: no cover - metadata with no locatable path
        return None
    rooted = location.rstrip(os.sep) + os.sep
    if any(rooted.startswith(root) for root in _INSTALLED_ROOTS):
        return None
    return location


def _unreproducible_reason(
    direct_url: Optional[Dict[str, Any]], source_checkout: Optional[str]
) -> Optional[str]:
    """Why this distribution cannot be reinstalled on another host, or None.

    A ``name @ file:///...work`` line is NOT such a case: conda records the
    build directory it compiled from, but the built artifact went into
    site-packages like any other wheel and ``name==version`` reinstalls it.
    Dropping those -- a third of a conda environment -- is what made
    ``import astropy`` fail on the worker.

    The genuinely unreproducible ones are those whose content lives somewhere
    the cluster cannot reach: an editable install pointing at a working tree on
    this laptop, a VCS checkout that may need credentials, or a bare
    ``.egg-info`` sitting in a source tree. Their version exists, but pinning
    it would install some unrelated package of the same name off an index.
    """
    if direct_url:
        url = direct_url.get("url") or "an unrecorded location"
        vcs_info = direct_url.get("vcs_info")
        if isinstance(vcs_info, dict):
            return f"installed from a {vcs_info.get('vcs', 'VCS')} checkout of {url}"
        dir_info = direct_url.get("dir_info")
        if isinstance(dir_info, dict) and dir_info.get("editable"):
            return f"installed in editable mode from {url}"
    if source_checkout is not None:
        return f"only present as a source checkout at {source_checkout}"
    return None


#: Scanning installed metadata costs a few hundred milliseconds, and a single
#: submission asks for it several times (once for the requirement set, once per
#: serialized payload to check for uninstallable packages). Keyed on sys.path,
#: because sys.path is what decides which distributions are visible: any change
#: that could change the answer changes the key.
_DISTRIBUTION_CACHE: (
    "collections.OrderedDict[Tuple[str, ...], Dict[str, Dict[str, Any]]]"
) = collections.OrderedDict()
_DISTRIBUTION_CACHE_SIZE = 8


def _distribution_records() -> Dict[str, Dict[str, Any]]:
    """Every distribution importable from this interpreter, keyed canonically.

    Read straight from installed metadata rather than from a freeze
    subprocess. ``pip list --format=freeze`` and ``uv pip freeze`` disagree
    about the same environment -- uv renders every conda-built distribution as
    ``name @ file:///...`` and every editable as ``-e file:///...``, pip
    renders both as ``name==version`` -- so which command happened to be on
    PATH changed both the requirement set and the environment cache key for a
    machine whose environment had not changed at all. The metadata is the same
    for both, so this is the same answer every time.

    One name can be found twice -- a site-packages ``.dist-info`` for an
    editable install plus the ``.egg-info`` in the source tree it points at.
    The unreproducible reading of a name wins, so an editable install cannot
    be laundered into a plain pin by whichever copy is enumerated last.

    Returns:
        Canonical name -> ``{"name", "version", "reason", "dist"}``, where
        ``reason`` is None for anything a plain ``pip install name==version``
        recreates.
    """
    cache_key = tuple(sys.path)
    cached = _DISTRIBUTION_CACHE.get(cache_key)
    if cached is not None:
        return cached

    records: Dict[str, Dict[str, Any]] = {}
    for dist in importlib_metadata.distributions():
        try:
            name = dist.metadata["Name"]
            version = dist.version
        except Exception:  # pragma: no cover - a broken .dist-info on disk
            continue
        if not name or not version:
            continue
        direct_url: Optional[Dict[str, Any]] = None
        try:
            raw = dist.read_text("direct_url.json")
        except Exception:  # pragma: no cover - unreadable metadata file
            raw = None
        if raw:
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                direct_url = parsed
        record = {
            "name": name,
            "version": version,
            "reason": _unreproducible_reason(direct_url, _source_checkout_path(dist)),
            "dist": dist,
        }
        canonical = _canonical_package_name(name)
        previous = records.get(canonical)
        if previous is not None and previous["reason"] and not record["reason"]:
            continue
        records[canonical] = record

    _DISTRIBUTION_CACHE[cache_key] = records
    while len(_DISTRIBUTION_CACHE) > _DISTRIBUTION_CACHE_SIZE:
        _DISTRIBUTION_CACHE.popitem(last=False)
    return records


def get_environment_requirements() -> Dict[str, str]:
    """Get current Python environment requirements.

    Returns a name -> version map of everything installed locally, so the
    remote execution environment can be rebuilt to match. Distributions that
    cannot be reinstalled elsewhere -- editable installs of a local working
    tree, VCS checkouts -- are not pinned here, because pinning their version
    would install some unrelated package of the same name off an index. They
    are reported by :func:`get_unreproducible_requirements` instead, and
    refused loudly at submit time if the job actually needs one.
    """
    requirements: Dict[str, str] = {}
    for canonical, record in _distribution_records().items():
        if canonical in _NEVER_REPLICATED:
            continue
        if record["reason"]:
            continue
        requirements[record["name"]] = record["version"]

    for pkg in _ESSENTIAL_PACKAGES:
        if pkg not in requirements:
            try:
                mod = importlib.import_module(pkg)
            except ImportError:
                continue
            version = getattr(mod, "__version__", None)
            if version:
                requirements[pkg] = version

    return requirements


def get_unreproducible_requirements() -> Dict[str, str]:
    """Installed distributions that no ``pip install`` on the cluster can recreate.

    Returns:
        Distribution name -> plain-English reason, for editable and VCS
        installs. clustrix itself is omitted: the worker never installs it.
    """
    unreproducible: Dict[str, str] = {}
    for canonical, record in _distribution_records().items():
        if canonical in _NEVER_REPLICATED:
            continue
        if record["reason"]:
            unreproducible[record["name"]] = record["reason"]
    return unreproducible


def _distribution_import_names(dist: Any) -> List[str]:
    """Top-level module names a distribution provides."""
    names: Set[str] = set()
    try:
        text = dist.read_text("top_level.txt")
    except Exception:  # pragma: no cover - unreadable metadata file
        text = None
    if text:
        names.update(line.strip() for line in text.splitlines() if line.strip())
    if not names:
        try:
            files = dist.files or []
        except Exception:  # pragma: no cover - metadata without a file list
            files = []
        for entry in files:
            head = str(entry).replace("\\", "/").split("/")[0]
            if head.endswith(".py"):
                head = head[:-3]
            if head and not head.endswith((".dist-info", ".egg-info")):
                names.add(head)
    return sorted(n for n in names if n.isidentifier())


def unreproducible_module_owners() -> Dict[str, str]:
    """Import name -> reason, for modules whose distribution cannot be reinstalled.

    Used to refuse a submission that reaches into such a package rather than
    letting the worker die on ``import``. Only the handful of unreproducible
    distributions are indexed, so this stays cheap.
    """
    owners: Dict[str, str] = {}
    for canonical, record in _distribution_records().items():
        if canonical in _NEVER_REPLICATED:
            continue
        reason = record["reason"]
        if not reason:
            continue
        label = f"{record['name']} ({reason})"
        for import_name in _distribution_import_names(record["dist"]):
            owners[import_name] = label
    return owners


def get_environment_info() -> str:
    """Get current Python environment information as string (for compatibility)."""
    try:
        # Use pip list --format=freeze to capture all packages including conda-installed ones
        result = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--format=freeze"],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass

    return ""


def is_uv_available() -> bool:
    """Check if uv package manager is available."""
    try:
        result = subprocess.run(
            ["uv", "--version"], capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def is_conda_available() -> bool:
    """Check if conda package manager is available."""
    try:
        result = subprocess.run(
            ["conda", "--version"], capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def get_package_manager_command(config: ClusterConfig) -> str:
    """
    Get the appropriate package manager command based on configuration.

    Args:
        config: Cluster configuration

    Returns:
        Package manager command (pip, uv, or conda)
    """
    if config.package_manager == "uv":
        return "uv pip"
    elif config.package_manager == "conda":
        return "conda"
    elif config.package_manager == "auto":
        # Auto-detect: prefer uv if available, then conda, fallback to pip
        if is_uv_available():
            return "uv pip"
        elif is_conda_available():
            return "conda"
        else:
            return "pip"
    else:
        # Default to pip (handles "pip" and any unrecognized values)
        return "pip"


def setup_environment(
    work_dir: str, requirements: Dict[str, str], config: ClusterConfig
) -> str:
    """
    Setup Python environment on cluster.

    Args:
        work_dir: Working directory path
        requirements: Package requirements
        config: Cluster configuration

    Returns:
        Path to Python executable
    """

    if config.conda_env_name:
        # Use existing conda environment
        return f"conda run -n {config.conda_env_name} python"

    # Get package manager to determine environment type
    pkg_manager = get_package_manager_command(config)

    if pkg_manager == "conda":
        # Create conda environment
        env_name = f"clustrix_env_{hash(work_dir) % 10000}"
        env_path = f"{work_dir}/conda_envs/{env_name}"

        setup_commands = [
            f"mkdir -p {work_dir}/conda_envs",
            f"conda create -p {shlex.quote(env_path)} "
            f"python={validate_shell_fragment('python_executable', config.python_executable).replace('python', '3.11')} -y",
        ]

        # Install requirements with conda
        if requirements:
            # Create conda environment.yml file
            env_file = f"{work_dir}/environment.yml"
            env_content = f"""name: {env_name}
dependencies:
  - python={config.python_executable.replace('python', '3.11')}
  - pip
  - pip:"""
            for pkg, version in requirements.items():
                env_content += f"\n    - {pkg}=={version}"

            setup_commands.extend(
                [
                    f"echo {shlex.quote(env_content)} > {shlex.quote(env_file)}",
                    f"conda env update -p {shlex.quote(env_path)} -f {shlex.quote(env_file)}",
                ]
            )

        return f"conda run -p {shlex.quote(env_path)} python"

    else:
        # Create virtual environment (for pip/uv)
        venv_path = f"{work_dir}/venv"

        setup_commands = [
            f"python -m venv {shlex.quote(venv_path)}",
            f"source {venv_path}/bin/activate",
        ]

        # Install requirements
        if requirements:
            req_file = f"{work_dir}/requirements.txt"
            req_content = "\n".join(
                [f"{pkg}=={version}" for pkg, version in requirements.items()]
            )

            # This would need to be written to remote file
            setup_commands.extend(
                [
                    f"echo {shlex.quote(req_content)} > {shlex.quote(req_file)}",
                    f"{shlex.quote(venv_path)}/bin/{pkg_manager} install -r {shlex.quote(req_file)}",
                ]
            )

        return f"{venv_path}/bin/python"


# Bump when the recipe below changes what actually lands in an environment.
# The key hashes the *inputs*; without this, a policy change (as when VENV2
# stopped installing nine hardcoded packages and started mirroring the local
# environment) leaves every existing environment matching its old key, so the
# cache serves a stale environment and the new packages are never installed.
ENVIRONMENT_RECIPE_VERSION = "3"


def _environment_key(
    python_version: str,
    requirements: Dict[str, str],
    config: Optional[ClusterConfig] = None,
) -> str:
    """A short, stable name for an environment with these exact contents.

    Two calls asking for the same Python version, the same requirements and the
    same install policy get the same key, so the environment is built once and
    reused. Any difference produces a different key, so environments are never
    silently shared between jobs that need different packages.
    """
    import hashlib

    excluded = sorted(
        str(name).lower() for name in (getattr(config, "excluded_packages", None) or [])
    )
    extra = sorted(
        str(spec) for spec in (getattr(config, "cluster_packages", None) or [])
    )
    material = "|".join(
        [
            ENVIRONMENT_RECIPE_VERSION,
            python_version,
            ";".join(
                f"{name}=={version}" for name, version in sorted(requirements.items())
            ),
            f"replicate={bool(getattr(config, 'replicate_local_environment', True))}",
            "excluded=" + ",".join(excluded),
            "extra=" + ",".join(extra),
        ]
    )
    digest = hashlib.sha256(material.encode()).hexdigest()[:12]
    return f"py{python_version.replace('.', '')}_{digest}"


#: Dropped into a conda environment only after every setup command in it
#: succeeded. Presence of the environment NAME proves nothing: a run whose
#: package installs failed left a named but half-built environment behind, and
#: every later job with the same requirements "reused" it and skipped setup.
_ENV_READY_MARKER = ".clustrix_ready"


def _parse_conda_env_paths(listing: str) -> Dict[str, str]:
    """Map environment name -> prefix path from ``conda env list`` output."""
    paths: Dict[str, str] = {}
    for line in listing.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        prefix = fields[-1]
        if not prefix.startswith("/"):
            continue
        paths[fields[0]] = prefix
    return paths


def _conda_env_marker_path(prefix: str) -> str:
    """Where the readiness marker lives inside a conda environment prefix."""
    return f"{prefix.rstrip('/')}/{_ENV_READY_MARKER}"


def _conda_env_ready_commands(env_names: List[str]) -> List[str]:
    """Commands that stamp each environment as fully built.

    Appended last, so the ``&&`` chain only reaches them when every create and
    every install before them succeeded.
    """
    stamp = (
        "import os, sys; "
        f"open(os.path.join(sys.prefix, {_ENV_READY_MARKER!r}), 'w').close()"
    )
    return [f'conda run -n {name} python -c "{stamp}"' for name in env_names]


def _conda_envs_exist(ssh_client, conda_setup_prefix: str, *env_names: str) -> bool:
    """True when every named conda environment exists AND finished building."""
    prefix = f"{conda_setup_prefix} && " if conda_setup_prefix else ""
    try:
        stdin, stdout, stderr = ssh_client.exec_command(
            f"bash -c '{prefix}conda env list' 2>/dev/null"
        )
        listing = stdout.read().decode()
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"Could not list conda environments: {e}")
        return False
    existing = _parse_conda_env_paths(listing)
    for name in env_names:
        env_prefix = existing.get(name)
        if not env_prefix:
            return False
        marker = _conda_env_marker_path(env_prefix)
        try:
            stdin, stdout, stderr = ssh_client.exec_command(
                f"test -f {shlex.quote(marker)}"
            )
            if stdout.channel.recv_exit_status() != 0:
                logger.debug(
                    "Conda environment %s exists but was never finished; rebuilding.",
                    name,
                )
                return False
        except Exception as e:  # pragma: no cover - defensive
            logger.debug(f"Could not check readiness of {name}: {e}")
            return False
    return True


def _select_remote_python(
    probed: List[Tuple[str, str]], local_version: str
) -> Tuple[str, str]:
    """Choose a remote interpreter whose minor version matches this one.

    dill and cloudpickle embed CPython bytecode in the payload, and that
    bytecode does not load across minor versions -- a 3.9 worker handed a 3.12
    payload dies on "unknown opcode", which names nothing the user can act on.
    The old code took the first remote interpreter that was merely >= 3.6 and
    never compared it to the local one.

    Args:
        probed: ``(command, "major.minor")`` pairs actually found remotely, in
            preference order.
        local_version: ``"major.minor"`` of the submitting interpreter.

    Returns:
        The matching ``(command, version)``.

    Raises:
        RuntimeError: when no remote interpreter matches.
    """
    for command, version in probed:
        if version == local_version:
            return command, version
    if probed:
        found = ", ".join(sorted({version for _, version in probed}))
        raise RuntimeError(
            f"The remote system has Python {found}, but this session runs "
            f"Python {local_version}. Serialized functions carry CPython "
            "bytecode, which cannot be loaded by a different minor version, so "
            f"the cluster needs a Python {local_version} interpreter -- or "
            "conda, which clustrix will use to create one."
        )
    raise RuntimeError(
        "No Python 3 interpreter found on the remote system. Consider installing conda."
    )


def _write_remote_text(ssh_client, remote_path: str, content: str) -> None:
    """Write `content` to `remote_path` over SFTP.

    Used for anything multi-line. Shell heredocs cannot be composed into a
    `cmd && cmd` chain, and quoting a long payload into `echo` invites the
    exact escaping bugs that are hardest to notice: the command succeeds and
    the file holds something subtly wrong.
    """
    sftp = ssh_client.open_sftp()
    try:
        with sftp.open(remote_path, "w") as handle:
            handle.write(content)
    finally:
        sftp.close()


def setup_two_venv_environment(
    ssh_client,
    work_dir: str,
    requirements: Dict[str, str],
    config: Optional[ClusterConfig] = None,
) -> Dict[str, Any]:
    """Setup a two-venv environment on remote cluster for cross-version compatibility.

    This function creates two separate virtual environments:
    1. VENV1: Compatible Python version for serialization/deserialization
    2. VENV2: Job execution environment that replicates the local environment

    Args:
        ssh_client: SSH client connection
        work_dir: Remote working directory
        requirements: Package requirements
        config: Cluster configuration

    Returns:
        Dict containing paths to both Python executables
    """
    if config is None:
        from .config import get_config

        config = get_config()

    import sys

    local_python_version = f"{sys.version_info.major}.{sys.version_info.minor}"

    # Check if conda is available first - on many clusters, only conda Python
    # works. Two things make this harder than `conda --version`:
    #
    #  * paramiko's exec_command starts a non-interactive, non-login shell,
    #    which never sources the profile scripts that put conda on PATH.
    #  * On many HPC installs `conda` is a wrapper that refuses to do anything
    #    until conda.sh has been sourced ("The 'conda' system must be
    #    initialized as shell functions"), so PATH alone is not enough.
    #
    # So locate conda.sh and source it ahead of every conda command. Without
    # this, clustrix built two virtualenvs with pip over NFS instead, which
    # took longer than venv_setup_timeout and failed.
    conda_available = False
    conda_setup_prefix = ""
    conda_probe = (
        "bash -lc '"
        'for p in "$CONDA_PREFIX" "$(conda info --base 2>/dev/null)" '
        '"$HOME/miniconda3" "$HOME/anaconda3" "$HOME/miniforge3" '
        "/opt/conda /usr/local/miniconda3 /usr/local/anaconda3; do "
        'if [ -n "$p" ] && [ -f "$p/etc/profile.d/conda.sh" ]; then '
        'echo "$p/etc/profile.d/conda.sh"; exit 0; fi; done; '
        # An uninitialised conda wrapper names conda.sh in the very error it
        # prints, which at some sites is the only pointer available.
        'conda --version 2>&1 | grep -oE "/[^ ]*/etc/profile.d/conda.sh" | head -1'
        "'"
    )
    stdin, stdout, stderr = ssh_client.exec_command(conda_probe)
    conda_sh = ""
    for line in stdout.read().decode().splitlines():
        line = line.strip()
        if line.endswith("/etc/profile.d/conda.sh"):
            conda_sh = line
            break

    if conda_sh:
        conda_available = True
        conda_setup_prefix = f"source {conda_sh}"
        print(f"Conda available on remote system ({conda_sh}), using it for both venvs")
    else:
        stdin, stdout, stderr = ssh_client.exec_command(
            "bash -lc 'conda --version' 2>/dev/null"
        )
        if "conda" in stdout.read().decode():
            conda_available = True
            print("Conda available on remote system, using it for both venvs")

    if conda_available:
        # Use conda for both VENV1 and VENV2 to ensure compatibility.
        #
        # Both environments are pinned to the *local* Python version. dill and
        # cloudpickle embed CPython bytecode in the payload, and that bytecode
        # is not portable across minor versions -- a function pickled under
        # 3.12 and loaded under 3.9 raises "unknown opcode". Since the function
        # object is handed from the caller to VENV1 and on to VENV2, every hop
        # has to agree on the interpreter version.
        compatible_python = "conda"  # Special marker to use conda
        remote_python_version = local_python_version
    else:
        # Fall back to system Python search
        compatible_python = None
        venv1_python = None

        # Try to find system Python (many clusters don't have this accessible)
        venv1_candidates = [
            f"python{local_python_version}",
            "python3.12",
            "python3.11",
            "python3.10",
            "python3.9",
            "python3.8",
            "python3.7",
            "python3.6",
            "python3",
            "python",
        ]

        probed: List[Tuple[str, str]] = []
        for python_cmd in venv1_candidates:
            test_cmd = (
                f"{python_cmd} -c 'import sys; print(sys.version_info[:2])' 2>/dev/null"
            )
            stdin, stdout, stderr = ssh_client.exec_command(test_cmd)
            version_output = stdout.read().decode().strip()

            if version_output and "(" in version_output:
                try:
                    version_str = version_output.split("(")[1].split(")")[0]
                    major, minor = map(int, version_str.split(", ")[:2])
                except Exception:
                    continue
                if major == 3:
                    probed.append((python_cmd, f"{major}.{minor}"))
                    if f"{major}.{minor}" == local_python_version:
                        break

        # Not "any Python 3 will do": the payload's bytecode is version-locked.
        venv1_python, remote_python_version = _select_remote_python(
            probed, local_python_version
        )
        compatible_python = venv1_python

    # Environment names.
    #
    # Conda environments are named after what is IN them -- the Python version
    # and the requirement set -- not after the job directory. Naming them per
    # job meant every single @cluster call built two fresh conda environments
    # and left them behind: on a shared filesystem that is roughly ten minutes
    # each, twenty minutes before any user code runs, repeated for every job,
    # accumulating environments nothing ever removes.
    #
    # Keying on content means the second job with the same requirements reuses
    # the first job's environments, and a job whose requirements differ gets
    # its own rather than silently inheriting the wrong ones. `conda create`
    # is skipped when the environment already exists.
    venv1_path = f"{work_dir}/venv1_serialization"
    venv2_path = f"{work_dir}/venv2_execution"
    env_key = _environment_key(remote_python_version, requirements, config)
    conda_env1_name = f"clustrix_venv1_{env_key}"
    conda_env2_name = f"clustrix_venv2_{env_key}"

    commands = [f"cd {shlex.quote(work_dir)}"]
    if conda_setup_prefix:
        # Every `conda ...` below runs in a fresh non-login shell, so conda.sh
        # has to be sourced first. The generated job script does the same.
        commands.insert(0, conda_setup_prefix)

    if compatible_python == "conda" and _conda_envs_exist(
        ssh_client, conda_setup_prefix, conda_env1_name, conda_env2_name
    ):
        # Already built by an earlier job with the same requirements. Building
        # them again would cost ten minutes per environment for no change.
        print(f"Reusing existing conda environments ({env_key})")
        return {
            "compatible_python": compatible_python,
            "remote_python_version": remote_python_version,
            "venv1_python": f"conda run -n {conda_env1_name} python",
            "venv1_path": f"conda:{conda_env1_name}",
            "venv2_python": f"conda run -n {conda_env2_name} python",
            "venv2_path": f"conda:{conda_env2_name}",
            "conda_env1_name": conda_env1_name,
            "conda_env2_name": conda_env2_name,
            "conda_env_name": conda_env2_name,
            "conda_setup_prefix": conda_setup_prefix,
            "uses_conda": True,
        }

    if compatible_python == "conda":
        # Use conda for both VENV1 and VENV2 (preferred for clusters)
        commands.extend(
            [
                # Create VENV1 using conda, matching the local Python version
                # so dill payloads round-trip (see remote_python_version above)
                f"conda create -n {shlex.quote(conda_env1_name)} "
                f"python={shlex.quote(remote_python_version)} -y",
                f"conda run -n {conda_env1_name} pip install --upgrade pip --timeout=30 || echo 'pip upgrade failed for conda venv1'",
                f"conda run -n {conda_env1_name} pip install dill cloudpickle --timeout=30",
                # Create VENV2 using conda, same version as VENV1 for execution
                f"conda create -n {shlex.quote(conda_env2_name)} "
                f"python={shlex.quote(remote_python_version)} -y",
                f"conda run -n {conda_env2_name} pip install --upgrade pip --timeout=30 || echo 'pip upgrade failed for conda venv2'",
            ]
        )
    else:
        # Fall back to regular venv (less common on clusters)
        commands.extend(
            [
                # Create VENV1 (serialization environment)
                f"{compatible_python} -m venv {shlex.quote(venv1_path)}",
                f"source {shlex.quote(venv1_path)}/bin/activate",
                "pip install --upgrade pip --timeout=30 || echo 'pip upgrade failed for venv1'",
                "pip install dill cloudpickle --timeout=30",
                "deactivate",
                # Create VENV2 using regular venv
                f"{compatible_python} -m venv {shlex.quote(venv2_path)}",
                f"source {shlex.quote(venv2_path)}/bin/activate",
                "pip install --upgrade pip --timeout=30 || echo 'pip upgrade failed for venv2'",
                "deactivate",
            ]
        )

    # Install essential packages in VENV2
    essential_packages = ["dill", "cloudpickle"]
    for pkg in essential_packages:
        if compatible_python == "conda":
            if pkg in requirements:
                commands.append(
                    f"conda run -n {conda_env2_name} pip install "
                    f"{shlex.quote(f'{pkg}=={requirements[pkg]}')} --timeout=30"
                )
            else:
                commands.append(
                    f"conda run -n {conda_env2_name} pip install "
                    f"{shlex.quote(pkg)} --timeout=30"
                )
        else:
            commands.append(f"source {shlex.quote(venv2_path)}/bin/activate")
            if pkg in requirements:
                commands.append(
                    f"pip install {shlex.quote(f'{pkg}=={requirements[pkg]}')} "
                    f"--timeout=30"
                )
            else:
                commands.append(f"pip install {shlex.quote(pkg)} --timeout=30")
            commands.append("deactivate")

    # Rebuild the local environment on the worker.
    #
    # This used to install a hardcoded list of nine "core scientific" packages
    # and drop everything else, so a function importing anything outside that
    # list -- torch, networkx, polars, the user's own dependency -- failed
    # remotely with ModuleNotFoundError while running fine locally. The
    # environment is now mirrored from whatever the local package manager
    # reports, which is what this function's docstring has always promised.
    #
    # The specs go into a requirements file rather than one `pip install` per
    # package: a single resolve is far faster over a few hundred packages, and
    # pip reports a conflict once instead of leaving a half-built environment.
    if requirements and getattr(config, "replicate_local_environment", True):
        excluded = {
            str(name).lower()
            for name in (getattr(config, "excluded_packages", None) or [])
        }
        mirrored = {
            k: v
            for k, v in requirements.items()
            if k not in essential_packages and k.lower() not in excluded
        }

        if mirrored:
            spec_lines = "\n".join(f"{k}=={v}" for k, v in sorted(mirrored.items()))
            requirements_path = f"{work_dir}/clustrix_requirements.txt"
            # Written over SFTP rather than emitted as a shell heredoc: these
            # commands are joined with " && ", which would put the heredoc
            # terminator on a line with `&& ...` after it. Bash would not
            # recognise it as a terminator, and `cat` would swallow the rest of
            # the setup script into the requirements file -- so pip never ran
            # and the environment silently stayed empty.
            _write_remote_text(ssh_client, requirements_path, spec_lines + "\n")
            commands.append(
                f"echo 'Replicating local environment ({len(mirrored)} packages)...'"
            )
            # No `|| echo`: a package that cannot install here means the remote
            # environment does not match the local one, and the function will
            # fail later with a less obvious error. Name it now. `excluded_packages`
            # is the documented way to drop one deliberately.
            install = f"pip install -r {shlex.quote(requirements_path)} --timeout=300"
            if compatible_python == "conda":
                commands.append(f"conda run -n {conda_env2_name} {install}")
            else:
                commands.append(f"source {shlex.quote(venv2_path)}/bin/activate")
                commands.append(install)
                commands.append("deactivate")

    # Add cluster-specific package installations from config
    if hasattr(config, "cluster_packages") and config.cluster_packages:
        commands.append("echo 'Installing cluster-specific packages...'")
        for package_spec in config.cluster_packages:
            if isinstance(package_spec, str):
                # Simple package name or package==version
                if compatible_python == "conda":
                    commands.append(
                        f"conda run -n {conda_env2_name} pip install "
                        f"{shlex.quote(package_spec)} --timeout=300"
                    )
                else:
                    commands.append(f"source {shlex.quote(venv2_path)}/bin/activate")
                    commands.append(
                        f"pip install {shlex.quote(package_spec)} --timeout=300"
                    )
                    commands.append("deactivate")
            elif isinstance(package_spec, dict):
                # Complex package specification with options
                pkg_name = package_spec.get("package", "")
                pip_args = package_spec.get("pip_args", "")
                timeout = package_spec.get("timeout", 300)

                if pkg_name:
                    if compatible_python == "conda":
                        install_cmd = (
                            f"conda run -n {conda_env2_name} pip install "
                            f"{shlex.quote(pkg_name)}"
                        )
                    else:
                        commands.append(
                            f"source {shlex.quote(venv2_path)}/bin/activate"
                        )
                        install_cmd = f"pip install {shlex.quote(pkg_name)}"
                    if pip_args:
                        install_cmd += f" {pip_args}"
                    install_cmd += f" --timeout={timeout}"
                    commands.append(install_cmd)
                    if compatible_python != "conda":
                        commands.append("deactivate")

    # Add cluster-specific post-installation commands from config
    if (
        hasattr(config, "venv_post_install_commands")
        and config.venv_post_install_commands
    ):
        commands.append("echo 'Running cluster-specific post-installation commands...'")
        for cmd in config.venv_post_install_commands:
            if compatible_python == "conda":
                # Run post-install commands in conda environment
                commands.append(f"conda run -n {conda_env2_name} {cmd}")
            else:
                commands.append(f"source {shlex.quote(venv2_path)}/bin/activate")
                commands.append(f"{cmd}")
                commands.append("deactivate")

    # Mark the environments complete. This is the LAST link in the `&&` chain,
    # so it is only reached when every create and every install succeeded --
    # which is what makes the reuse check above safe. Without it, a run whose
    # installs failed left correctly-named but half-empty environments that
    # every later job with the same requirements silently reused.
    if compatible_python == "conda":
        commands.extend(_conda_env_ready_commands([conda_env1_name, conda_env2_name]))

    # Execute setup commands
    full_command = " && ".join(commands)
    stdin, stdout, stderr = ssh_client.exec_command(full_command)

    # Wait for completion with extended timeout
    exit_status = stdout.channel.recv_exit_status()

    if exit_status == 0:
        result: Dict[str, Any] = {
            "compatible_python": compatible_python,
            "remote_python_version": remote_python_version,
        }

        if compatible_python == "conda":
            # Both venvs use conda
            result.update(
                {
                    "venv1_python": f"conda run -n {conda_env1_name} python",
                    "venv1_path": f"conda:{conda_env1_name}",
                    "venv2_python": f"conda run -n {conda_env2_name} python",
                    "venv2_path": f"conda:{conda_env2_name}",
                    "conda_env1_name": conda_env1_name,
                    "conda_env2_name": conda_env2_name,
                    "conda_env_name": conda_env2_name,  # For backward compatibility with job script generation
                    "conda_setup_prefix": conda_setup_prefix,
                    "uses_conda": True,
                }
            )
        else:
            # Both venvs use regular virtualenv
            result.update(
                {
                    "venv1_python": f"{venv1_path}/bin/python",
                    "venv1_path": venv1_path,
                    "venv2_python": f"{venv2_path}/bin/python",
                    "venv2_path": venv2_path,
                    "conda_env1_name": None,
                    "conda_env2_name": None,
                    "uses_conda": False,
                }
            )

        return result
    else:
        error_output = stderr.read().decode()
        raise RuntimeError(f"Failed to setup two-venv environment: {error_output}")


def setup_python_compatible_environment(
    ssh_client,
    work_dir: str,
    requirements: Dict[str, str],
    config: Optional[ClusterConfig] = None,
) -> str:
    """Setup a Python-compatible environment on remote cluster.

    This function creates a separate venv with a compatible Python version
    to ensure cross-version compatibility for function serialization.

    Args:
        ssh_client: SSH client connection
        work_dir: Remote working directory
        requirements: Package requirements
        config: Cluster configuration

    Returns:
        Path to the compatible Python executable
    """
    if config is None:
        from .config import get_config

        config = get_config()

    # Try to detect available Python versions on the remote system
    detect_cmd = "python3 --version 2>/dev/null || python --version 2>/dev/null || echo 'no python'"
    stdin, stdout, stderr = ssh_client.exec_command(detect_cmd)
    _ = stdout.read().decode().strip()  # Version detection output

    # Check if we can create a compatible venv
    compatible_python = None

    # Try different Python versions in order of preference
    python_candidates = [
        "python3.9",
        "python3.8",
        "python3.7",
        "python3.6",
        "python3",
        "python",
    ]

    for python_cmd in python_candidates:
        test_cmd = (
            f"{python_cmd} -c 'import sys; print(sys.version_info[:2])' 2>/dev/null"
        )
        stdin, stdout, stderr = ssh_client.exec_command(test_cmd)
        version_output = stdout.read().decode().strip()

        if version_output and "(" in version_output:
            try:
                # Extract version tuple
                version_str = version_output.split("(")[1].split(")")[0]
                major, minor = map(int, version_str.split(", ")[:2])

                # Check if version is compatible (3.6+)
                if major == 3 and minor >= 6:
                    compatible_python = python_cmd
                    break
            except Exception:
                continue

    if compatible_python:
        # Create a separate venv with the compatible Python version
        compat_venv_path = f"{work_dir}/compat_venv"

        commands = [
            f"cd {shlex.quote(work_dir)}",
            f"{compatible_python} -m venv {shlex.quote(compat_venv_path)}",
            f"source {shlex.quote(compat_venv_path)}/bin/activate",
        ]

        # Install only essential packages for function execution
        # Skip complex requirements to avoid timeout issues
        commands.extend(
            [
                # pip's own version is not part of the replicated environment,
                # so failing to upgrade it is genuinely non-fatal.
                "pip install --upgrade pip --timeout=30 || echo 'pip upgrade failed, continuing...'",
                # dill and cloudpickle are not optional: the generated worker
                # refuses to fall back to stdlib pickle, because pickle
                # serializes a function by qualified name and cannot resolve it
                # in a fresh interpreter. Swallowing this failure only moved the
                # error to a later, far more confusing point.
                "pip install dill cloudpickle --timeout=30",
            ]
        )

        # Execute setup commands
        full_command = " && ".join(commands)
        stdin, stdout, stderr = ssh_client.exec_command(full_command)

        # Wait for completion
        exit_status = stdout.channel.recv_exit_status()
        if exit_status == 0:
            return f"{compat_venv_path}/bin/python"
        else:
            # Fall back to original approach
            return setup_remote_environment(ssh_client, work_dir, requirements, config)
    else:
        # Fall back to original approach
        return setup_remote_environment(ssh_client, work_dir, requirements, config)


def resolve_remote_python(ssh_client, config: ClusterConfig) -> str:
    """Find a remote interpreter that can actually run this job.

    An explicitly configured `python_executable` is respected as given -- if
    the user named it, they mean it.

    Otherwise the version must MATCH the caller's. dill embeds CPython
    bytecode, so a function pickled under 3.12 and unpickled under 3.9 fails
    with things like "code() takes at most 15 arguments (20 given)" -- an
    error that names neither Python nor the version gap. The two-venv path
    solves this by creating a conda environment at the right version; the
    single-venv path can only use what is already installed, so if nothing
    matches it has to say so rather than proceed to that traceback.

    The default "python" is also not assumed to exist: Python 3 installs ship
    `python3`, and `python` is only present where somebody added a
    compatibility symlink.
    """
    configured = getattr(config, "python_executable", None)
    if configured and configured != "python":
        return configured

    import sys as _sys

    wanted = f"python{_sys.version_info.major}.{_sys.version_info.minor}"

    def exists(candidate: str) -> bool:
        try:
            stdin, stdout, stderr = ssh_client.exec_command(f"command -v {candidate}")
            return bool(stdout.read().decode().strip())
        except Exception:  # pragma: no cover - defensive
            return False

    if exists(wanted):
        logger.debug("Using remote interpreter %s", wanted)
        return wanted

    # Report what IS there, so the message is actionable.
    available = []
    for candidate in ("python3", "python"):
        if exists(candidate):
            try:
                stdin, stdout, stderr = ssh_client.exec_command(
                    f"{candidate} -c 'import sys; print(sys.version.split()[0])'"
                )
                version = stdout.read().decode().strip()
            except Exception:  # pragma: no cover - defensive
                version = "?"
            available.append(f"{candidate} ({version})")

    raise RuntimeError(
        f"No {wanted} on the remote host, and dill payloads cannot cross "
        f"Python minor versions. Found: {', '.join(available) or 'no Python at all'}. "
        f"Either install {wanted} there, set python_executable to a matching "
        "interpreter, or leave use_two_venv enabled so clustrix can build a "
        "conda environment at the right version."
    )


def setup_remote_environment(
    ssh_client,
    work_dir: str,
    requirements: Dict[str, str],
    config: Optional[ClusterConfig] = None,
) -> str:
    """Setup environment on remote cluster via SSH (original approach)."""

    # Get appropriate package manager
    if config is None:
        from .config import get_config

        config = get_config()

    pkg_manager = get_package_manager_command(config)

    if pkg_manager == "conda":
        # Create conda environment
        env_name = f"clustrix_env_{hash(work_dir) % 10000}"
        env_path = f"{work_dir}/conda_envs/{env_name}"

        commands = [
            f"cd {shlex.quote(work_dir)}",
            "mkdir -p conda_envs",
            f"conda create -p {shlex.quote(env_path)} "
            f"python={validate_shell_fragment('python_executable', config.python_executable).replace('python', '3.11')} -y",
        ]

        if requirements:
            # Create conda environment.yml file
            env_content = f"""name: {env_name}
dependencies:
  - python={config.python_executable.replace('python', '3.11')}
  - pip
  - pip:"""
            for pkg, version in requirements.items():
                env_content += f"\n    - {pkg}=={version}"

            # Write environment file
            sftp = ssh_client.open_sftp()
            with sftp.open(f"{work_dir}/environment.yml", "w") as f:
                f.write(env_content)
            sftp.close()

            commands.append(
                f"conda env update -p {shlex.quote(env_path)} -f environment.yml"
            )

    else:
        # Create virtual environment (for pip/uv)
        commands = [f"cd {shlex.quote(work_dir)}"]

        # Module loads, environment variables and pre-execution commands, with
        # the same quote-or-validate treatment the job scripts get.
        commands.extend(environment_setup_lines(config))

        # Now create the virtual environment. `python_executable` defaults to
        # "python", which does not exist on most modern systems -- Python 3
        # installs ship `python3`, and `python` is only present where someone
        # added a compatibility symlink. tensor01 is one of the many hosts
        # where it is absent, so `python -m venv venv` failed, the venv was
        # never created, and the job script then died on
        # `source venv/bin/activate` with "python: command not found".
        python_cmd = resolve_remote_python(ssh_client, config)
        commands.extend(
            [
                f"{shlex.quote(python_cmd)} -m venv venv",
                "source venv/bin/activate",
            ]
        )

        # dill and cloudpickle are not optional: the job script deserializes
        # the function with them, and without them it gets `func = None` and
        # dies twenty lines into a remote traceback with
        # "'NoneType' object is not callable" -- which names neither the
        # missing package nor the failed install. So this must not be swallowed
        # by `|| echo ... continuing`, as it was.
        #
        # Versions are pinned to the caller's when known, because dill embeds
        # CPython bytecode and a mismatched pair is its own class of failure;
        # an unpinned install is the fallback, not the default.
        pinned = [
            shlex.quote(f"{pkg}=={version}")
            for pkg, version in (requirements or {}).items()
            if pkg.lower() in ("dill", "cloudpickle")
        ]
        wanted = " ".join(pinned) if pinned else "dill cloudpickle"
        commands.append(
            f"{pkg_manager} install {wanted} --timeout=120 "
            f"|| {pkg_manager} install dill cloudpickle --timeout=120"
        )

    # Execute setup commands
    full_command = " && ".join(commands)
    stdin, stdout, stderr = ssh_client.exec_command(full_command)

    # Wait for completion
    exit_status = stdout.channel.recv_exit_status()
    if exit_status != 0:
        error = stderr.read().decode()
        raise RuntimeError(f"Environment setup failed: {error}")

    return "Environment setup completed successfully"


def result_key_export_line(remote_job_dir: str) -> str:
    """Shell line making the per-job result-signing key available to the job.

    Read from a 0600 file in the job directory rather than baked into job.sh,
    which is world-readable on some shared filesystems.

    The path is quoted: it is an ordinary shell word, and it comes from
    ``config.remote_work_dir``, so an unquoted ``$(...)`` in that setting ran
    as a command inside the very line meant to protect the key.
    """
    key_file = shlex.quote(f"{remote_job_dir}/.clustrix_result_key")
    return f"export CLUSTRIX_RESULT_KEY=$(cat {key_file} 2>/dev/null || true)"


def environment_setup_lines(config) -> list:
    """The module-load / export / pre-execution lines every generator emits.

    Four script generators and ``setup_remote_environment`` each carried their
    own copy, so a fix to one missed the rest. This is also the single place
    where the quote-or-validate decision for these three settings lives:

    * ``module_loads`` entries stay unquoted: ``module`` is a shell function
      and the module name is its bare argument, so quoting would change what
      is loaded. They are validated against a strict allowlist instead, and a
      metacharacter is refused by name.
    * ``environment_variables`` names cannot be quoted -- ``export NAME=``
      needs the bare name -- so they are validated as shell identifiers. The
      values beside them are quoted, which also makes a value containing a
      space work for the first time.
    * ``pre_execution_commands`` are shell commands by definition; quoting or
      restricting them would delete the feature, so they pass through. A user
      who writes a command there is asking for it to run.
    """
    lines: list = []
    for module in getattr(config, "module_loads", None) or []:
        if not str(module).strip():
            # The widget's textarea yields blank lines; `module load ` is not
            # an error worth refusing a job over.
            continue
        name = validate_shell_fragment("module_loads", str(module).strip())
        lines.append(f"module load {name}")
    for var, value in (getattr(config, "environment_variables", None) or {}).items():
        lines.append(f"export {validate_env_var_name(var)}={shlex.quote(str(value))}")
    for cmd in getattr(config, "pre_execution_commands", None) or []:
        lines.append(cmd)
    return lines


def conda_activation_lines(config) -> list:
    """Lines a generated job script needs before it can run `conda`.

    A batch script runs under a non-login shell, on a compute node, so conda
    is uninitialised there even when the submitting host found it. Every
    script generator calls this; keeping it in one place is what stops one
    backend from being fixed while another silently keeps emitting bare
    `conda run` and failing with "conda: command not found".
    """
    venv_info = getattr(config, "venv_info", None) or {}
    prefix = venv_info.get("conda_setup_prefix", "")
    return [prefix] if prefix else []


def generate_two_venv_execution_commands(
    remote_job_dir: str,
    conda_env1_name: Optional[str] = None,
    conda_env2_name: Optional[str] = None,
) -> list:
    """
    Generate the standardized two-venv execution commands.

    This centralizes the two-venv logic to eliminate code duplication across
    different cluster types (SLURM, SSH, PBS, SGE).

    The three stages hand objects to each other through files on disk. Those
    handoffs use dill (falling back to cloudpickle, then stdlib pickle) rather
    than pickle directly: the function being executed is almost always defined
    in the caller's ``__main__``, and stdlib pickle serializes such functions by
    qualified name, which cannot be resolved in a fresh remote interpreter. Both
    venvs get dill and cloudpickle installed by ``setup_two_venv_environment``.

    Args:
        remote_job_dir: Remote working directory path
        conda_env1_name: Conda environment for serialization (VENV1), if any
        conda_env2_name: Conda environment for execution (VENV2), if any

    Returns:
        List of command strings for two-venv execution
    """
    # Every use below is an ordinary shell word, so quoting is the right
    # treatment: `source <dir>/...` and `<dir>/.../python -c "` both keep
    # working with the directory quoted, and a `$(...)` in remote_work_dir
    # stops being a command. Two of these sites also *open* a double-quoted
    # `python -c "` string, where an unquoted `"` broke straight out into the
    # shell.
    quoted_dir = shlex.quote(remote_job_dir)
    env1 = shlex.quote(conda_env1_name) if conda_env1_name else None
    env2 = shlex.quote(conda_env2_name) if conda_env2_name else None

    def _serializer_preamble() -> list:
        """Lines binding ``_ser`` to dill, or failing with a reason.

        Falling back to stdlib pickle here was not a degradation, it was a
        different bug: every payload these stages exchange is written by dill,
        and pickle cannot read dill's bytes. The job then died somewhere in
        the unpickler naming neither the missing package nor the real cause.
        """
        return key_capture_lines() + [
            "import pickle",
            "try:",
            "    import dill as _ser",
            "except ImportError:",
            "    try:",
            "        import cloudpickle as _ser",
            "    except ImportError:",
            "        raise RuntimeError(",
            "            'clustrix needs dill (or at least cloudpickle) in this '",
            "            'environment: the function, its arguments and its result '",
            "            'are exchanged as dill bytes, which stdlib pickle cannot '",
            "            'read. Install it on the cluster (pip install dill) and '",
            "            're-submit.')",
        ]

    def _error_handler(stage: str, message: str) -> list:
        """Lines recording a stage failure without masking an earlier one.

        Each stage writes its own ``error_<stage>.pkl`` unconditionally, but
        only claims the shared ``error.pkl`` if no earlier stage already did.
        Without this, a stage-1 failure is overwritten by the cascade it
        causes, and the caller is shown the symptom instead of the cause.

        ``error.pkl`` is signed exactly like ``result.pkl``: the caller
        deserializes it with dill, so an unsigned one would make "make the
        job fail" a way to hand the submitting machine arbitrary code.
        """
        return (
            [
                "except Exception as e:",
                f"    print('{message}', str(e))",
                "    traceback.print_exc()",
                "    import os as _os",
                "    _payload = {"
                "'error': str(e), "
                "'traceback': traceback.format_exc(), "
                f"'stage': '{stage}'"
                "}",
                # Ship the exception OBJECT too, so the caller can catch the type
                # the function actually raised instead of a generic RuntimeError.
                # _ser (dill) handles exception classes defined in the caller's
                # __main__; an exception that refuses to serialize at all must not
                # take the error report down with it.
                "    try:",
                "        _blob = _ser.dumps(dict(_payload, exception=e), protocol=4)",
                "    except Exception:",
                "        _blob = pickle.dumps(_payload, protocol=4)",
                f"    with open('error_{stage}.pkl', 'wb') as f:",
                "        f.write(_blob)",
                "    if not _os.path.exists('error.pkl'):",
                "        with open('error.pkl', 'wb') as f:",
                "            f.write(_blob)",
            ]
            + payload_signing_lines("_blob", "error.pkl", indent="        ")
            + [
                "    raise",
            ]
        )

    return (
        [
            "# Two-venv approach for cross-version compatibility",
            "# VENV1: Serialization/deserialization with compatible Python",
            "# VENV2: Function execution with proper environment",
            "",
            "# Step 1: Use VENV1 to deserialize function data",
            (
                f"# Using conda environment {conda_env1_name}"
                if conda_env1_name
                else f"source {quoted_dir}/venv1_serialization/bin/activate"
            ),
            (f'conda run -n {env1} python -c "' if conda_env1_name else 'python -c "'),
        ]
        + _serializer_preamble()
        + [
            "import sys",
            "import traceback",
            "",
            "try:",
            "    import dill",
            "except ImportError:",
            "    dill = None",
            "try:",
            "    import cloudpickle",
            "except ImportError:",
            "    cloudpickle = None",
            "",
            "print('VENV1 - Deserializing function data')",
            "print('Python version:', sys.version)",
            "",
            "try:",
            "    with open('function_data.pkl', 'rb') as f:",
            "        data = pickle.load(f)",
            "    ",
            "    # Try to deserialize function",
            "    func = None",
            "    clean_source = None",
            "    func_info = data.get('func_info', {})",
            "    try:",
            "        func = dill.loads(data['function']) if dill else None",
            "        print('Successfully deserialized function with dill')",
            "    except Exception as e:",
            "        print('Dill deserialization failed:', str(e))",
            "        try:",
            "            func = cloudpickle.loads(data['function']) if cloudpickle else None",
            "            print('Successfully deserialized function with cloudpickle')",
            "        except Exception as e2:",
            "            print('Cloudpickle deserialization failed:', str(e2))",
            "            # Try source code fallback",
            "            if func_info.get('source'):",
            "                print('Using source code fallback')",
            "                # Remove @cluster decorator from source",
            "                import textwrap",
            "                source = func_info['source']",
            "                lines = source.split('\\n')",
            "                clean_lines = []",
            "                for line in lines:",
            "                    if not line.strip().startswith('@'):",
            "                        clean_lines.append(line)",
            "                clean_source = '\\n'.join(clean_lines)",
            "                clean_source = textwrap.dedent(clean_source)",
            "                ",
            "                # Create function from source",
            "                namespace = {}",
            "                exec(clean_source, namespace)",
            "                func = namespace[func_info['name']]",
            "                print('Successfully created function from source code')",
            "            else:",
            "                raise Exception('All deserialization methods failed')",
            "    ",
            "    # _ser, not stdlib pickle: args may carry classes defined in",
            "    # the caller's __main__, which pickle can only store by name.",
            "    args = _ser.loads(data['args'])",
            "    kwargs = _ser.loads(data['kwargs'])",
            "    ",
            "    # Pass data to VENV2 for execution. _ser (dill/cloudpickle) is",
            "    # required here: stdlib pickle cannot serialize a function that",
            "    # is not importable by name in this interpreter.",
            "    with open('function_deserialized.pkl', 'wb') as f:",
            "        if clean_source is not None:",
            "            # Function was created from source code, pass the source",
            "            _ser.dump({'source': clean_source, 'func_name': func_info['name'], 'args': args, 'kwargs': kwargs}, f, protocol=4)",
            "        else:",
            "            # Function was deserialized from binary, pass the function object",
            "            _ser.dump({'func': func, 'args': args, 'kwargs': kwargs}, f, protocol=4)",
            "    ",
            "    print('VENV1 - Function data prepared for VENV2 using', _ser.__name__)",
            "    ",
        ]
        + _error_handler("venv1_deserialize", "VENV1 - Error during deserialization:")
        + [
            '"',
            "",
            "# Step 2: Use VENV2 to execute the function",
            (
                "# No deactivation needed for conda run"
                if conda_env1_name
                else "deactivate"
            ),
            (
                f"# Using conda environment {conda_env2_name}"
                if conda_env2_name
                else f"source {quoted_dir}/venv2_execution/bin/activate"
            ),
            (
                f'conda run -n {env2} python -c "'
                if conda_env2_name
                else f'{quoted_dir}/venv2_execution/bin/python -c "'
            ),
        ]
        + _serializer_preamble()
        + [
            "import sys",
            "import traceback",
            "",
            "print('VENV2 - Executing function')",
            "print('Python version:', sys.version)",
            "",
            "try:",
            "    import os",
            "    if not os.path.exists('function_deserialized.pkl'):",
            "        raise FileNotFoundError('function_deserialized.pkl not found - VENV1 deserialization may have failed')",
            "    with open('function_deserialized.pkl', 'rb') as f:",
            "        exec_data = _ser.load(f)",
            "    ",
            "    if 'func' in exec_data:",
            "        # Function object was passed",
            "        func = exec_data['func']",
            "    elif 'source' in exec_data:",
            "        # Source code was passed, recreate function",
            "        print('Recreating function from source code in VENV2')",
            "        namespace = {}",
            "        exec(exec_data['source'], namespace)",
            "        func = namespace[exec_data['func_name']]",
            "    else:",
            "        raise Exception('No function or source code found')",
            "    ",
            "    args = exec_data['args']",
            "    kwargs = exec_data['kwargs']",
            "    ",
            "    # Execute the function",
            "    print('Executing function with args:', args)",
            "    result = func(*args, **kwargs)",
            "    print('Function execution completed successfully')",
            "    ",
            "    # Save result for VENV1 to serialize",
            "    with open('result_raw.pkl', 'wb') as f:",
            "        _ser.dump(result, f, protocol=4)",
            "    ",
        ]
        + _error_handler("venv2_execute", "VENV2 - Error during execution:")
        + [
            '"',
            "",
            "# Step 3: Use VENV1 to serialize the result",
            (
                "# No deactivation needed for conda run"
                if conda_env2_name
                else "deactivate"
            ),
            (
                f"# Using conda environment {conda_env1_name}"
                if conda_env1_name
                else f"source {quoted_dir}/venv1_serialization/bin/activate"
            ),
            (f'conda run -n {env1} python -c "' if conda_env1_name else 'python -c "'),
        ]
        + _serializer_preamble()
        + [
            "import sys",
            "import traceback",
            "",
            "print('VENV1 - Serializing result')",
            "",
            "try:",
            "    import os",
            "    if not os.path.exists('result_raw.pkl'):",
            "        raise FileNotFoundError('result_raw.pkl not found - VENV2 execution may have failed')",
            "    with open('result_raw.pkl', 'rb') as f:",
            "        result = _ser.load(f)",
            "    ",
            "    print('Result loaded from VENV2:', type(result))",
            "    ",
            "    _payload_bytes = _ser.dumps(result, protocol=4)",
            "    with open('result.pkl', 'wb') as f:",
            "        f.write(_payload_bytes)",
            "    ",
            "    # Tag the result so the caller can tell it apart from anything",
            "    # else that may have been written into this directory. Loading a",
            "    # pickle executes code, so the caller must not do it on trust.",
        ]
        + payload_signing_lines("_payload_bytes", "result.pkl")
        + [
            "    ",
            "    print('Result serialized successfully')",
            "    ",
        ]
        + _error_handler(
            "venv1_serialize", "VENV1 - Error during result serialization:"
        )
        + [
            '"',
            "",
        ]
    )


MEMORY_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMGTP]?)(i?)B?\s*$", re.I)


def normalize_memory(value: Any, target: str) -> str:
    """Render a memory size in the form a given scheduler accepts.

    Clustrix's own configuration uses human sizes like ``"16GB"``. Schedulers
    do not agree on that spelling:

    * Kubernetes quantities are ``16G`` (decimal) or ``16Gi`` (binary) and a
      pod carrying ``16GB`` is rejected outright by the API server.
    * SLURM's ``--mem`` takes a bare number with an optional ``K|M|G|T``.
    * PBS and SGE accept ``16gb`` and ``16G`` respectively.

    Passing the configured string through unchanged is what made
    ``default_memory`` unusable on Kubernetes.

    Args:
        value: A size such as ``"16GB"``, ``"512Mi"``, ``16`` (GB assumed).
        target: ``"kubernetes"``, ``"slurm"``, ``"pbs"`` or ``"sge"``.

    Returns:
        The size spelled the way ``target`` expects it.
    """
    text = str(value).strip()
    match = MEMORY_PATTERN.match(text)
    if not match:
        # Not a shape we recognise. Passing it through unchanged is better
        # than guessing: the scheduler's own error names the real problem.
        logger.warning(
            "Could not parse memory value %r; passing it to %s unchanged.",
            value,
            target,
        )
        return text

    amount, unit = match.group(1), match.group(2).upper()
    if not unit:
        unit = "G"  # a bare number has always meant gigabytes here

    # Schedulers take integers. "1.5GB" would reach SLURM as --mem=1.5G and
    # PBS as mem=1.5gb, both of which they reject, so round up to the next
    # whole unit rather than emit something that cannot be submitted. Rounding
    # *up* because a job asking for 1.5G and given 1G would be killed.
    if "." in amount:
        import math

        whole = math.ceil(float(amount))
        logger.info(
            "Rounding memory %s%s up to %d%s: schedulers take whole units.",
            amount,
            unit,
            whole,
            unit,
        )
        amount = str(whole)

    if target == "kubernetes":
        # "16GB" means 16 gibibytes in every other part of clustrix, so keep
        # the binary suffix rather than silently shrinking the request by 7%.
        return f"{amount}{unit}i" if unit else amount
    if target == "slurm":
        return f"{amount}{unit}"
    if target == "pbs":
        return f"{amount.lower()}{unit.lower()}b"
    if target == "sge":
        return f"{amount}{unit}"
    return text


def resolve_job_resources(
    job_config: Dict[str, Any], config: ClusterConfig
) -> Dict[str, Any]:
    """Fill in resource keys the caller left out, from the configured defaults.

    The scheduler script generators index job_config["cores"], ["memory"] and
    ["time"] directly, so a caller that omits one gets a bare
    ``KeyError: 'time'`` -- which is what the GPU-detection probe hit, and
    what got swallowed into "Could not detect remote GPU count". The defaults
    exist for exactly this; use them.
    """
    resolved = dict(job_config)
    resolved.setdefault("cores", config.default_cores)
    resolved.setdefault("memory", config.default_memory)
    resolved.setdefault("time", config.default_time)
    return resolved


def create_job_script(
    cluster_type: str,
    job_config: Dict[str, Any],
    remote_job_dir: str,
    config: ClusterConfig,
) -> str:
    """Create job submission script for different cluster types."""

    job_config = resolve_job_resources(job_config, config)

    if cluster_type == "slurm":
        return _create_slurm_script(job_config, remote_job_dir, config)
    elif cluster_type == "pbs":
        return _create_pbs_script(job_config, remote_job_dir, config)
    elif cluster_type == "sge":
        return _create_sge_script(job_config, remote_job_dir, config)
    elif cluster_type == "ssh":
        return _create_ssh_script(job_config, remote_job_dir, config)
    else:
        raise ValueError(f"Unsupported cluster type: {cluster_type}")


def key_capture_lines(indent: str = "") -> list:
    """Take CLUSTRIX_RESULT_KEY out of the environment, keeping its value.

    The key is the whole basis on which the caller believes a result came
    from this job, so anything that can read it can forge one -- and the
    user's function, plus every dependency it imports, runs in this same
    process. Nothing needs the variable after this point: the signing lines
    below use the captured value, so the environment the function inherits no
    longer carries the secret.
    """
    return [
        f"{indent}import os as _os",
        f"{indent}_CLUSTRIX_KEY = _os.environ.pop('CLUSTRIX_RESULT_KEY', '')",
    ]


def payload_signing_lines(payload_var: str, target: str, indent: str = "    ") -> list:
    """Python lines writing ``<target>.hmac`` beside a payload the caller reads.

    The single place any remote stage tags a file. ``result.pkl`` had one and
    ``error.pkl`` had none, which made "make the job fail" a complete bypass
    of the check: both files end up in ``dill.loads`` on the submitting
    machine, so both must be signed with the per-job key.

    Args:
        payload_var: Name of the Python variable holding the exact bytes that
            were written -- the tag has to cover those, not a re-serialization.
        target: File name that was written, e.g. ``result.pkl``.
        indent: Leading whitespace for the emitted lines.
    """
    return [
        f"{indent}import hashlib as _hashlib",
        f"{indent}import hmac as _hmac",
        f"{indent}if _CLUSTRIX_KEY:",
        f"{indent}    _tag = _hmac.new(_CLUSTRIX_KEY.encode(), {payload_var}, "
        "_hashlib.sha256).hexdigest()",
        f"{indent}    with open('{target}.hmac', 'w') as _sigf:",
        f"{indent}        _sigf.write(_tag)",
    ]


def result_signing_lines(indent: str = "    ", serializer: str = "pickle") -> list:
    """Python lines that write result.pkl together with its HMAC.

    Both execution branches must emit this. The single-venv branch did not,
    while the submitter recorded a signing key regardless -- so every job that
    fell back to it (use_two_venv=False, or any two-venv setup failure or
    timeout) produced a result the caller then refused as unsigned. A degraded
    but working path became a hard failure.

    Args:
        indent: Leading whitespace for the emitted lines.
        serializer: Name of the module-or-alias in scope on the worker that
            writes the bytes. The caller loads ``result.pkl`` with dill, so a
            worker that has dill should write it with dill: the cloud script
            passes its ``_ser`` alias here, where it previously used stdlib
            ``pickle.dump`` under a comment claiming otherwise.
    """
    return [
        f"{indent}_payload_bytes = {serializer}.dumps(result, protocol=4)",
        f"{indent}with open('result.pkl', 'wb') as f:",
        f"{indent}    f.write(_payload_bytes)",
    ] + payload_signing_lines("_payload_bytes", "result.pkl", indent)


def job_execution_lines(remote_job_dir: str, config: ClusterConfig) -> list:
    """The lines that actually run the user's function in a job script.

    Shared by every scheduler. It used to live only inside the SLURM
    generator: PBS ran `python execute_function.py`, a file nothing in
    clustrix has ever created, and SGE carried its own divergent copy of
    the single-venv script. Both therefore missed the two-venv path, the
    result signing and every fix made to the SLURM one.
    """
    script_lines: list = []
    # Add execution commands
    # `python_executable` is a single command word (config default "python"),
    # so quoting is the right treatment -- it survives a path with a space and
    # neutralises anything else.
    python_cmd = shlex.quote(config.python_executable or "python")
    # `cd` takes an ordinary shell word, so the job directory is quoted here.
    quoted_dir = shlex.quote(remote_job_dir)

    # Check if we have two-venv setup
    if hasattr(config, "venv_info") and config.venv_info:
        # Use the centralized two-venv approach for cross-version compatibility
        script_lines.append(f"cd {quoted_dir}")
        conda_env1_name = config.venv_info.get("conda_env1_name", None)
        conda_env2_name = config.venv_info.get("conda_env2_name", None)
        script_lines.append(result_key_export_line(remote_job_dir))
        script_lines.extend(conda_activation_lines(config))
        script_lines.extend(
            generate_two_venv_execution_commands(
                remote_job_dir, conda_env1_name, conda_env2_name
            )
        )
    else:
        # Use the original single-venv approach
        script_lines.append(result_key_export_line(remote_job_dir))
        script_lines.extend(
            [
                f"cd {quoted_dir}",
                "source venv/bin/activate",
                f'{python_cmd} -c "',
            ]
            + key_capture_lines()
            + [
                "import pickle",
                "import sys",
                "import traceback",
                "",
                "try:",
                "    import dill",
                "except ImportError:",
                "    dill = None",
                "try:",
                "    import cloudpickle",
                "except ImportError:",
                "    cloudpickle = None",
                "",
                "try:",
                "    with open('function_data.pkl', 'rb') as f:",
                "        data = pickle.load(f)",
                "    ",
                # dill (or at least cloudpickle) is a hard requirement of this
                # environment, not a nicety. The submitting side writes the
                # function, args and kwargs with _dumps_by_value(), which is
                # dill first; stdlib pickle cannot read dill's bytes, so the
                # old `dill or cloudpickle or pickle` fallback did not degrade
                # gracefully -- it produced an unrelated error deep in the
                # unpickler, or a silent `func = None` followed by
                # "'NoneType' object is not callable". Say so instead.
                "    if dill is None and cloudpickle is None:",
                "        raise RuntimeError(",
                "            'clustrix needs dill (or at least cloudpickle) in the job '",
                "            'environment: this function and its arguments were '",
                "            'serialized with dill, and stdlib pickle cannot read those '",
                "            'bytes. Install it on the cluster (pip install dill) and '",
                "            're-submit.')",
                "    ",
                "    # dill, not stdlib pickle: args may carry classes defined",
                "    # in the caller's __main__, which pickle stores only by name.",
                "    _argser = dill or cloudpickle",
                "    try:",
                "        func = _argser.loads(data['function'])",
                "    except Exception:",
                "        func = cloudpickle.loads(data['function']) if cloudpickle else None",
                "    ",
                "    args = _argser.loads(data['args'])",
                "    kwargs = _argser.loads(data['kwargs'])",
                "    ",
                "    result = func(*args, **kwargs)",
                "    ",
            ]
            + result_signing_lines()
            + [
                "    ",
                "except Exception as e:",
                "    _payload = {'error': str(e), 'traceback': traceback.format_exc()}",
                # Preserve the exception object so the caller catches the real
                # type, not a RuntimeError rebuilt from the message alone.
                "    _errser = dill or cloudpickle or pickle",
                "    try:",
                "        _blob = _errser.dumps(dict(_payload, exception=e), protocol=4)",
                "    except Exception:",
                "        _blob = pickle.dumps(_payload, protocol=4)",
                "    with open('error.pkl', 'wb') as f:",
                "        f.write(_blob)",
            ]
            # error.pkl is deserialized by the caller with dill, exactly like
            # result.pkl, so it gets the same tag. Without it a job only had
            # to fail to get its bytes unpickled unchecked.
            + payload_signing_lines("_blob", "error.pkl")
            + [
                "    raise",
                '"',
            ]
        )

    return script_lines


def _create_slurm_script(
    job_config: Dict[str, Any], remote_job_dir: str, config: ClusterConfig
) -> str:
    """Create SLURM job script."""

    # #SBATCH lines are read by SLURM itself, not by a shell, so a quoted
    # value would land in the partition name or the output path verbatim.
    # They are validated instead, and anything carrying shell syntax is
    # refused by config key -- `--partition=gpu --wrap='touch /tmp/pwn'` was
    # otherwise a working command injection into the submitted job.
    job_dir = validate_shell_fragment("remote_work_dir", remote_job_dir)
    script_lines = [
        "#!/bin/bash",
        "#SBATCH --job-name=clustrix",
        f"#SBATCH --output={job_dir}/slurm-%j.out",
        f"#SBATCH --error={job_dir}/slurm-%j.err",
        f"#SBATCH --cpus-per-task={validate_shell_fragment('cores', job_config['cores'])}",
        f"#SBATCH --mem="
        f"{validate_shell_fragment('memory', normalize_memory(job_config['memory'], 'slurm'))}",
        f"#SBATCH --time={validate_shell_fragment('time', job_config['time'])}",
    ]

    if job_config.get("partition"):
        partition = validate_shell_fragment("partition", job_config["partition"])
        script_lines.append(f"#SBATCH --partition={partition}")

    # Add environment setup
    script_lines.extend(environment_setup_lines(config))

    script_lines.extend(job_execution_lines(remote_job_dir, config))

    return "\n".join(script_lines)


def _create_pbs_script(
    job_config: Dict[str, Any], remote_job_dir: str, config: ClusterConfig
) -> str:
    """Create PBS job script."""

    # As for SLURM: #PBS directives are parsed by the scheduler, so these are
    # validated rather than quoted.
    job_dir = validate_shell_fragment("remote_work_dir", remote_job_dir)
    script_lines = [
        "#!/bin/bash",
        "#PBS -N clustrix",
        f"#PBS -o {job_dir}/job.out",
        f"#PBS -e {job_dir}/job.err",
        f"#PBS -l nodes=1:ppn={validate_shell_fragment('cores', job_config['cores'])}",
        f"#PBS -l mem="
        f"{validate_shell_fragment('memory', normalize_memory(job_config['memory'], 'pbs'))}",
        f"#PBS -l walltime={validate_shell_fragment('time', job_config['time'])}",
    ]

    if job_config.get("queue"):
        queue = validate_shell_fragment("queue", job_config["queue"])
        script_lines.append(f"#PBS -q {queue}")

    # Add environment setup
    script_lines.extend(environment_setup_lines(config))

    script_lines.extend(job_execution_lines(remote_job_dir, config))

    return "\n".join(script_lines)


def _create_sge_script(
    job_config: Dict[str, Any], remote_job_dir: str, config: ClusterConfig
) -> str:
    """Create SGE job script."""

    # As for SLURM: #$ directives are parsed by the scheduler, so these are
    # validated rather than quoted.
    job_dir = validate_shell_fragment("remote_work_dir", remote_job_dir)
    script_lines = [
        "#!/bin/bash",
        "#$ -N clustrix",
        f"#$ -o {job_dir}/job.out",
        f"#$ -e {job_dir}/job.err",
        f"#$ -pe smp {validate_shell_fragment('cores', job_config['cores'])}",
        f"#$ -l h_vmem="
        f"{validate_shell_fragment('memory', normalize_memory(job_config['memory'], 'sge'))}",
        f"#$ -l h_rt={validate_shell_fragment('time', job_config['time'])}",
        "#$ -cwd",
        "",
    ]

    # Add environment setup
    script_lines.extend(environment_setup_lines(config))

    script_lines.extend(job_execution_lines(remote_job_dir, config))

    return "\n".join(script_lines)


def _create_ssh_script(
    job_config: Dict[str, Any], remote_job_dir: str, config: ClusterConfig
) -> str:
    """Create simple execution script for SSH."""

    # Start with base script structure. `cd` takes a shell word, so the
    # directory is quoted here rather than validated.
    script_lines = [
        "#!/bin/bash",
        f"cd {shlex.quote(remote_job_dir)}",
        "",
    ]

    # Add environment setup (module loads, environment variables, pre-execution commands)
    setup_lines = environment_setup_lines(config)
    if setup_lines:
        script_lines.append("# Environment setup")
        script_lines.extend(setup_lines)
        script_lines.append("")

    # Check if we have two-venv setup
    script_lines.extend(job_execution_lines(remote_job_dir, config))

    return "\n".join(script_lines)


def detect_gpu_capabilities(
    ssh_client, config: Optional[ClusterConfig] = None
) -> Dict[str, Any]:
    """
    Detect GPU capabilities on remote cluster for job distribution.

    This function is designed to run in VENV1 and provides information
    needed for distributing GPU-enabled jobs across the cluster.

    Args:
        ssh_client: SSH client connection
        config: Cluster configuration

    Returns:
        Dictionary with GPU information including:
        - gpu_available: bool
        - gpu_count: int
        - gpu_devices: List[Dict] with device info
        - cuda_available: bool
        - cuda_version: str
        - pytorch_gpu_support: bool
        - tensorflow_gpu_support: bool
    """
    gpu_info: Dict[str, Any] = {
        "gpu_available": False,
        "gpu_count": 0,
        "gpu_devices": [],
        "cuda_available": False,
        "cuda_version": None,
        "pytorch_gpu_support": False,
        "tensorflow_gpu_support": False,
        "detection_method": "unknown",
        "detection_errors": [],
    }

    # Method 1: Try nvidia-smi (most reliable)
    try:
        stdin, stdout, stderr = ssh_client.exec_command(
            "nvidia-smi --query-gpu=index,name,memory.total,memory.free,compute_cap --format=csv,noheader,nounits 2>/dev/null"
        )
        exit_status = stdout.channel.recv_exit_status()

        if exit_status == 0:
            smi_output = stdout.read().decode().strip()
            if smi_output:
                gpu_info["gpu_available"] = True
                gpu_info["detection_method"] = "nvidia-smi"

                # Parse nvidia-smi output
                devices = []
                for line in smi_output.split("\n"):
                    if line.strip():
                        parts = [p.strip() for p in line.split(",")]
                        if len(parts) >= 5:
                            devices.append(
                                {
                                    "index": int(parts[0]),
                                    "name": parts[1],
                                    "memory_total_mb": int(parts[2]),
                                    "memory_free_mb": int(parts[3]),
                                    "compute_capability": parts[4],
                                }
                            )

                gpu_info["gpu_count"] = len(devices)
                gpu_info["gpu_devices"] = devices
    except Exception as e:
        gpu_info["detection_errors"].append(f"nvidia-smi failed: {str(e)}")

    # Method 2: Check CUDA installation
    try:
        stdin, stdout, stderr = ssh_client.exec_command(
            "nvcc --version 2>/dev/null | grep 'release' | sed 's/.*release \\([0-9.]*\\).*/\\1/'"
        )
        exit_status = stdout.channel.recv_exit_status()

        if exit_status == 0:
            cuda_version = stdout.read().decode().strip()
            if cuda_version:
                gpu_info["cuda_available"] = True
                gpu_info["cuda_version"] = cuda_version
    except Exception as e:
        gpu_info["detection_errors"].append(f"CUDA detection failed: {str(e)}")

    # Method 3: Check /proc/driver/nvidia if nvidia-smi fails
    if not gpu_info["gpu_available"]:
        try:
            stdin, stdout, stderr = ssh_client.exec_command(
                "ls -la /proc/driver/nvidia/gpus/ 2>/dev/null | wc -l"
            )
            exit_status = stdout.channel.recv_exit_status()

            if exit_status == 0:
                gpu_count_str = stdout.read().decode().strip()
                try:
                    # Subtract 2 for . and .. entries
                    gpu_count = max(0, int(gpu_count_str) - 2)
                    if gpu_count > 0:
                        gpu_info["gpu_available"] = True
                        gpu_info["gpu_count"] = gpu_count
                        gpu_info["detection_method"] = "/proc/driver/nvidia"
                except ValueError:
                    pass
        except Exception as e:
            gpu_info["detection_errors"].append(
                f"/proc/driver/nvidia detection failed: {str(e)}"
            )

    # Method 4: Check for GPU via lspci (fallback)
    if not gpu_info["gpu_available"]:
        try:
            stdin, stdout, stderr = ssh_client.exec_command(
                "lspci | grep -i nvidia | wc -l"
            )
            exit_status = stdout.channel.recv_exit_status()

            if exit_status == 0:
                nvidia_count_str = stdout.read().decode().strip()
                try:
                    nvidia_count = int(nvidia_count_str)
                    if nvidia_count > 0:
                        gpu_info["gpu_available"] = True
                        gpu_info["gpu_count"] = nvidia_count
                        gpu_info["detection_method"] = "lspci"
                except ValueError:
                    pass
        except Exception as e:
            gpu_info["detection_errors"].append(f"lspci detection failed: {str(e)}")

    return gpu_info


def setup_gpu_enabled_venv2(
    ssh_client,
    work_dir: str,
    requirements: Dict[str, str],
    gpu_info: Dict[str, Any],
    config: Optional[ClusterConfig] = None,
) -> Dict[str, Any]:
    """
    Setup VENV2 with GPU support based on remote cluster capabilities.

    This function ensures that VENV2 has appropriate GPU-enabled packages
    even if the local environment doesn't have GPU support.

    Args:
        ssh_client: SSH client connection
        work_dir: Remote working directory
        requirements: Package requirements from local environment
        gpu_info: GPU capabilities from detect_gpu_capabilities()
        config: Cluster configuration

    Returns:
        Dict with VENV2 setup information
    """
    if config is None:
        from .config import get_config

        config = get_config()

    venv2_info: Dict[str, Any] = {
        "gpu_packages_installed": False,
        "cuda_support_added": False,
        "pytorch_gpu_installed": False,
        "tensorflow_gpu_installed": False,
        "installation_errors": [],
    }

    # Only proceed if GPUs are available on remote cluster
    if not gpu_info.get("gpu_available", False):
        return venv2_info

    # Determine if we're using conda or venv
    conda_env2_name = f"clustrix_venv2_{work_dir.split('/')[-1]}"
    venv2_path = f"{work_dir}/venv2_execution"

    # Check if conda is available
    conda_available = False
    stdin, stdout, stderr = ssh_client.exec_command("conda --version 2>/dev/null")
    if "conda" in stdout.read().decode():
        conda_available = True

    commands = []

    # Install GPU-enabled packages based on what's in local requirements
    gpu_package_mapping = {
        "torch": {
            "conda": "pytorch torchvision torchaudio pytorch-cuda -c pytorch -c nvidia",
            "pip": "torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118",
        },
        "tensorflow": {"conda": "tensorflow-gpu", "pip": "tensorflow[and-cuda]"},
        "cupy": {
            "conda": "cupy",
            "pip": "cupy-cuda11x",  # Adjust based on CUDA version
        },
        "jax": {"conda": "jax", "pip": "jax[cuda]"},
    }

    # Check which GPU packages are in local requirements
    packages_to_install = []
    for local_pkg in requirements.keys():
        local_pkg_lower = local_pkg.lower()
        for gpu_pkg, install_info in gpu_package_mapping.items():
            if gpu_pkg in local_pkg_lower or local_pkg_lower.startswith(gpu_pkg):
                packages_to_install.append((gpu_pkg, install_info))
                break

    # Install GPU-enabled versions
    for gpu_pkg, install_info in packages_to_install:
        try:
            if conda_available:
                install_cmd = f"conda run -n {conda_env2_name} conda install {install_info['conda']} -y"
                commands.append(
                    f"{install_cmd} || echo 'Failed to install {gpu_pkg} via conda'"
                )
            else:
                commands.append(f"source {shlex.quote(venv2_path)}/bin/activate")
                install_cmd = f"pip install {install_info['pip']} --timeout=600"
                commands.append(
                    f"{install_cmd} || echo 'Failed to install {gpu_pkg} via pip'"
                )
                commands.append("deactivate")

            venv2_info["gpu_packages_installed"] = True

            if gpu_pkg == "torch":
                venv2_info["pytorch_gpu_installed"] = True
            elif gpu_pkg == "tensorflow":
                venv2_info["tensorflow_gpu_installed"] = True

        except Exception as e:
            venv2_info["installation_errors"].append(
                f"Failed to install {gpu_pkg}: {str(e)}"
            )

    # Install additional CUDA support packages if needed
    cuda_support_packages = ["numba", "cudf", "cuml", "cugraph"]  # Rapids ecosystem

    # Only install CUDA support packages if user had scientific computing packages
    has_scientific_packages = any(
        pkg in requirements for pkg in ["numpy", "scipy", "pandas", "scikit-learn"]
    )

    if has_scientific_packages and gpu_info.get("cuda_available", False):
        for cuda_pkg in cuda_support_packages:
            try:
                if conda_available:
                    # Use conda-forge for rapids packages
                    install_cmd = f"conda run -n {conda_env2_name} conda install {cuda_pkg} -c conda-forge -c rapidsai -y"
                    commands.append(
                        f"{install_cmd} || echo 'Failed to install {cuda_pkg} via conda'"
                    )
                else:
                    commands.append(f"source {shlex.quote(venv2_path)}/bin/activate")
                    install_cmd = f"pip install {cuda_pkg} --timeout=300"
                    commands.append(
                        f"{install_cmd} || echo 'Failed to install {cuda_pkg} via pip'"
                    )
                    commands.append("deactivate")

                venv2_info["cuda_support_added"] = True

            except Exception as e:
                venv2_info["installation_errors"].append(
                    f"Failed to install CUDA support package {cuda_pkg}: {str(e)}"
                )

    # Execute all GPU package installations
    if commands:
        full_command = " && ".join(commands)
        stdin, stdout, stderr = ssh_client.exec_command(full_command)
        exit_status = stdout.channel.recv_exit_status()

        if exit_status != 0:
            error_output = stderr.read().decode()
            venv2_info["installation_errors"].append(
                f"GPU package installation failed: {error_output}"
            )

    return venv2_info


def enhanced_setup_two_venv_environment(
    ssh_client,
    work_dir: str,
    requirements: Dict[str, str],
    config: Optional[ClusterConfig] = None,
) -> Dict[str, Any]:
    """
    Enhanced two-venv setup with automatic GPU detection and support.

    This function combines the original two-venv setup with GPU detection
    and GPU-enabled package installation as per user requirements.

    Args:
        ssh_client: SSH client connection
        work_dir: Remote working directory
        requirements: Package requirements from local environment
        config: Cluster configuration

    Returns:
        Dict containing both venv paths and GPU capabilities
    """
    if config is None:
        from .config import get_config

        config = get_config()

    # Step 1: Detect GPU capabilities (for VENV1 job distribution)
    print("Detecting GPU capabilities on remote cluster...")
    gpu_info = detect_gpu_capabilities(ssh_client, config)

    # Step 2: Setup basic two-venv environment
    print("Setting up two-venv environment...")
    venv_info = setup_two_venv_environment(ssh_client, work_dir, requirements, config)

    # Step 3: Enhanced VENV2 with GPU support if GPUs are available
    if gpu_info.get("gpu_available", False):
        print(
            f"GPU detected ({gpu_info['gpu_count']} devices), setting up GPU-enabled VENV2..."
        )
        gpu_venv2_info = setup_gpu_enabled_venv2(
            ssh_client, work_dir, requirements, gpu_info, config
        )
        venv_info.update(gpu_venv2_info)
    else:
        print("No GPUs detected, using standard VENV2 setup...")

    # Step 4: Add GPU detection results to venv_info
    venv_info["gpu_info"] = gpu_info

    return venv_info
