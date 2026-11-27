"""What a job is called in the scheduler's queue.

Every SLURM job used to be ``clustrix``. These build the real things -- a
real function, a real split made by ``clustrix.parallelize``, the real job
script -- and read the name off them. The scheduler's own view of it is
``tests/real_world/test_job_names_real.py``.
"""

import re

import pytest

from clustrix import parallelize
from clustrix.config import ClusterConfig
from clustrix.utils import (
    DEFAULT_JOB_NAME,
    MAX_JOB_NAME_LENGTH,
    create_job_script,
    scheduler_job_name,
    serialize_function,
)


def simulate_market(values):
    out = []
    for value in values:
        out.append(value * 2)
    return out


def test_a_job_is_named_after_its_function():
    assert scheduler_job_name(simulate_market) == "simulate_market"
    assert (
        serialize_function(simulate_market, ([1],), {})["func_info"]["job_name"]
        == "simulate_market"
    )


def test_each_piece_of_a_divided_loop_names_the_function_it_came_from():
    """A real split: ``pre``, ``body``, ``post`` and ``rest`` are functions
    clustrix generated, and none of them is called ``simulate_market``."""
    plan = parallelize.plan(simulate_market)
    assert plan.parallelizable, plan.explain()
    split = parallelize.bind(plan, simulate_market)

    names = {
        part: scheduler_job_name(getattr(split, part))
        for part in ("pre", "body", "post", "rest")
    }
    print(names)

    assert names == {
        "pre": "simulate_market.pre",
        "body": "simulate_market.body",
        "post": "simulate_market.post",
        "rest": "simulate_market.rest",
    }
    env = split.pre(parallelize.bound_arguments(simulate_market, ([1, 2],), {}))
    shipped = serialize_function(split.body, (env, [1, 2]), {})
    assert shipped["func_info"]["job_name"] == "simulate_market.body"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("<lambda>", "lambda"),
        ("run; rm -rf ~", "run_rm_-rf"),
        ("naïve analysis", "na_ve_analysis"),
        ("a\nb", "a_b"),
        ("__init__", "init"),
        ("", DEFAULT_JOB_NAME),
        ("$(reboot)", "reboot"),
        ("---", DEFAULT_JOB_NAME),
    ],
)
def test_a_name_is_reduced_to_what_a_scheduler_directive_can_hold(raw, expected):
    def job():
        return None

    job.__name__ = raw

    name = scheduler_job_name(job)

    assert name == expected
    assert re.fullmatch(r"[A-Za-z0-9_.-]+", name)


def test_a_long_name_is_cut():
    def job():
        return None

    job.__name__ = "x" * 300

    assert scheduler_job_name(job) == "x" * MAX_JOB_NAME_LENGTH


def test_something_that_is_not_a_function_still_gets_a_name():
    class Callable_:
        def __call__(self):
            return None

    assert scheduler_job_name(Callable_()) == DEFAULT_JOB_NAME


def _directive(script: str) -> str:
    (line,) = [ln for ln in script.splitlines() if "--job-name" in ln]
    return line


def test_the_name_is_the_job_scripts_job_name_directive():
    job_config = {"cores": 1, "memory": "1GB", "time": "00:05:00"}
    config = ClusterConfig(cluster_type="slurm", cluster_host="login.example.edu")

    named = create_job_script(
        "slurm", job_config, "/scratch/job_1", config, job_name="simulate_market.body"
    )
    unnamed = create_job_script("slurm", job_config, "/scratch/job_1", config)

    assert _directive(named) == "#SBATCH --job-name=simulate_market.body"
    assert _directive(unnamed) == f"#SBATCH --job-name={DEFAULT_JOB_NAME}"


def test_a_name_that_would_break_the_directive_is_refused():
    job_config = {"cores": 1, "memory": "1GB", "time": "00:05:00"}
    config = ClusterConfig(cluster_type="slurm", cluster_host="login.example.edu")

    with pytest.raises(ValueError, match="job name"):
        create_job_script(
            "slurm", job_config, "/scratch/job_1", config, job_name="a\n#SBATCH -w x"
        )
