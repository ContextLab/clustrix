"""When neither deserializer can read a payload, both reasons must survive.

Issue #168, site 3. ``deserialize_function`` tries ``dill.loads`` and falls
back to ``cloudpickle.loads``. The fallback was written as a bare

    except Exception:
        func = cloudpickle.loads(...)

which rebinds without chaining, so when cloudpickle failed too the caller saw
only cloudpickle's reason and dill's was gone. The two are usually different,
and dill's is often the informative one -- it is the one that names the object
that could not be reconstructed.

This sits on the remote execution path, where the failure happened in another
interpreter on another machine and the traceback is the whole of what the
caller gets, so losing half of it is expensive.

The fallback itself is expected to fire routinely, so the success path stays
silent; only the failure path reports, and it reports both.
"""

import pickle

import cloudpickle
import dill
import pytest

from clustrix.utils import deserialize_function, serialize_function


def _payload(function_bytes):
    """A real serialize_function-shaped dict with a chosen function payload."""
    return {
        "function": function_bytes,
        "args": dill.dumps((5,)),
        "kwargs": dill.dumps({}),
    }


def _both_fail(function_bytes):
    """Confirm the premise: neither loader can read these bytes."""
    for loader in (dill.loads, cloudpickle.loads):
        with pytest.raises(Exception):
            loader(function_bytes)


class TestBothReasonsSurvive:
    def test_the_message_names_dill_and_cloudpickle(self):
        garbage = b"not a pickle at all"
        _both_fail(garbage)

        with pytest.raises(Exception) as caught:
            deserialize_function(_payload(garbage))

        message = str(caught.value)
        assert "dill" in message, f"dill's attempt is unnamed: {message!r}"
        assert (
            "cloudpickle" in message
        ), f"cloudpickle's attempt is unnamed: {message!r}"

    def test_the_message_carries_both_reasons_not_just_both_names(self):
        garbage = b"not a pickle at all"

        dill_reason = None
        try:
            dill.loads(garbage)
        except Exception as exc:  # noqa: BLE001 - capturing the real reason
            dill_reason = str(exc)
        assert dill_reason is not None

        cloudpickle_reason = None
        try:
            cloudpickle.loads(garbage)
        except Exception as exc:  # noqa: BLE001 - capturing the real reason
            cloudpickle_reason = str(exc)
        assert cloudpickle_reason is not None

        with pytest.raises(Exception) as caught:
            deserialize_function(_payload(garbage))

        message = str(caught.value)
        # Both reasons, counted separately. For this payload the two happen to
        # read the same, and "the text appears once" is exactly what the
        # defect produced -- the message *was* dill's reason alone. Requiring
        # two occurrences is what makes this assertion arm the fix.
        wanted = 2 if dill_reason == cloudpickle_reason else 1
        assert message.count(dill_reason) >= wanted, (
            f"dill said {dill_reason!r} and cloudpickle said "
            f"{cloudpickle_reason!r}; the report carries the reason "
            f"{message.count(dill_reason)} time(s), wanted {wanted}: "
            f"{message!r}"
        )
        assert cloudpickle_reason in message

    def test_the_exception_chain_holds_both_real_exceptions(self):
        """Not just the text: both original exception objects stay reachable."""
        with pytest.raises(Exception) as caught:
            deserialize_function(_payload(b"not a pickle at all"))

        raised = caught.value
        cause = raised.__cause__
        assert (
            cause is not None
        ), "nothing was chained, so the second failure's traceback is gone"
        first = cause.__context__
        assert first is not None, (
            "the second failure is chained but the first one is not, which is "
            "the exact defect: dill's reason has been discarded"
        )
        assert first is not cause

    def test_a_different_reason_pair_is_reported_too(self):
        """Not hard-coded to one payload: a truncated pickle reports as itself."""
        truncated = b"\x80\x05\x95\x00"
        _both_fail(truncated)

        with pytest.raises(Exception) as caught:
            deserialize_function(_payload(truncated))

        message = str(caught.value)
        assert "truncated" in message, message
        assert "dill" in message and "cloudpickle" in message, message


class TestTheOrdinaryPathIsUnchanged:
    def test_a_real_function_still_round_trips(self):
        def triple(x):
            return x * 3

        func, args, kwargs = deserialize_function(serialize_function(triple, (5,), {}))
        assert func(*args, **kwargs) == 15

    def test_the_bytes_format_still_works(self):
        import math

        func, args, kwargs = deserialize_function(pickle.dumps((math.sqrt, (25,), {})))
        assert func(*args, **kwargs) == 5.0

    def test_a_non_dict_non_bytes_payload_still_raises_valueerror(self):
        with pytest.raises(ValueError, match="Invalid function data format"):
            deserialize_function("invalid_string_format")
