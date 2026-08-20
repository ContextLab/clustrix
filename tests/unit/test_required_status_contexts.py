"""A required status check must come from a workflow that always runs.

Branch protection on ``master`` requires two status contexts:

.. code-block:: console

   $ gh api repos/ContextLab/clustrix/branches/master/protection \\
       --jq '.required_status_checks.contexts'
   ["Tests Status","CI Status"]

That API is the authority; the list below is a copy of it, and the command
above is how to re-derive it. The copy exists because the API needs
credentials that CI does not have, and because the failure it guards against
is silent: GitHub does not treat an *absent* required check as passing. It
blocks the merge on "Expected -- Waiting for status to be reported",
indefinitely.

So a workflow that publishes a required context must fire on every pull
request. ``fast_ci.yml`` used to carry a ``paths:`` filter naming only
``clustrix/**``, ``tests/**`` and the packaging files, which meant a pull
request touching only ``docs/`` or ``README.md`` never triggered it, never
reported ``CI Status``, and could not be merged by anyone without an admin
override (#169).

This test cannot prove the merge succeeds -- only a real pull request against
the real protected branch can. What it can do is stop the filter coming back.
"""

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"

REQUIRED_CONTEXTS = ("Tests Status", "CI Status")

PROTECTED_BRANCHES = {"master", "main"}


def _triggers(document):
    """The ``on:`` mapping.

    YAML 1.1 reads a bare ``on`` as the boolean ``True``, so the key is not
    the string most readers expect.
    """
    return document[True] if True in document else document["on"]


def _workflows():
    for path in sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml")):
        yield path, yaml.safe_load(path.read_text())


def _publisher(context):
    """The (path, document, job) that publishes ``context``, or None."""
    for path, document in _workflows():
        for job_id, job in (document.get("jobs") or {}).items():
            if isinstance(job, dict) and job.get("name") == context:
                return path, document, job_id
    return None


@pytest.mark.parametrize("context", REQUIRED_CONTEXTS)
def test_required_context_is_published_by_some_workflow(context):
    found = _publisher(context)
    assert found is not None, (
        f"branch protection requires the status context {context!r}, but no "
        f"job in {WORKFLOWS} is named that, so nothing will ever report it"
    )


@pytest.mark.parametrize("context", REQUIRED_CONTEXTS)
def test_required_context_is_not_path_filtered(context):
    path, document, job_id = _publisher(context)
    pull_request = _triggers(document).get("pull_request")

    assert pull_request is not None, (
        f"{path.name} publishes the required context {context!r} but has no "
        "pull_request trigger, so it can never report on a pull request"
    )

    branches = set(pull_request.get("branches") or [])
    assert branches & PROTECTED_BRANCHES, (
        f"{path.name} publishes {context!r} but its pull_request trigger "
        f"names branches {sorted(branches)}, none of them protected"
    )

    for filter_key in ("paths", "paths-ignore"):
        assert filter_key not in pull_request, (
            f"{path.name} publishes the required status context {context!r} "
            f"from job {job_id!r}, but its pull_request trigger is filtered "
            f"by {filter_key}: {pull_request[filter_key]}. A pull request "
            "that touches none of those files never triggers the workflow, "
            "so the context is never reported, and GitHub blocks the merge "
            "forever waiting for it. See #169."
        )


# A job-level `if:` is the same trap one level down (#169, review RT-3).
#
# The trigger is only the first of two places a required check can be
# silenced. Even on a workflow that fires for every pull request, the job
# publishing the context runs only if its own `if:` evaluates true and its
# `needs:` let it start. `if: github.event_name == 'push'` on `status-check`
# leaves `fast_ci.yml` firing on every pull request and reporting `CI Status`
# on none of them -- and a skipped job is not a stuck one that anybody
# notices: GitHub counts a skipped required check as satisfied, so the gate
# stops gating and nothing goes red to say so. The tests above inspect only
# `on.pull_request`, so that mutation survived them all.

# Expressions that cannot evaluate false. GitHub accepts `if:` bare or
# wrapped in `${{ }}`, and YAML may hand either back as a string.
ALWAYS_RUNS = frozenset(
    {
        "always()",
        "${{ always() }}",
        "${{always()}}",
    }
)


@pytest.mark.parametrize("context", REQUIRED_CONTEXTS)
def test_required_context_job_cannot_be_conditionally_skipped(context):
    """The publishing job must run on every pull request, unconditionally."""
    path, document, job_id = _publisher(context)
    job = document["jobs"][job_id]
    condition = job.get("if")

    if condition is not None:
        assert str(condition).strip() in ALWAYS_RUNS, (
            f"{path.name} publishes the required status context {context!r} "
            f"from job {job_id!r}, which is guarded by `if: {condition}`. A "
            "required check is only required when it is reported: a pull "
            "request where that condition is false skips the job, GitHub "
            "counts the skip as satisfying branch protection, and the gate "
            "silently stops gating. The only condition allowed here is one "
            f"that cannot be false -- one of {sorted(ALWAYS_RUNS)}. See #169."
        )


@pytest.mark.parametrize("context", REQUIRED_CONTEXTS)
def test_required_context_job_still_reports_when_a_dependency_fails(context):
    """A gate with ``needs:`` and no ``always()`` skips itself on failure.

    Without ``if: always()`` a job whose dependency failed is skipped rather
    than run, so the context is never reported on exactly the pull requests
    that most need a verdict -- and the skip reads as a pass.
    """
    path, document, job_id = _publisher(context)
    job = document["jobs"][job_id]

    if job.get("needs"):
        assert str(job.get("if", "")).strip() in ALWAYS_RUNS, (
            f"{path.name}'s job {job_id!r} publishes {context!r} and depends "
            f"on {job['needs']}, but its `if:` is {job.get('if')!r}. A job "
            "whose dependency fails or is skipped does not run, so the "
            "required context goes unreported precisely when a job broke. "
            f"Use one of {sorted(ALWAYS_RUNS)}. See #169."
        )
