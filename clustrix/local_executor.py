"""The ``cluster_type = "local"`` backend, and what counts as a worker count.

Dividing a loop among local worker processes is not done here: that is
``clustrix.parallelize`` (the split) and ``clustrix.loop_workers`` (the
processes).
"""

import secrets
import time
import traceback
from typing import Any, Dict


def is_worker_count(value: object) -> bool:
    """Whether ``value`` can be a number of workers.

    ``bool`` is excluded deliberately. It subclasses ``int``, so
    ``isinstance(True, int)`` is true: ``@cluster(cores=True)`` was accepted
    and quietly read as a request for one worker, while ``False`` was
    rejected as "not a positive integer" -- a message that is confusing for
    ``True``, which is not a positive integer in any sense the caller means
    (#152).

    ``None`` ("use the configured default") and ``-1`` ("every CPU this
    process may use", local execution only) are not counts; ``@cluster``
    checks for them itself.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


class LocalJobManager:
    """Runs jobs on the submitting machine, for ``cluster_type = "local"``.

    "local" is offered in the notebook widget's cluster-type dropdown and
    documented as a cluster type, but ``ClusterExecutor.submit_job`` had no
    branch for it and raised ``ValueError: Unsupported cluster type: local``
    (#120). This gives it one.

    Execution is synchronous: ``submit_job`` runs the function and records
    its outcome, and ``wait_for_result`` hands back that outcome. There is no
    scheduler to hand the work to and nothing to poll, so pretending otherwise
    would only add a thread whose result nobody could cancel anyway.
    """

    def __init__(self, config):
        """Initialize the local job manager.

        Args:
            config: ClusterConfig instance (unused for execution; kept so this
                manager has the same shape as the other backend managers)
        """
        self.config = config
        self.active_jobs: Dict[str, Any] = {}

    def submit_job(self, func_data: Dict[str, Any], job_config: Dict[str, Any]) -> str:
        """Run a serialized job here and now, returning its job ID."""
        from .utils import deserialize_function

        job_id = f"local_{int(time.time())}_{secrets.token_hex(4)}"
        func, args, kwargs = deserialize_function(func_data)

        record: Dict[str, Any] = {
            "status": "running",
            "submit_time": time.time(),
        }
        self.active_jobs[job_id] = record

        # No pool here. One deserialized call is one unit of work, so a pool
        # has nothing to distribute; constructing one and then not using it
        # is what once made ``cores`` look honoured (#152). ``@cluster``
        # warns when a caller asked for cores>1 on this route.
        try:
            record["result"] = func(*args, **kwargs)
            record["status"] = "completed"
        except Exception as e:
            record["error"] = e
            record["traceback"] = traceback.format_exc()
            record["status"] = "failed"

        return job_id

    def get_job_status(self, job_id: str) -> str:
        """Return the recorded status of a local job."""
        record = self.active_jobs.get(job_id)
        if record is None:
            raise ValueError(f"Unknown local job ID: {job_id}")
        return record["status"]

    def wait_for_result(self, job_id: str) -> Any:
        """Return a local job's result, or re-raise the exception it hit."""
        record = self.active_jobs.pop(job_id, None)
        if record is None:
            raise ValueError(f"Unknown local job ID: {job_id}")

        if record["status"] == "failed":
            raise record["error"]
        return record["result"]

    def get_error_log(self, job_id: str) -> str:
        """Return the traceback of a failed local job."""
        record = self.active_jobs.get(job_id)
        if record is None:
            raise ValueError(f"Unknown local job ID: {job_id}")
        return record.get("traceback", "")

    def cancel_job(self, job_id: str):
        """Local jobs cannot be cancelled: they have already run.

        Reporting a successful cancellation here would be a lie -- the work
        was done, and any side effects it had have already happened.
        """
        if job_id not in self.active_jobs:
            raise ValueError(f"Unknown local job ID: {job_id}")
        raise RuntimeError(
            f"Local job {job_id} cannot be cancelled: cluster_type 'local' "
            "runs the function on this machine during submission, so it has "
            "already finished by the time a cancellation could be requested."
        )
