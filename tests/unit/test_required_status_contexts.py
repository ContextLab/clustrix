"""A required status check must come from a workflow that always reports it.

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
indefinitely. Worse, it *does* treat a **skipped** required check as passing,
so a job that silently stops running turns the gate off with nothing going
red to say so.

``fast_ci.yml`` used to carry a ``paths:`` filter naming only ``clustrix/**``,
``tests/**`` and the packaging files, which meant a pull request touching only
``docs/`` or ``README.md`` never triggered it, never reported ``CI Status``,
and could not be merged by anyone without an admin override (#169).

WHAT THIS GUARD CLAIMS, EXACTLY:

    That for each context in ``REQUIRED_CONTEXTS``, **every** workflow
    document under ``.github/workflows`` that could publish it is arranged so
    that the job publishing it starts, and finishes, on every pull request
    against a protected branch -- as far as a YAML document can settle that.
    Concretely: the workflow fires on ``pull_request`` for a protected branch
    with no ``paths``/``paths-ignore`` filter and no ``types`` narrower than
    the default; the job is not conditioned on anything that can be false,
    not made advisory with ``continue-on-error``, not fanned out by a
    ``strategy: matrix`` (which renames the context), and not delegated to a
    reusable workflow (which also renames it); and no step of it can be
    skipped or made advisory either.

WHAT IT DOES NOT CLAIM:

    **That the report means anything.** Whether the gate actually checks the
    things it should is shell and action semantics, and no YAML parser
    decides it. A job named ``CI Status`` whose only step is ``run: exit 0``
    satisfies every assertion here and reports green on every pull request
    without looking at a thing.

    **That GitHub will really report it.** Only a real pull request against
    the real protected branch proves that. Repository state -- Actions
    disabled, the workflow disabled from the UI, a fork awaiting a
    maintainer's "Approve and run", the required-contexts list itself being
    edited -- is not in these files and cannot be read from them.

    This is the same demotion the credential lint in
    ``tests/unit/test_credential_file_permissions.py`` took after adversarial
    review, and the same remedy: ``KNOWN_BLIND_SPOTS`` below lists shapes
    this guard provably does not see, and
    ``test_the_guard_is_blind_to_these_and_says_so`` asserts that it does
    not, so a green run here is never read as wider than what is claimed
    above. If somebody teaches the guard one of them, that test fails and
    forces this docstring and the two dictionaries to be updated together.

One limitation is deliberately *not* in that list, because it fails closed
rather than open: branch names are compared literally, so a glob in
``branches:`` is not expanded and would be reported as a problem rather than
waved through. A guard that complains too much gets fixed; one that stays
quiet does not.
"""

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"

REQUIRED_CONTEXTS = ("Tests Status", "CI Status")

PROTECTED_BRANCHES = {"master", "main"}

#: The ``types`` GitHub uses for ``pull_request`` when none are named. A
#: workflow that names a narrower set stops firing on the events that matter:
#: ``types: [labeled]`` leaves the workflow valid, firing, and reporting the
#: context on no ordinary pull request at all.
DEFAULT_PULL_REQUEST_TYPES = {"opened", "synchronize", "reopened"}

#: Expressions that cannot evaluate false. GitHub accepts ``if:`` bare or
#: wrapped in ``${{ }}``, and YAML may hand either back as a string.
ALWAYS_RUNS = frozenset(
    {
        "always()",
        "${{ always() }}",
        "${{always()}}",
    }
)


def _triggers(document):
    """The ``on:`` mapping.

    YAML 1.1 reads a bare ``on`` as the boolean ``True``, so the key is not
    the string most readers expect.
    """
    if not isinstance(document, dict):
        return {}
    triggers = document[True] if True in document else document.get("on")
    return triggers if isinstance(triggers, dict) else {}


def real_workflows():
    """Every workflow document in the repository, as (name, parsed) pairs."""
    paths = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    return [(path.name, yaml.safe_load(path.read_text())) for path in paths]


def _publishers(context, workflows):
    """Every (name, document, job_id) that could publish ``context``.

    Every one, not the first one. Returning the first match sorted by
    filename meant a compliant decoy -- a job named ``CI Status`` in a file
    sorting before ``fast_ci.yml`` -- satisfied the whole module while the
    real publisher was broken (#169, review RT5-3). GitHub does not pick one:
    each of these jobs publishes a check run under that name, and any of them
    can be the one that fails to appear.
    """
    found = []
    for name, document in workflows:
        if not isinstance(document, dict):
            continue
        for job_id, job in (document.get("jobs") or {}).items():
            if isinstance(job, dict) and job.get("name") == context:
                found.append((name, document, job_id))
    return found


def _trigger_problems(context, name, document, job_id):
    """Reasons the workflow might not fire on a pull request at all."""
    problems = []
    where = f"{name} publishes the required status context {context!r} from job {job_id!r}, but "
    pull_request = _triggers(document).get("pull_request")

    if pull_request is None and "pull_request" not in _triggers(document):
        return [
            where + "has no pull_request trigger, so it can never report on a "
            "pull request. See #169."
        ]
    pull_request = pull_request or {}

    branches = set(pull_request.get("branches") or [])
    if branches and not branches & PROTECTED_BRANCHES:
        problems.append(
            where + f"its pull_request trigger names branches {sorted(branches)}, "
            "none of them protected. See #169."
        )
    ignored = set(pull_request.get("branches-ignore") or [])
    if ignored & PROTECTED_BRANCHES:
        problems.append(
            where + f"its pull_request trigger excludes branches {sorted(ignored)}, "
            "which are protected. See #169."
        )

    for filter_key in ("paths", "paths-ignore"):
        if filter_key in pull_request:
            problems.append(
                where + f"its pull_request trigger is filtered by {filter_key}: "
                f"{pull_request[filter_key]}. A pull request that touches none "
                "of those files never triggers the workflow, so the context is "
                "never reported, and GitHub blocks the merge forever waiting "
                "for it. See #169."
            )

    types = set(pull_request.get("types") or [])
    if types and not DEFAULT_PULL_REQUEST_TYPES <= types:
        problems.append(
            where + f"its pull_request trigger fires only on types {sorted(types)}, "
            f"which omits {sorted(DEFAULT_PULL_REQUEST_TYPES - types)}. The "
            "workflow then stops running when a pull request is opened or "
            "pushed to -- exactly the events branch protection waits on -- and "
            "the merge sticks on 'Expected'. See #169."
        )
    return problems


def _job_problems(context, name, document, job_id):
    """Reasons the publishing job might not run, or might not be that name."""
    job = document["jobs"][job_id]
    problems = []
    where = f"{name}'s job {job_id!r} publishes the required status context {context!r}, but "

    condition = job.get("if")
    if condition is not None and str(condition).strip() not in ALWAYS_RUNS:
        problems.append(
            where + f"is guarded by `if: {condition}`. A required check is only "
            "required when it is reported: a pull request where that condition "
            "is false skips the job, GitHub counts the skip as satisfying "
            "branch protection, and the gate silently stops gating. The only "
            f"condition allowed here is one that cannot be false -- one of "
            f"{sorted(ALWAYS_RUNS)}. See #169."
        )

    if job.get("needs") and str(job.get("if", "")).strip() not in ALWAYS_RUNS:
        problems.append(
            where + f"depends on {job['needs']} with `if: {job.get('if')!r}`. A "
            "job whose dependency fails or is skipped does not run, so the "
            "required context goes unreported precisely when a job broke -- "
            f"and the skip reads as a pass. Use one of {sorted(ALWAYS_RUNS)}. "
            "See #169."
        )

    if job.get("continue-on-error"):
        problems.append(
            where + "is marked `continue-on-error`, which makes the job "
            "advisory: it reports success whatever its steps did, so the "
            "required context turns green on a pull request that broke the "
            "build. See #169."
        )

    matrix = (job.get("strategy") or {}).get("matrix")
    if matrix:
        problems.append(
            where + f"is fanned out by `strategy.matrix: {matrix}`. A matrix job "
            "publishes one check run per combination, each named "
            f"'{context} (<values>)' -- so the context branch protection waits "
            f"for, {context!r} exactly, is never reported by anything. See #169."
        )

    if "uses" in job:
        problems.append(
            where + f"delegates to the reusable workflow {job['uses']!r}. The "
            "check runs then come from the jobs *inside* that workflow and are "
            f"named '{context} / <inner job>', so nothing reports {context!r} "
            "itself. See #169."
        )

    steps = job.get("steps")
    if not isinstance(steps, list) or not steps:
        if "uses" not in job:
            problems.append(where + "has no steps. See #169.")
        return problems

    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        label = step.get("name") or step.get("uses") or f"#{index}"
        condition = step.get("if")
        if condition is not None and str(condition).strip() not in ALWAYS_RUNS:
            problems.append(
                where + f"its step {label!r} is guarded by `if: {condition}`. "
                "This is the trigger trap one more level down: the job still "
                "runs, every step skips, the job concludes success in a few "
                "seconds, and the context reports a pass on every pull request "
                "without having checked anything. See #169, review RT5-1."
            )
        if step.get("continue-on-error"):
            problems.append(
                where + f"its step {label!r} is marked `continue-on-error`, so "
                "the job succeeds when that step fails and the gate reports "
                "green on a broken build. See #169."
            )
    return problems


def context_problems(context, workflows):
    """Every reason ``context`` might go unreported. Empty means no problem."""
    publishers = _publishers(context, workflows)
    if not publishers:
        return [
            f"branch protection requires the status context {context!r}, but no "
            f"job in any workflow is named that, so nothing will ever report it"
        ]
    problems = []
    for name, document, job_id in publishers:
        problems.extend(_trigger_problems(context, name, document, job_id))
        problems.extend(_job_problems(context, name, document, job_id))
    return problems


# The repository's own workflows.


@pytest.mark.parametrize("context", REQUIRED_CONTEXTS)
def test_required_context_is_published_by_some_workflow(context):
    assert _publishers(context, real_workflows()), (
        f"branch protection requires the status context {context!r}, but no "
        f"job in {WORKFLOWS} is named that, so nothing will ever report it"
    )


@pytest.mark.parametrize("context", REQUIRED_CONTEXTS)
def test_required_context_will_be_reported_on_every_pull_request(context):
    problems = context_problems(context, real_workflows())
    assert problems == [], "\n\n".join(problems)


# Bypasses. Every one of these defeated the guard as it stood before this
# commit -- verified by applying each to a copy of the tree and watching all
# eight tests pass -- and every one silences the required context or makes it
# report green without checking anything.

COMPLIANT = """
name: Gate
on:
  pull_request:
    branches: [master]
jobs:
  gate:
    name: CI Status
    runs-on: ubuntu-latest
    if: always()
    steps:
      - name: Check status
        run: ./verify.sh
"""

BYPASSES = {
    # RT5-1, the worst of them: the job runs, its only step skips, the job
    # concludes success, and the context passes on every pull request.
    "step_level_if": {"gate.yml": """
name: Gate
on:
  pull_request:
    branches: [master]
jobs:
  gate:
    name: CI Status
    runs-on: ubuntu-latest
    if: always()
    steps:
      - name: Check status
        if: github.event_name == 'push'
        run: ./verify.sh
"""},
    # RT5-2: the workflow stops firing on opened/synchronize, so the context
    # is never reported and the pull request sticks on "Expected".
    "narrowed_trigger_types": {"gate.yml": """
name: Gate
on:
  pull_request:
    branches: [master]
    types: [labeled]
jobs:
  gate:
    name: CI Status
    runs-on: ubuntu-latest
    if: always()
    steps:
      - name: Check status
        run: ./verify.sh
"""},
    # RT5-3: a compliant decoy sorting first, and the real publisher broken.
    # Checking only the first match passed this 8/8.
    "compliant_decoy_hiding_a_broken_publisher": {
        "aaa_decoy.yml": COMPLIANT,
        "fast_ci.yml": """
name: Fast CI
on:
  pull_request:
    branches: [master]
    paths:
      - 'clustrix/**'
jobs:
  status-check:
    name: CI Status
    runs-on: ubuntu-latest
    if: always()
    steps:
      - name: Check status
        run: ./verify.sh
""",
    },
    # RT5-4: an advisory gate is not a gate.
    "continue_on_error_job": {"gate.yml": """
name: Gate
on:
  pull_request:
    branches: [master]
jobs:
  gate:
    name: CI Status
    runs-on: ubuntu-latest
    continue-on-error: true
    if: always()
    steps:
      - name: Check status
        run: ./verify.sh
"""},
    "continue_on_error_step": {"gate.yml": """
name: Gate
on:
  pull_request:
    branches: [master]
jobs:
  gate:
    name: CI Status
    runs-on: ubuntu-latest
    if: always()
    steps:
      - name: Check status
        continue-on-error: true
        run: ./verify.sh
"""},
    # G5: the context becomes "CI Status (1)" and "CI Status (2)", and
    # "CI Status" is reported by nothing.
    "matrix_renames_the_context": {"gate.yml": """
name: Gate
on:
  pull_request:
    branches: [master]
jobs:
  gate:
    name: CI Status
    runs-on: ubuntu-latest
    if: always()
    strategy:
      matrix:
        shard: [1, 2]
    steps:
      - name: Check status
        run: ./verify.sh
"""},
    # The same rename by a different route: "CI Status / <inner job>".
    "reusable_workflow_renames_the_context": {"gate.yml": """
name: Gate
on:
  pull_request:
    branches: [master]
jobs:
  gate:
    name: CI Status
    uses: ./.github/workflows/inner.yml
"""},
    # The originals this module was written for.
    "paths_filtered_trigger": {"gate.yml": """
name: Gate
on:
  pull_request:
    branches: [master]
    paths:
      - 'clustrix/**'
jobs:
  gate:
    name: CI Status
    runs-on: ubuntu-latest
    if: always()
    steps:
      - name: Check status
        run: ./verify.sh
"""},
    "job_level_if": {"gate.yml": """
name: Gate
on:
  pull_request:
    branches: [master]
jobs:
  gate:
    name: CI Status
    runs-on: ubuntu-latest
    if: github.event_name == 'push'
    steps:
      - name: Check status
        run: ./verify.sh
"""},
    "needs_without_always": {"gate.yml": """
name: Gate
on:
  pull_request:
    branches: [master]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: ./build.sh
  gate:
    name: CI Status
    runs-on: ubuntu-latest
    needs: [build]
    steps:
      - name: Check status
        run: ./verify.sh
"""},
    "protected_branch_excluded": {"gate.yml": """
name: Gate
on:
  pull_request:
    branches-ignore: [master]
jobs:
  gate:
    name: CI Status
    runs-on: ubuntu-latest
    if: always()
    steps:
      - name: Check status
        run: ./verify.sh
"""},
    "nothing_publishes_it": {"gate.yml": """
name: Gate
on:
  pull_request:
    branches: [master]
jobs:
  gate:
    name: Something Else
    runs-on: ubuntu-latest
    steps:
      - name: Check status
        run: ./verify.sh
"""},
}

#: Shapes this guard provably does NOT see. Recorded here, and asserted
#: below, so the module states its own limits rather than implying it has
#: none. Every one of these leaves the required context useless or
#: unreported, and not one of them is decidable from the YAML.
#:
#: Two further blind spots cannot be written down as a document at all,
#: because they are repository state rather than file content, and they are
#: named in the module docstring instead: Actions or the workflow being
#: disabled, a fork's run awaiting "Approve and run", and the
#: required-contexts list itself drifting from ``REQUIRED_CONTEXTS`` (whose
#: re-derivation command is at the top of this file).
KNOWN_BLIND_SPOTS = {
    # The decoy from BYPASSES, standing alone. It is caught above only
    # because a *broken* publisher sits beside it; as the sole publisher it
    # satisfies every assertion in this module and verifies nothing. Whether
    # a shell script checks anything is not a YAML question.
    "a_publisher_that_verifies_nothing": {"gate.yml": """
name: Gate
on:
  pull_request:
    branches: [master]
jobs:
  gate:
    name: CI Status
    runs-on: ubuntu-latest
    if: always()
    steps:
      - name: Check status
        run: exit 0
"""},
    # `|| true` on the one command that matters, which is the same thing
    # written to look like work.
    "a_publisher_that_swallows_its_own_verdict": {"gate.yml": """
name: Gate
on:
  pull_request:
    branches: [master]
jobs:
  gate:
    name: CI Status
    runs-on: ubuntu-latest
    if: always()
    steps:
      - name: Check status
        run: ./verify.sh || true
"""},
    # An aggregator that forgets a job. `needs` covering every other job in
    # the workflow is not a rule -- workflows legitimately contain unrelated
    # jobs -- so this guard cannot tell a deliberate omission from a
    # forgotten one. The gate reports success while `security-scan` burns.
    "an_aggregator_that_omits_a_job": {"gate.yml": """
name: Gate
on:
  pull_request:
    branches: [master]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: ./build.sh
  security-scan:
    runs-on: ubuntu-latest
    steps:
      - run: ./scan.sh
  gate:
    name: CI Status
    runs-on: ubuntu-latest
    needs: [build]
    if: always()
    steps:
      - name: Check status
        run: test "${{ needs.build.result }}" = success
"""},
    # A runner label nobody provides. The job queues forever, the context is
    # never reported, and the merge blocks on "Expected" -- the #169 symptom
    # exactly. The set of valid labels is site-defined, so no parser knows.
    "a_runner_label_nobody_provides": {"gate.yml": """
name: Gate
on:
  pull_request:
    branches: [master]
jobs:
  gate:
    name: CI Status
    runs-on: [self-hosted, gpu-box-that-was-decommissioned]
    if: always()
    steps:
      - name: Check status
        run: ./verify.sh
"""},
    # A third-party action in place of the shell. What it does is its own
    # business, and it may well be a no-op.
    "a_third_party_action_of_unknown_behaviour": {"gate.yml": """
name: Gate
on:
  pull_request:
    branches: [master]
jobs:
  gate:
    name: CI Status
    runs-on: ubuntu-latest
    if: always()
    steps:
      - name: Check status
        uses: some-org/always-green-action@v1
"""},
}


def _parse(documents):
    return [(name, yaml.safe_load(text)) for name, text in documents.items()]


@pytest.mark.parametrize("name", sorted(BYPASSES))
def test_guard_catches_every_known_bypass(name):
    """Each of these silences the required check; the guard must say so."""
    assert context_problems("CI Status", _parse(BYPASSES[name])) != [], (
        f"the bypass {name!r} passed every check, so a required status "
        "context can be turned off without this module noticing. See #169."
    )


@pytest.mark.parametrize("name", sorted(KNOWN_BLIND_SPOTS))
def test_the_guard_is_blind_to_these_and_says_so(name):
    """The guard must not be believed to cover what it cannot see.

    A guard whose docstring overstates it is worse than no guard, because it
    is believed. This asserts the overstatement is impossible: if somebody
    extends the guard to catch one of these, this test fails and forces the
    docstring and ``KNOWN_BLIND_SPOTS`` to be updated together.

    None of these is acceptable. Each one is a required check that reports
    nothing, or reports green having checked nothing -- and each one is
    caught only by opening a real pull request against the real protected
    branch, which is the only authority there has ever been here.
    """
    assert context_problems("CI Status", _parse(KNOWN_BLIND_SPOTS[name])) == [], (
        f"the guard now catches {name!r}; move it out of KNOWN_BLIND_SPOTS "
        "and into BYPASSES, and update the docstring's list of what this "
        "module does not claim"
    )
