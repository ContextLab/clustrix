"""The message for a job that was stopped rather than raising.

The log these read is real: a real bash script runs a real ``python -c``
program that kills itself, and bash's own report of that -- the whole
command, echoed -- is what is condensed. Stopping a real SLURM and a real
ssh job is ``tests/real_world/test_stopped_jobs_real.py``.
"""

import shutil
import subprocess
import sys

import pytest

from clustrix.executor_scheduler_status import (
    ERROR_LOG_LINE_WIDTH,
    ERROR_LOG_TAIL_LINES,
    JobFailedError,
    condense_error_log,
)

PROGRAM_LINES = 60


@pytest.fixture(scope="module")
def killed_job_log(tmp_path_factory):
    """stderr of a bash script whose multi-line ``python -c`` child is killed."""
    if shutil.which("bash") is None:
        pytest.skip("needs bash, which is what a job script is run by")
    filler = "\n".join(f"filler_{n} = {n}" for n in range(PROGRAM_LINES))
    script = tmp_path_factory.mktemp("job") / "job.sh"
    script.write_text(
        "#!/bin/bash\n"
        "echo 'starting the long part' >&2\n"
        f'{sys.executable} -c "\n'
        "import os, signal\n"
        f"{filler}\n"
        "os.kill(os.getpid(), signal.SIGKILL)\n"
        '"\n'
        "exit $?\n"
    )
    completed = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, timeout=60
    )
    assert completed.returncode == 137
    return completed.stderr


def test_bash_really_does_echo_the_whole_program(killed_job_log):
    """The premise. If bash stops doing this, the folding has nothing to do."""
    assert "Killed" in killed_job_log
    assert f"filler_{PROGRAM_LINES - 1} = {PROGRAM_LINES - 1}" in killed_job_log
    assert len(killed_job_log.splitlines()) > PROGRAM_LINES


def test_the_echoed_program_is_folded_into_the_line_that_reports_the_kill(
    killed_job_log,
):
    condensed = condense_error_log(killed_job_log)
    print("\n".join(condensed))

    assert condensed[0] == "starting the long part"
    assert len(condensed) == 2
    assert "Killed" in condensed[1]
    assert condensed[1].endswith(f'-c "<the job program, {PROGRAM_LINES + 2} lines>"')
    assert "filler_" not in "\n".join(condensed)


def test_the_message_is_short_and_the_whole_log_stays_on_the_exception(
    killed_job_log,
):
    error = JobFailedError(
        job_id="ssh_1",
        outcome="failed",
        reason=" (exit status 137: killed by SIGKILL)",
        remote_dir="/scratch/me/job_1",
        host="login.example.edu",
        error_log=killed_job_log,
    )
    message = str(error)
    print(message)

    assert isinstance(error, RuntimeError)
    assert message.splitlines()[0] == (
        "Job ssh_1 failed (exit status 137: killed by SIGKILL)."
    )
    assert "/scratch/me/job_1 on login.example.edu" in message
    assert len(message.splitlines()) == 5
    assert ".error_log" in message
    assert error.error_log == killed_job_log
    assert (error.job_id, error.remote_dir) == ("ssh_1", "/scratch/me/job_1")


def test_what_the_job_itself_wrote_is_not_folded():
    """A line the job printed that merely ends like a command stays as it is
    when nothing closes it -- only a complete echo is folded."""
    log = 'the job printed: run with -c "\nand then carried on\n'

    assert condense_error_log(log) == [
        'the job printed: run with -c "',
        "and then carried on",
    ]


def test_only_the_end_of_a_long_log_is_quoted_and_long_lines_are_cut():
    log = "\n".join(f"line {n}" for n in range(200)) + "\n" + "x" * 5000

    condensed = condense_error_log(log)

    assert len(condensed) == ERROR_LOG_TAIL_LINES
    assert condensed[0] == f"line {200 - ERROR_LOG_TAIL_LINES + 1}"
    assert condensed[-1] == "x" * ERROR_LOG_LINE_WIDTH + " ..."


def test_a_short_log_is_quoted_whole_and_says_nothing_about_an_attribute():
    error = JobFailedError("7", "was cancelled", "", None, None, "one\ntwo\n")

    assert str(error) == "Job 7 was cancelled.\nIts error log:\n  one\n  two"


def test_no_log_at_all_is_one_line():
    error = JobFailedError("7", "failed", "", None, None, "")

    assert str(error) == "Job 7 failed."
