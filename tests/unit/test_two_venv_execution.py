"""Tests for the three-stage remote execution script.

`generate_two_venv_execution_commands` emits bash that embeds three Python
programs as `python -c "..."` arguments. Two classes of bug live here and both
have shipped before:

* the Python does not compile, or compiles but cannot do what it is asked --
  the released version handed a dill-deserialized function to stdlib pickle,
  which cannot serialize a ``__main__`` function by name, so every realistic
  ``@cluster`` call failed;
* the Python is fine but the *shell* mangles it, because a double quote ends
  the argument early.

These tests run the real generator and check both.
"""

import pytest

from clustrix.utils import generate_two_venv_execution_commands


def _stages(*args, **kwargs):
    """Extract the three embedded Python programs from the generated bash."""
    lines = generate_two_venv_execution_commands(*args, **kwargs)
    blocks, current, inside = [], [], False
    for line in lines:
        if line.endswith('python -c "'):
            inside, current = True, []
            continue
        if inside and line == '"':
            blocks.append("\n".join(current))
            inside = False
            continue
        if inside:
            current.append(line)
    return blocks


CONDA = ("/remote/job", "clustrix_venv1_job", "clustrix_venv2_job")
PLAIN = ("/remote/job", None, None)


@pytest.mark.parametrize("args", [CONDA, PLAIN], ids=["conda", "virtualenv"])
class TestGeneratedStages:
    def test_there_are_exactly_three_stages(self, args):
        assert len(_stages(*args)) == 3

    def test_every_stage_is_valid_python(self, args):
        for i, block in enumerate(_stages(*args), 1):
            compile(block, f"<stage-{i}>", "exec")

    def test_no_stage_contains_a_double_quote(self, args):
        """A double quote would close the `python -c "` shell argument."""
        for i, block in enumerate(_stages(*args), 1):
            assert '"' not in block, f"stage {i} would break shell embedding"

    def test_handoffs_never_use_stdlib_pickle(self, args):
        """The cross-stage handoffs must go through dill/cloudpickle.

        stdlib pickle serializes a function by qualified name, which cannot be
        resolved in a fresh remote interpreter. This is the exact defect that
        made remote execution fail with "attribute lookup <func> on __main__
        failed".
        """
        for handoff in ("function_deserialized.pkl", "result_raw.pkl", "result.pkl"):
            for block in _stages(*args):
                if handoff not in block:
                    continue
                for line in block.splitlines():
                    if handoff in line or "_ser." in line:
                        continue
                    assert f"pickle.dump({handoff}" not in line

    def test_each_stage_selects_a_rich_serializer(self, args):
        for block in _stages(*args):
            assert "import dill as _ser" in block
            assert "import cloudpickle as _ser" in block

    def test_no_stage_degrades_to_stdlib_pickle(self, args):
        """Falling back to pickle was not a degradation, it was a second bug.

        Every payload these stages exchange is dill bytes, which stdlib pickle
        cannot read, so `_ser = pickle` produced an unrelated failure deep in
        the unpickler instead of naming the missing package (#121).
        """
        for block in _stages(*args):
            assert "_ser = pickle" not in block
            assert "pip install dill" in block

    def test_stages_do_not_clobber_each_other_error_file(self, args):
        """The first failure must survive the cascade it causes.

        Each stage writes its own error_<stage>.pkl unconditionally and only
        claims the shared error.pkl if no earlier stage did. Without this the
        caller is shown "result_raw.pkl not found" instead of the real cause.
        """
        for block in _stages(*args):
            if "except Exception as e:" not in block:
                continue
            assert "if not _os.path.exists('error.pkl'):" in block
            assert "error_venv" in block

    def test_every_stage_names_itself_in_its_error_payload(self, args):
        stages = _stages(*args)
        expected = ["venv1_deserialize", "venv2_execute", "venv1_serialize"]
        for block, name in zip(stages, expected):
            assert f"'stage': '{name}'" in block
            assert f"error_{name}.pkl" in block


class TestCondaVersusVirtualenv:
    def test_conda_mode_runs_every_stage_through_conda_run(self):
        lines = generate_two_venv_execution_commands(*CONDA)
        invocations = [line for line in lines if line.endswith('python -c "')]
        assert len(invocations) == 3
        assert all(line.startswith("conda run -n ") for line in invocations)

    def test_conda_mode_uses_venv1_for_stages_one_and_three(self):
        """Serialization happens in VENV1; only execution happens in VENV2."""
        lines = generate_two_venv_execution_commands(*CONDA)
        invocations = [line for line in lines if line.endswith('python -c "')]
        assert "clustrix_venv1_job" in invocations[0]
        assert "clustrix_venv2_job" in invocations[1]
        assert "clustrix_venv1_job" in invocations[2]

    def test_virtualenv_mode_activates_the_right_environments(self):
        lines = generate_two_venv_execution_commands(*PLAIN)
        text = "\n".join(lines)
        assert ". /remote/job/venv1_serialization/bin/activate" in text
        assert "/remote/job/venv2_execution/bin/python -c " in text

    def test_virtualenv_mode_deactivates_between_stages(self):
        lines = generate_two_venv_execution_commands(*PLAIN)
        assert lines.count("deactivate") == 2

    def test_conda_mode_does_not_emit_deactivate(self):
        """`conda run` does not leave an environment to deactivate."""
        assert "deactivate" not in generate_two_venv_execution_commands(*CONDA)


class TestEveryBackendRunsTheSameBody:
    """Remote backends used to each carry their own copy of the job body.

    PBS ended its script with ``python execute_function.py`` -- a file nothing
    in clustrix has ever created, so every PBS job died immediately. SGE
    carried its own copy of the single-venv script, which meant it silently
    missed the two-venv path, the conda sourcing and the result signing as
    those were fixed on the SLURM one. Both backends have since been removed
    (issues #140, #141) for never having been run against real hardware, so
    the invariant is asserted over the two remote backends that remain: only
    the directive header may differ between them.
    """

    SCHEDULERS = ("slurm", "ssh")

    def _script(self, scheduler, *, two_venv):
        from clustrix.config import ClusterConfig
        from clustrix.utils import create_job_script

        config = ClusterConfig(cluster_type=scheduler)
        if two_venv:
            config.venv_info = {
                "conda_env1_name": "e1",
                "conda_env2_name": "e2",
                "conda_setup_prefix": ". /opt/conda/etc/profile.d/conda.sh",
            }
        return create_job_script(
            scheduler,
            {"cores": 2, "memory": "8GB", "time": "01:00:00"},
            "/remote/job",
            config,
        )

    @pytest.mark.parametrize("scheduler", SCHEDULERS)
    def test_no_backend_runs_a_file_that_is_never_created(self, scheduler):
        assert "execute_function.py" not in self._script(scheduler, two_venv=False)

    @pytest.mark.parametrize("scheduler", SCHEDULERS)
    def test_every_backend_reads_the_function_payload(self, scheduler):
        assert "function_data.pkl" in self._script(scheduler, two_venv=False)

    @pytest.mark.parametrize("scheduler", SCHEDULERS)
    def test_every_backend_uses_the_two_venv_path_when_available(self, scheduler):
        script = self._script(scheduler, two_venv=True)
        assert "VENV1" in script
        assert "conda run -n e1" in script
        assert "conda run -n e2" in script

    @pytest.mark.parametrize("scheduler", SCHEDULERS)
    def test_every_backend_sources_conda(self, scheduler):
        script = self._script(scheduler, two_venv=True)
        assert ". /opt/conda/etc/profile.d/conda.sh" in script

    @pytest.mark.parametrize("scheduler", SCHEDULERS)
    def test_every_backend_signs_its_result(self, scheduler):
        script = self._script(scheduler, two_venv=True)
        assert "result.pkl.hmac" in script
        assert "CLUSTRIX_RESULT_KEY" in script

    def test_the_bodies_are_identical_across_backends(self):
        """Only the directive header should differ between backends.

        The slice starts at the result-key export rather than at the first
        `cd`. The SSH script emits its own `cd <job dir>` before the shared
        block, so the shared block itself begins one line later; comparing
        from the first `cd` would compare SSH's redundant one against SLURM's
        and report a difference that is not in the executed body. Everything
        that actually runs the function -- venv activation, both `python -c`
        programs, the signing step -- is inside the compared region.
        """
        bodies = {}
        for scheduler in self.SCHEDULERS:
            script = self._script(scheduler, two_venv=True)
            body = script[script.index("export CLUSTRIX_RESULT_KEY") :]
            bodies[scheduler] = body
        assert bodies["slurm"] == bodies["ssh"]
