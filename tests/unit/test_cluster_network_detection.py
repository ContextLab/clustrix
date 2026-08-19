#!/usr/bin/env python3
"""Private-cluster network detection gates which real-world tests may run.

This file used to print its findings and `return` the detector's answer.
pytest treats a returned value as a pass, so it could not fail: the detector
could have raised, returned a string, or claimed the network was reachable
when it was not, and the suite would still have been green.

The contract worth holding it to is that a positive answer means the
configured cluster hosts are actually reachable -- that is the whole reason
the gate exists. The hosts come from CLUSTRIX_TEST_*_HOST, so this file names
no machine of its own; with none configured the detector must answer False.
"""

import socket

from tests.real_world.conftest import can_reach_configured_cluster
from tests.real_world.credential_manager import configured_test_hosts


def _resolves(host):
    try:
        socket.gethostbyname(host)
        return True
    except socket.gaierror:
        return False


def test_detection_returns_a_boolean():
    """Callers branch on this, so a truthy string or None would be a bug."""
    assert isinstance(can_reach_configured_cluster(), bool)


def test_detection_is_stable_across_calls():
    """The gate is consulted repeatedly; it must not flip between calls."""
    assert can_reach_configured_cluster() == can_reach_configured_cluster()


def test_no_configured_host_means_no_network():
    """With nothing configured there is nothing to reach, so the gate must
    stay shut -- otherwise real-world tests would run with no target."""
    if configured_test_hosts():
        return  # This developer has pointed the suite at a cluster.

    assert not can_reach_configured_cluster()


def test_a_positive_answer_means_a_gated_host_resolves():
    """Claiming the network without reachability would let real-world tests
    run and fail with a DNS error instead of skipping."""
    if not can_reach_configured_cluster():
        return  # Off-network is a legitimate state; nothing to assert.

    hosts = configured_test_hosts()
    assert any(_resolves(host) for host in hosts), (
        "can_reach_configured_cluster() reported a reachable cluster, but none "
        f"of the configured hosts resolve: {', '.join(hosts)}"
    )


def test_detection_is_bounded():
    """It gates which tests run, so it must answer fast even off-network.

    On a macOS CI runner, where these lookups blackhole, each call took about
    seventy seconds and three of them exhausted the job's fifteen-minute
    budget before the suite could finish.
    """
    import time

    from tests.real_world.conftest import can_reach_configured_cluster as detect

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
