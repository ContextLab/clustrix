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
    Concretely: the workflow fires on ``pull_request`` for every protected
    branch, with no ``paths``/``paths-ignore`` filter and no ``types``
    narrower than the default; the job names a runner, is not conditioned on
    anything that can be false, not made advisory with ``continue-on-error``,
    not fanned out by a ``strategy: matrix`` (which renames the context), and
    not delegated to a reusable workflow (which also renames it); and no step
    of it can be skipped or made advisory either.

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

Branch names are matched the way GitHub matches them -- ``*`` within a path
segment, ``**`` across segments, ``?``, and ``!`` negation, evaluated in
order -- because the literal comparison this module started with was not
merely conservative. It was fail-closed on ``branches: [ma*]``, a working
trigger reported as broken; but it was fail-*open* on ``branches-ignore:
[ma*]`` and ``branches-ignore: ['**']``, which exclude the protected branch,
silence the required context and were waved straight through (review RT6-3).

Two constructs in that filter syntax are still not modelled -- ``+`` (one or
more of the preceding character) and character ranges -- and a pattern using
either is reported as a problem rather than guessed at. That direction is
only conservative: a guard that complains too much gets fixed; one that stays
quiet does not.
"""

import re
from pathlib import Path

import pytest

import sys
import sys

if sys.platform == "win32":
    pytest.skip(
        "parses workflow YAML and asserts runner-shaped context output",
        allow_module_level=True,
    )
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"

REQUIRED_CONTEXTS = ("Tests Status", "CI Status")

#: The branches whose protection actually requires those contexts. Derived
#: from the same API as ``REQUIRED_CONTEXTS``, and from nothing else:
#:
#: .. code-block:: console
#:
#:    $ gh api repos/ContextLab/clustrix/branches \
#:        --jq '[.[] | select(.protected) | .name]'
#:    ["master"]
#:
#: ``main`` was in this set too, on the reasoning that it is the conventional
#: name and costs nothing. It cost the guard its point. A trigger reading
#: ``branches: [main]`` names a branch this repository does not have, so it
#: never fires on a pull request against ``master`` and never reports the
#: context -- and membership of this set waved it through (review RT6-4).
#: What the guard checks is that *every* protected branch is covered, so a
#: name that is not protected cannot stand in for one that is. If ``main``
#: is ever protected here, re-run the command above and add it back.
PROTECTED_BRANCHES = {"master"}

#: Constructs in GitHub's branch filter syntax that this module does not
#: model: ``+`` matches one or more of the preceding character and ``[]``
#: introduces a character range. A pattern containing either is reported as a
#: problem in both directions rather than evaluated wrongly in one.
UNMODELLED_PATTERN_CHARS = frozenset("+[]")

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
    """The events the workflow fires on, keyed by event name.

    YAML 1.1 reads a bare ``on`` as the boolean ``True``, so the key is not
    the string most readers expect.

    The *value* has three spellings, and only one of them is a mapping.
    ``on: pull_request`` and ``on: [pull_request, push]`` are both valid, and
    both mean the event with no ``branches``, ``paths`` or ``types`` filter
    at all -- which is the most permissive form there is, strictly better
    than the mapping form for the purpose of this module. Reading only the
    mapping form reported both of them as having no pull_request trigger
    (review RT6-6), which is a guard rejecting a correct configuration: the
    fix for that is always to weaken or delete the guard, and everything in
    ``BYPASSES`` goes with it.
    """
    if not isinstance(document, dict):
        return {}
    triggers = document[True] if True in document else document.get("on")
    if isinstance(triggers, str):
        return {triggers: {}}
    if isinstance(triggers, list):
        return {event: {} for event in triggers if isinstance(event, str)}
    return triggers if isinstance(triggers, dict) else {}


def _pattern_matches(pattern, branch):
    """Does one GitHub branch filter pattern match this branch name?

    ``*`` matches within a path segment, ``**`` across segments, ``?`` one
    character, and everything else is literal. Callers must reject patterns
    containing ``UNMODELLED_PATTERN_CHARS`` before getting here.
    """
    regex = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if pattern[index + 1 : index + 2] == "*":
                regex.append(".*")
                index += 2
            else:
                regex.append("[^/]*")
                index += 1
            continue
        regex.append("[^/]" if char == "?" else re.escape(char))
        index += 1
    return re.fullmatch("".join(regex), branch) is not None


def _filter_matches(patterns, branch):
    """Whether a ``branches``/``branches-ignore`` list selects ``branch``.

    ``!`` negates, and a later pattern overrides an earlier one, which is how
    GitHub evaluates these lists.
    """
    selected = False
    for pattern in patterns:
        if pattern.startswith("!"):
            if _pattern_matches(pattern[1:], branch):
                selected = False
        elif _pattern_matches(pattern, branch):
            selected = True
    return selected


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

    for filter_key in ("branches", "branches-ignore"):
        patterns = [str(pattern) for pattern in (pull_request.get(filter_key) or [])]
        if not patterns:
            continue
        unmodelled = sorted(
            {p for p in patterns if UNMODELLED_PATTERN_CHARS & set(p.lstrip("!"))}
        )
        if unmodelled:
            problems.append(
                where + f"its pull_request trigger's {filter_key}: uses the "
                f"patterns {unmodelled}, which contain filter syntax this "
                "guard does not model. Whether they cover the protected "
                f"branches {sorted(PROTECTED_BRANCHES)} is therefore reported "
                "rather than guessed at. See #169."
            )
            continue
        if filter_key == "branches":
            missed = sorted(
                branch
                for branch in PROTECTED_BRANCHES
                if not _filter_matches(patterns, branch)
            )
            if missed:
                problems.append(
                    where + f"its pull_request trigger names branches {patterns}, "
                    f"which do not select the protected branch(es) {missed}. A "
                    "pull request against one of those never triggers the "
                    "workflow, so the context is never reported and GitHub "
                    "blocks the merge forever waiting for it. See #169."
                )
        else:
            excluded = sorted(
                branch
                for branch in PROTECTED_BRANCHES
                if _filter_matches(patterns, branch)
            )
            if excluded:
                problems.append(
                    where + f"its pull_request trigger excludes {patterns}, which "
                    f"covers the protected branch(es) {excluded}. See #169."
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

    if "uses" not in job and not job.get("runs-on"):
        problems.append(
            where + "names no `runs-on`. That is not a valid workflow: GitHub "
            "rejects the document, so the job never starts, no check run is "
            "ever created under that name, and the pull request sits on "
            "'Expected -- Waiting for status to be reported' -- #169's symptom "
            "exactly, arrived at by a typo rather than a filter. See #169."
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


# Bypasses. Every one of these silences the required status context, or makes
# it report green without checking anything, and all but one defeated the
# guard as it stood before the commit that added it -- verified by running the
# guard from before that commit over each document and watching it return no
# problems. (The exception is noted where it appears: it was already caught,
# for a reason that no longer applies.)

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
    # RT6-3: the glob the docstring used to claim was reported as a problem.
    # `ma*` and `**` both cover master, and neither is a literal match, so
    # the required context is silenced and nothing says so.
    "protected_branch_excluded_by_a_glob": {"gate.yml": """
name: Gate
on:
  pull_request:
    branches-ignore: ['ma*']
jobs:
  gate:
    name: CI Status
    runs-on: ubuntu-latest
    if: always()
    steps:
      - name: Check status
        run: ./verify.sh
"""},
    "every_branch_excluded": {"gate.yml": """
name: Gate
on:
  pull_request:
    branches-ignore: ['**']
jobs:
  gate:
    name: CI Status
    runs-on: ubuntu-latest
    if: always()
    steps:
      - name: Check status
        run: ./verify.sh
"""},
    # The same exclusion by negation: `**` selects master, `!master` takes it
    # back out, and the workflow never fires on the branch that needs it. This
    # is the one entry here the literal comparison already rejected -- because
    # neither string is the word "master", not because anything understood the
    # negation. It is kept so that reading `!` correctly does not quietly turn
    # a caught case into an accepted one.
    "protected_branch_negated_out_of_the_selection": {"gate.yml": """
name: Gate
on:
  pull_request:
    branches: ['**', '!master']
jobs:
  gate:
    name: CI Status
    runs-on: ubuntu-latest
    if: always()
    steps:
      - name: Check status
        run: ./verify.sh
"""},
    # RT6-4: `main` is not a branch of this repository, let alone a
    # protected one. This fires on nothing and reports nothing.
    "only_an_unprotected_branch": {"gate.yml": """
name: Gate
on:
  pull_request:
    branches: [main]
jobs:
  gate:
    name: CI Status
    runs-on: ubuntu-latest
    if: always()
    steps:
      - name: Check status
        run: ./verify.sh
"""},
    # RT6-5: an invalid workflow. The job cannot start, so the check run is
    # never created -- indistinguishable, from branch protection's side,
    # from the path filter this module was written for.
    "publisher_with_no_runs_on": {"gate.yml": """
name: Gate
on:
  pull_request:
    branches: [master]
jobs:
  gate:
    name: CI Status
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


#: Configurations that are correct, and that the guard must NOT flag. A
#: guard that rejects a working setup is its own defect: the fix somebody
#: reaches for is to weaken or delete it, and every bypass above goes with
#: it. ``on: [pull_request]`` and ``on: pull_request`` are both valid and
#: both *more* permissive than the mapping form -- no branch, path or type
#: filter at all -- and both were reported as "has no pull_request trigger"
#: (review RT6-6). The two glob cases are the other half of RT6-3: matching
#: branch patterns properly has to accept a pattern that covers the
#: protected branch as readily as it rejects one that excludes it.
ACCEPTED = {
    "trigger_as_a_list": {"gate.yml": """
name: Gate
on: [pull_request]
jobs:
  gate:
    name: CI Status
    runs-on: ubuntu-latest
    if: always()
    steps:
      - name: Check status
        run: ./verify.sh
"""},
    "trigger_as_a_bare_string": {"gate.yml": """
name: Gate
on: pull_request
jobs:
  gate:
    name: CI Status
    runs-on: ubuntu-latest
    if: always()
    steps:
      - name: Check status
        run: ./verify.sh
"""},
    "trigger_as_a_list_of_several_events": {"gate.yml": """
name: Gate
on: [push, pull_request]
jobs:
  gate:
    name: CI Status
    runs-on: ubuntu-latest
    if: always()
    steps:
      - name: Check status
        run: ./verify.sh
"""},
    "a_branch_glob_that_covers_master": {"gate.yml": """
name: Gate
on:
  pull_request:
    branches: ['ma*']
jobs:
  gate:
    name: CI Status
    runs-on: ubuntu-latest
    if: always()
    steps:
      - name: Check status
        run: ./verify.sh
"""},
    "a_branches_ignore_that_misses_master": {"gate.yml": """
name: Gate
on:
  pull_request:
    branches-ignore: ['dependabot/**', 'gh-pages']
jobs:
  gate:
    name: CI Status
    runs-on: ubuntu-latest
    if: always()
    steps:
      - name: Check status
        run: ./verify.sh
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


@pytest.mark.parametrize("name", sorted(ACCEPTED))
def test_guard_accepts_a_correct_configuration(name):
    """A guard that fails a working setup gets deleted, and takes the rest.

    Every document here reports the required context on every pull request
    against every protected branch. None of them may produce a problem.
    """
    assert context_problems("CI Status", _parse(ACCEPTED[name])) == [], (
        f"the guard rejects {name!r}, which is a correct configuration. See "
        "#169, review RT6-6."
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
