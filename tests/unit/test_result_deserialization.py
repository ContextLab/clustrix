"""Results must be read back with the serializer that wrote them.

The remote stages write `result.pkl` with dill. The caller read it with stdlib
`pickle.loads`. stdlib pickle can *replay* dill's reconstruction opcodes -- so
nothing errored -- but for a class defined in the caller's `__main__` it builds
a brand new class object instead of reusing the one already in memory. A
returned instance then failed `isinstance()` against the very class that
defined it, and any `__eq__` guarded by `isinstance` returned False, while the
repr looked perfect:

    >>> got
    Point(12,23)
    >>> got == Point(12, 23)
    False

dill's loader reuses the existing class. This was mistaken for an inherent
limit of by-value serialization; it is only an asymmetry.

The scenario needs the class to live in `__main__`, which is the whole point,
so the caller runs as a script rather than as an imported test module.
"""

import json
import pathlib
import subprocess
import sys

import pytest

CALLER = pathlib.Path(__file__).with_name("_result_roundtrip_caller.py")


@pytest.fixture(scope="module")
def verdicts(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("resultser")
    result = subprocess.run(
        [sys.executable, str(CALLER), str(tmp)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr.strip())
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_dill_preserves_the_callers_class(verdicts):
    assert verdicts["dill"] == {
        "repr": "Point(12,23)",
        "is_local_class": True,
        "isinstance": True,
        "equal": True,
    }


def test_stdlib_pickle_does_not(verdicts):
    """Pinned so the reason this matters stays visible: it looks correct."""
    wrong = verdicts["stdlib_pickle"]
    assert wrong["repr"] == "Point(12,23)"
    assert wrong["is_local_class"] is False
    assert wrong["isinstance"] is False
    assert wrong["equal"] is False


def test_every_result_path_reads_with_dill():
    """A new backend reaching for stdlib pickle would reintroduce the bug."""
    root = pathlib.Path(__file__).resolve().parents[2] / "clustrix"
    offenders = []
    for path in sorted(root.glob("*.py")):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or "cloudpickle" in stripped:
                continue
            if "pickle.loads(payload" in stripped or "result = pickle.load" in stripped:
                offenders.append(f"{path.name}:{number}")
    assert not offenders, f"results read with stdlib pickle at: {offenders}"
