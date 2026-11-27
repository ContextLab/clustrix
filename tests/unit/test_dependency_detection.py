"""What a job needs is worked out from the job, not listed by the user.

Three things are found without being declared, and each is checked here
against real payloads and real installed metadata:

* the installed packages a payload will import when the worker loads it,
  read off the pickle bytes themselves (``_payload_import_names``);
* the modules imported by ``import`` statements inside function bodies,
  which leave no trace in globals or in the pickle (``_body_imports``);
* the distributions behind those names, closed over their dependencies
  (``required_distributions``).

The last class ships a project-local module that is imported only inside a
function body and runs the function in an interpreter that cannot import it,
which is the only way to tell "made importable on the worker" from "happened
to be importable here".
"""

import base64
import importlib.metadata
import json
import pathlib
import subprocess
import sys
import textwrap

import cloudpickle
import dill
import pytest

from clustrix.utils import (
    _body_imports,
    _payload_import_names,
    local_module_registration_lines,
    required_distributions,
    serialize_function,
)

LOCAL_PROJECT = pathlib.Path(__file__).parent / "localproject"
if str(LOCAL_PROJECT) not in sys.path:
    sys.path.insert(0, str(LOCAL_PROJECT))

from mypkg.mathutils import Widget  # noqa: E402


def uses_json_as_a_module_value(text):
    return json.loads(text)


def uses_two_modules_as_values(text):
    return json.loads(text), textwrap.dedent(text)


def imports_in_the_body(x):
    import fractions

    return fractions.Fraction(x, 3)


def unrelated_neighbour():
    import wave  # noqa: F401  -- must not be attributed to its neighbours

    return None


def calls_a_helper_that_imports(x):
    return imports_in_the_body(x)


def imports_a_local_module_in_the_body(x):
    from lazypkg.deep import nine_times

    return nine_times(x)


class TestPayloadImportNames:
    """The pickle says which modules it will import; nothing is guessed."""

    @pytest.mark.parametrize("dumps", [cloudpickle.dumps, dill.dumps])
    def test_a_module_held_as_a_value_is_named(self, dumps):
        # Pickled by value so the module reference is inside the payload,
        # exactly as it is for a function defined in the user's __main__.
        cloudpickle.register_pickle_by_value(sys.modules[__name__])
        try:
            payload = dumps(uses_json_as_a_module_value, protocol=4)
        finally:
            cloudpickle.unregister_pickle_by_value(sys.modules[__name__])
        if dumps is dill.dumps:
            payload = dill.dumps(json, protocol=4)
        assert "json" in _payload_import_names(payload)

    def test_the_second_module_is_found_through_the_memo(self):
        """The import reducer is written once and fetched from the memo after.

        A scan that only recognised it where it is spelled out found the
        first module a function used and missed every other one -- numpy was
        dropped from a job that also used humanize.
        """
        cloudpickle.register_pickle_by_value(sys.modules[__name__])
        try:
            payload = cloudpickle.dumps(uses_two_modules_as_values, protocol=4)
        finally:
            cloudpickle.unregister_pickle_by_value(sys.modules[__name__])
        assert {"json", "textwrap"} <= _payload_import_names(payload)

    def test_an_object_stored_by_reference_names_its_module(self):
        payload = dill.dumps(importlib.metadata.version, protocol=4)
        assert "importlib" in _payload_import_names(payload)

    def test_plain_data_imports_nothing(self):
        assert _payload_import_names(dill.dumps({"a": [1, 2.5, "x"]}, protocol=4)) == (
            set()
        )

    def test_scanning_does_not_execute_the_payload(self, tmp_path):
        marker = tmp_path / "ran"

        class Boom:
            def __reduce__(self):
                return (pathlib.Path.touch, (marker,))

        _payload_import_names(dill.dumps(Boom(), protocol=4))
        assert not marker.exists()


class TestBodyImports:
    def test_an_import_inside_the_body_is_found(self):
        assert "fractions" in _body_imports(imports_in_the_body)

    def test_it_is_found_through_a_helper_the_function_calls(self):
        assert "fractions" in _body_imports(calls_a_helper_that_imports)

    def test_a_neighbouring_function_is_not_this_jobs_dependency(self):
        """Everything else in the user's script is not what this job runs."""
        assert "wave" not in _body_imports(imports_in_the_body)
        assert "wave" not in _body_imports(calls_a_helper_that_imports)

    def test_arguments_are_looked_at_one_level_deep_only(self):
        big = list(range(200_000))
        assert _body_imports((big, {"k": big})) == set()


class TestRequiredDistributions:
    def test_an_import_name_maps_to_its_distribution_and_local_version(self):
        required = required_distributions({"paramiko"})
        assert required["paramiko"] == importlib.metadata.version("paramiko")

    def test_declared_dependencies_come_along(self):
        required = {name.lower() for name in required_distributions({"paramiko"})}
        assert "cryptography" in required

    def test_optional_extras_are_not_followed(self, tmp_path, monkeypatch):
        """``pytest; extra == 'tests'`` is not a dependency of the package.

        Following those made one small library drag in the test and
        documentation stack of everything it was ever tested with. The three
        distributions here are real metadata on a real ``sys.path`` entry,
        so the lookup is the one a real environment gets.
        """

        def install(name, requires=()):
            info = tmp_path / f"{name}-1.0.dist-info"
            info.mkdir()
            lines = ["Metadata-Version: 2.1", f"Name: {name}", "Version: 1.0"]
            lines += [f"Requires-Dist: {requirement}" for requirement in requires]
            (info / "METADATA").write_text("\n".join(lines) + "\n")
            (info / "top_level.txt").write_text(name + "\n")
            (tmp_path / f"{name}.py").write_text("")

        install(
            "clxdemo_main",
            ["clxdemo_plain>=1", "clxdemo_testsonly; extra == 'tests'"],
        )
        install("clxdemo_plain")
        install("clxdemo_testsonly")
        monkeypatch.syspath_prepend(str(tmp_path))

        assert required_distributions({"clxdemo_main"}) == {
            "clxdemo_main": "1.0",
            "clxdemo_plain": "1.0",
        }

    def test_the_standard_library_and_unknown_names_need_nothing(self):
        assert required_distributions({"json", "os", "no_such_module_anywhere"}) == {}

    def test_clustrix_itself_is_never_a_requirement(self):
        assert "clustrix" not in {
            name.lower() for name in required_distributions({"clustrix"})
        }


class TestSerializeFunctionReportsWhatTheJobNeeds:
    def test_a_body_import_of_an_installed_package_is_required(self):
        def job():
            import paramiko

            return paramiko.__version__

        data = serialize_function(job, (), {})
        assert "paramiko" in data["required_packages"]

    def test_a_function_that_uses_nothing_requires_only_the_serializer(self):
        def job(x):
            return x + 1

        required = {
            name.lower()
            for name in serialize_function(job, (1,), {})["required_packages"]
        }
        assert required <= {"dill", "cloudpickle"}

    def test_the_whole_environment_is_still_reported_separately(self):
        def job(x):
            return x + 1

        data = serialize_function(job, (1,), {})
        assert len(data["requirements"]) > len(data["required_packages"])


class TestLazilyImportedLocalModulesTravel:
    def test_the_module_is_sent_as_its_own_payload(self):
        data = serialize_function(imports_a_local_module_in_the_body, (2,), {})
        assert data["local_modules"], "the lazily imported module was left behind"
        modules = dill.loads(data["local_modules"])
        assert {"lazypkg", "lazypkg.deep"} <= set(modules)

    def test_a_function_with_no_lazy_local_import_sends_none(self):
        assert (
            serialize_function(imports_in_the_body, (1,), {})["local_modules"] is None
        )

    def test_the_worker_lines_make_the_import_work_where_the_module_is_absent(self):
        """Run the shipped worker lines in an interpreter that has no lazypkg."""
        data = serialize_function(imports_a_local_module_in_the_body, (2,), {})
        program = "\n".join(
            [
                "import base64, sys",
                "import dill as _ser",
                "data = {k: base64.b64decode(v) for k, v in zip("
                "('function', 'args', 'local_modules'), sys.argv[1:4])}",
                "if True:",
            ]
            + local_module_registration_lines("data.get('local_modules')")
            + [
                "    func = _ser.loads(data['function'])",
                "    print(func(*_ser.loads(data['args'])))",
            ]
        )
        result = subprocess.run(
            [sys.executable, "-c", program]
            + [
                base64.b64encode(data[key]).decode()
                for key in ("function", "args", "local_modules")
            ],
            capture_output=True,
            text=True,
            cwd="/",
            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "18"

    def test_without_those_lines_the_same_job_fails_on_the_import(self):
        """The control: this is a failure the registration is what prevents."""
        data = serialize_function(imports_a_local_module_in_the_body, (2,), {})
        program = textwrap.dedent("""
            import base64, sys, dill
            func = dill.loads(base64.b64decode(sys.argv[1]))
            print(func(*dill.loads(base64.b64decode(sys.argv[2]))))
            """)
        result = subprocess.run(
            [sys.executable, "-c", program]
            + [base64.b64encode(data[key]).decode() for key in ("function", "args")],
            capture_output=True,
            text=True,
            cwd="/",
            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
        )
        assert result.returncode != 0
        assert "No module named 'lazypkg'" in result.stderr


def returns_local_instances(n):
    # Widget is a module-level name here, so it travels embedded by value --
    # the common case, and the one where the worker has no module to refer
    # the class back to.
    return [Widget(n), {"nested": Widget(n + 1)}]


class LocalJobError(Exception):
    pass


def raises_a_local_exception():
    try:
        raise LocalJobError("from the worker")
    except LocalJobError as exc:
        return exc


def _run_on_a_worker_without_the_project(func):
    """Run ``func`` where none of this project is importable; return dill bytes."""
    data = serialize_function(func, (3,) if func is returns_local_instances else (), {})
    program = "\n".join(
        [
            "import base64, sys",
            "import dill as _ser",
            "data = {k: base64.b64decode(v) if v else None for k, v in zip("
            "('function', 'args', 'local_modules'), sys.argv[1:4])}",
            "if True:",
        ]
        + local_module_registration_lines("data.get('local_modules')")
        + [
            "    func = _ser.loads(data['function'])",
            "    out = func(*_ser.loads(data['args']))",
            "    sys.stdout.write(base64.b64encode(_ser.dumps(out, protocol=4)).decode())",
        ]
    )
    result = subprocess.run(
        [sys.executable, "-c", program]
        + [
            base64.b64encode(data[key]).decode() if data[key] else ""
            for key in ("function", "args", "local_modules")
        ],
        capture_output=True,
        text=True,
        cwd="/",
        env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
    )
    assert result.returncode == 0, result.stderr
    return base64.b64decode(result.stdout)


class TestWhatComesBackIsTheCallersOwnClass:
    """A class sent to the worker by value comes back as the one sent."""

    def test_plain_dill_builds_a_stranger(self):
        """The control: this is the defect, shown with the stock loader."""
        payload = _run_on_a_worker_without_the_project(returns_local_instances)
        first = dill.loads(payload)[0]
        assert type(first).__name__ == "Widget"
        assert not isinstance(first, Widget)

    def test_instances_at_any_depth_are_the_callers_class(self):
        from clustrix.utils import loads_remote_payload

        payload = _run_on_a_worker_without_the_project(returns_local_instances)
        first, holder = loads_remote_payload(payload)
        assert isinstance(first, Widget)
        assert isinstance(holder["nested"], Widget)
        assert first.value() == Widget(3).value()

    def test_a_remote_exception_is_caught_by_the_callers_except_clause(self):
        from clustrix.utils import loads_remote_payload

        payload = _run_on_a_worker_without_the_project(raises_a_local_exception)
        with pytest.raises(LocalJobError, match="from the worker"):
            raise loads_remote_payload(payload)

    def test_a_class_this_process_does_not_have_is_built_as_sent(self):
        from clustrix.utils import loads_remote_payload

        def make():
            class OnlyThere:
                marker = 41

            return OnlyThere()

        cloudpickle.register_pickle_by_value(sys.modules[__name__])
        try:
            payload = dill.dumps(make(), protocol=4)
        finally:
            cloudpickle.unregister_pickle_by_value(sys.modules[__name__])
        assert loads_remote_payload(payload).marker == 41


def reads_a_package(package):
    with open(package.path("notes/readme.txt")) as handle:
        return handle.read(), sorted(package.filenames()), package.path("data.bin")


class TestADataPackageWorksWhereClustrixIsNotInstalled:
    """A worker has dill and the user's packages. It does not have clustrix.

    A ``DataPackage`` is an argument, and the function calls methods on it,
    so its class has to arrive with the job. Passing one to an ssh or slurm
    job used to fail on ``ModuleNotFoundError: No module named 'clustrix'``.
    The subprocess below makes ``import clustrix`` fail the way it does on a
    worker (``sys.modules['clustrix'] = None`` is Python's own spelling of
    "this module cannot be imported") and runs the function there.
    """

    def test_an_inline_package_is_read_on_a_machine_without_clustrix(self, tmp_path):
        import clustrix

        source = tmp_path / "source"
        (source / "notes").mkdir(parents=True)
        (source / "notes" / "readme.txt").write_text("declared, so it travelled\n")
        (source / "data.bin").write_bytes(bytes(range(256)) * 64)
        package = clustrix.data_package(str(source), force_local=True)

        data = serialize_function(reads_a_package, (package,), {})
        workdir = tmp_path / "jobdir"
        workdir.mkdir()
        program = textwrap.dedent("""
            import base64, sys
            sys.modules['clustrix'] = None
            import dill
            func = dill.loads(base64.b64decode(sys.argv[1]))
            text, names, binary = func(*dill.loads(base64.b64decode(sys.argv[2])))
            print(text.strip()); print(names); print(binary)
            print(open(binary, 'rb').read() == bytes(range(256)) * 64)
            """)
        # The original files are removed first: a package dereferenced where
        # its sources still exist reads them in place, which would prove
        # nothing about what travelled.
        import shutil

        shutil.rmtree(source)
        result = subprocess.run(
            [sys.executable, "-c", program]
            + [base64.b64encode(data[key]).decode() for key in ("function", "args")],
            capture_output=True,
            text=True,
            cwd=str(workdir),
            env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home")},
        )
        assert result.returncode == 0, result.stderr
        text, names, binary, intact = result.stdout.strip().splitlines()
        assert text == "declared, so it travelled"
        assert names == "['data.bin', 'notes/readme.txt']"
        # Unpacked beside the job, so cleanup_on_success removes it with the
        # job directory instead of leaving it in a cache on the cluster.
        assert pathlib.Path(binary).is_relative_to(workdir)
        assert intact == "True"
