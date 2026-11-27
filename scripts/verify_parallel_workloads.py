#!/usr/bin/env python3
"""Run realistic workloads through @cluster and compare with a direct call.

Each workload is an ordinary function with a loop, written the way it would
be for a laptop. It is called directly, then through ``@cluster`` on each
backend named on the command line, and the two results must be equal. The
report says, per workload and backend, whether the loop was divided, into
how many processes, and how long it took.

    python scripts/verify_parallel_workloads.py local
    python scripts/verify_parallel_workloads.py local ssh slurm \
        > docs/evidence/parallel-workloads.txt

``ssh`` and ``slurm`` use the test cluster described by
``CLUSTRIX_TEST_SSH_HOST``/``_PORT``/``_USER``/``_KEY``
(``scripts/setup_local_slurm.sh`` creates one). Nothing here is simulated:
the jobs are real jobs.
"""

import logging
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

import clustrix
from clustrix import cluster, configure

RESOURCES = dict(memory="1GB", time="00:10:00")


# -- workloads ---------------------------------------------------------------


def hash_chain(seeds, length):
    """CPU-bound pure Python: one long integer recurrence per seed."""
    import os

    finals, pids = {}, set()
    for seed in seeds:
        value = seed
        for step in range(length):
            value = (value * 6364136223846793005 + step) % 18446744073709551557
        finals[seed] = value
        pids.add(os.getpid())
    return finals, len(pids)


def monte_carlo_pi(replicates, samples):
    """Embarrassingly parallel simulation, one seeded generator per replicate."""
    import os

    estimates, pids = [], set()
    for replicate in range(replicates):
        rng = np.random.default_rng(1000 + replicate)
        points = rng.random((samples, 2))
        estimates.append(4.0 * float(np.mean((points**2).sum(axis=1) <= 1.0)))
        pids.add(os.getpid())
    mean = sum(estimates) / len(estimates)
    return estimates, mean, len(pids)


def model_sweep(strengths):
    """A batch of models: fit one per hyperparameter value, keep the best."""
    import os

    from sklearn.datasets import make_classification
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_score

    features, labels = make_classification(
        n_samples=30000, n_features=60, n_informative=20, random_state=0
    )
    scores, pids = {}, set()
    for strength in strengths:
        model = LogisticRegression(C=strength, max_iter=600)
        scores[strength] = float(cross_val_score(model, features, labels, cv=3).mean())
        pids.add(os.getpid())
    best = max(scores, key=scores.get)
    return scores, best, len(pids)


def per_subject_statistics(folder):
    """File-per-subject processing: the inputs are files on the local disk."""
    import os

    statistics, pids = {}, set()
    for name in sorted(os.listdir(folder)):
        table = np.loadtxt(os.path.join(folder, name), delimiter=",")
        statistics[name] = (float(table.mean()), float(table.std()), table.shape)
        pids.add(os.getpid())
    return statistics, len(pids)


def running_balance(deposits):
    """NOT splittable: each iteration needs the balance the last one left."""
    balance, history = 100.0, []
    for deposit in deposits:
        balance = balance * 1.01 + deposit
        history.append(round(balance, 6))
    return history, 1


def deduplicate(records, seen):
    """Looks splittable, is not: ``seen`` is modified as the loop goes."""
    kept = []
    for record in records:
        if record not in seen:
            seen.add(record)
            kept.append(record)
    return kept, 1


def make_workloads(data_dir: Path):
    return [
        ("CPU-bound recurrence", hash_chain, (list(range(8)), 3_000_000), {}),
        ("Monte Carlo, seeded per replicate", monte_carlo_pi, (8, 8_000_000), {}),
        (
            "scikit-learn sweep over C",
            model_sweep,
            ([0.001, 0.01, 0.1, 1.0, 10.0, 100.0],),
            {},
        ),
        ("per-subject files (staged)", per_subject_statistics, (str(data_dir),), {}),
        ("running balance (must not split)", running_balance, ([5.0] * 12,), {}),
        (
            "set mutated in the loop (must fall back)",
            deduplicate,
            ([3, 1, 3, 2, 1, 4, 2, 5], set()),
            {},
        ),
    ]


# -- harness -----------------------------------------------------------------


def configure_backend(backend: str) -> None:
    if backend == "local":
        configure(cluster_type="local", cluster_host=None, default_cores=4)
        return
    configure(
        cluster_type=backend,
        cluster_host=os.environ["CLUSTRIX_TEST_SSH_HOST"],
        cluster_port=int(os.environ.get("CLUSTRIX_TEST_SSH_PORT", "22")),
        username=os.environ["CLUSTRIX_TEST_SSH_USER"],
        key_file=os.environ["CLUSTRIX_TEST_SSH_KEY"],
        ssh_host_key_policy="auto_add",
        remote_work_dir="~/clustrix-workload-evidence",
        replicate_local_environment=False,
        max_parallel_jobs=4,
        job_wait_timeout=900,
    )


def strip_process_count(result):
    """Everything but the trailing count of distinct pids, which may differ."""
    return result[:-1]


def main(backends) -> int:
    logging.basicConfig(level=logging.WARNING, format="    [%(name)s] %(message)s")
    workdir = Path(tempfile.mkdtemp(prefix="clustrix-workloads-"))
    os.chdir(workdir)
    data_dir = Path("subjects")
    data_dir.mkdir()
    rng = np.random.default_rng(7)
    for subject in range(6):
        np.savetxt(
            data_dir / f"sub-{subject:02d}.csv",
            rng.normal(size=(400, 6)),
            delimiter=",",
        )

    print(
        f"clustrix {clustrix.__version__}, Python {sys.version.split()[0]}, "
        f"{clustrix.loop_workers.available_cpus()} CPUs available here"
    )
    failures = 0
    for title, function, args, kwargs in make_workloads(data_dir):
        print(f"\n== {title}: {function.__name__}")
        for line in clustrix.explain(function).splitlines()[1:]:
            print("   " + line)

        fresh = lambda: tuple(  # noqa: E731 - arguments rebuilt per call
            set(value) if isinstance(value, set) else value for value in args
        )
        started = time.perf_counter()
        expected = function(*fresh(), **kwargs)
        direct_seconds = time.perf_counter() - started
        print(f"   direct call            {direct_seconds:6.2f}s")

        for backend in backends:
            configure_backend(backend)
            decorated = cluster(cores=4 if backend == "local" else 1, **RESOURCES)(
                function
            )
            # Twice: the first call of a function pays for starting workers
            # (or building the remote environment) and for importing its
            # libraries there; the second shows what a call costs after that.
            for attempt in ("first call", "again"):
                started = time.perf_counter()
                try:
                    result = decorated(*fresh(), **kwargs)
                except Exception as error:  # noqa: BLE001 - reported, then counted
                    failures += 1
                    print(f"   {backend:<6} FAILED: {type(error).__name__}: {error}")
                    break
                seconds = time.perf_counter() - started
                same = strip_process_count(result) == strip_process_count(expected)
                failures += not same
                print(
                    f"   {backend:<6} {attempt:<10} "
                    f"{'equal' if same else 'DIFFERENT':<9} {seconds:6.2f}s  "
                    f"in {result[-1]} process(es)"
                )
    print(f"\n{'ALL EQUAL' if not failures else f'{failures} PROBLEM(S)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["local"]))
