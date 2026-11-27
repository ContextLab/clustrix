"""Imported only from inside function bodies; see test_dependency_detection."""

from mypkg.mathutils import triple


def nine_times(x):
    return triple(triple(x))
