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
            assert "_ser = pickle" in block

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
        assert "source /remote/job/venv1_serialization/bin/activate" in text
        assert "/remote/job/venv2_execution/bin/python -c " in text

    def test_virtualenv_mode_deactivates_between_stages(self):
        lines = generate_two_venv_execution_commands(*PLAIN)
        assert lines.count("deactivate") == 2

    def test_conda_mode_does_not_emit_deactivate(self):
        """`conda run` does not leave an environment to deactivate."""
        assert "deactivate" not in generate_two_venv_execution_commands(*CONDA)
