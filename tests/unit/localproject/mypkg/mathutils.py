"""A sibling module of the caller, of the kind every real project has."""

SCALE = 5


def triple(value):
    return value * 3


class Widget:
    def __init__(self, n):
        self.n = n

    def value(self):
        return self.n * SCALE

    def __eq__(self, other):
        return getattr(other, "n", object()) == self.n
