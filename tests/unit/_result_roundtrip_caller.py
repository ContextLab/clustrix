"""Run as a script so the class lives in __main__ -- the whole point.

Used by test_result_deserialization.py; not a test module itself.
"""

import json
import pathlib
import pickle
import subprocess
import sys

import dill

WORKER = """
import pathlib, sys, dill
func, arg = dill.loads(pathlib.Path(sys.argv[1]).read_bytes())
pathlib.Path(sys.argv[2]).write_bytes(dill.dumps(func(arg)))
"""


class Point:
    def __init__(self, x, y):
        self.x, self.y = x, y

    def __eq__(self, o):
        return isinstance(o, Point) and (o.x, o.y) == (self.x, self.y)

    def __repr__(self):
        return f"Point({self.x},{self.y})"


def translate(point):
    return Point(point.x + 10, point.y + 20)


tmp = pathlib.Path(sys.argv[1])
payload, output = tmp / "in.bin", tmp / "out.bin"
payload.write_bytes(dill.dumps((translate, Point(2, 3)), recurse=True))

# Another process entirely, as a real job is.
subprocess.run(
    [sys.executable, "-c", WORKER, str(payload), str(output)], check=True, cwd="/"
)

raw = output.read_bytes()
verdicts = {}
for name, loads in [("dill", dill.loads), ("stdlib_pickle", pickle.loads)]:
    got = loads(raw)
    verdicts[name] = {
        "repr": repr(got),
        "is_local_class": type(got) is Point,
        "isinstance": isinstance(got, Point),
        "equal": got == Point(12, 23),
    }
print(json.dumps(verdicts))
