"""Compatibility shim: every piece of project metadata lives in pyproject.toml.

This file used to repeat the name, version, classifiers, dependencies and
extras, and the copies had drifted -- the ``dev`` extra here was missing
packages the one in ``pyproject.toml`` listed. setuptools reads the
``[project]`` table and ignores what a ``setup()`` call says about the same
fields, so the copies were not even in effect; they were only wrong. It stays
as a file because tools that predate PEP 517 still look for one.
"""

from setuptools import setup

setup()
