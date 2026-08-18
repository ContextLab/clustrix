#!/usr/bin/env python3
"""Dartmouth network detection gates which real-world tests are allowed to run.

This file used to print its findings and `return is_dartmouth`. pytest treats a
returned value as a pass, so it could not fail: the detector could have raised,
returned a string, or claimed the network was reachable when it was not, and
the suite would still have been green.

The contract worth holding it to is that a positive answer means the cluster
hosts are actually reachable -- that is the whole reason the gate exists.
"""

import socket

from tests.real_world.conftest import is_dartmouth_network

GATED_HOSTS = ("tensor01.dartmouth.edu", "ndoli.dartmouth.edu")


def _resolves(host):
    try:
        socket.gethostbyname(host)
        return True
    except socket.gaierror:
        return False


def test_detection_returns_a_boolean():
    """Callers branch on this, so a truthy string or None would be a bug."""
    assert isinstance(is_dartmouth_network(), bool)


def test_detection_is_stable_across_calls():
    """The gate is consulted repeatedly; it must not flip between calls."""
    assert is_dartmouth_network() == is_dartmouth_network()


def test_a_positive_answer_means_the_gated_hosts_resolve():
    """Claiming the network without reachability would let real-world tests
    run and fail with a DNS error instead of skipping."""
    if not is_dartmouth_network():
        return  # Off-network is a legitimate state; nothing to assert.

    unreachable = [host for host in GATED_HOSTS if not _resolves(host)]
    assert not unreachable, (
        "is_dartmouth_network() reported the Dartmouth network, but these "
        f"hosts do not resolve: {', '.join(unreachable)}"
    )


def test_detection_is_bounded():
    """It gates which tests run, so it must answer fast even off-network.

    On a macOS CI runner, where these lookups blackhole, each call took about
    seventy seconds and three of them exhausted the job's fifteen-minute
    budget before the suite could finish.
    """
    import time

    from tests.real_world.conftest import is_dartmouth_network as detect

    detect.cache_clear()
    start = time.time()
    detect()
    assert time.time() - start < 10


def test_a_slow_lookup_is_abandoned():
    """The bound is real: a `with` block around the executor silently waited
    for the worker anyway, so a three-second budget still took thirty."""
    import time

    from tests.real_world.conftest import NETWORK_DETECTION_TIMEOUT, _within

    start = time.time()
    result = _within(NETWORK_DETECTION_TIMEOUT, time.sleep, 30)
    elapsed = time.time() - start

    assert result is None
    assert elapsed < NETWORK_DETECTION_TIMEOUT + 2
