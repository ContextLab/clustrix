"""A stand-in for a package that lives in the user's working tree.

Nothing installs this; it exists so tests can prove that a function reaching
into a project-local module still runs on a machine that has never seen it.
"""
