#!/bin/bash
cd /remote/job

cd /remote/job
export CLUSTRIX_RESULT_KEY=$(cat /remote/job/.clustrix_result_key 2>/dev/null || true)
. /opt/conda/etc/profile.d/conda.sh
# Two-venv approach for cross-version compatibility
# VENV1: Serialization/deserialization with compatible Python
# VENV2: Function execution with proper environment

# Step 1: Use VENV1 to deserialize function data
# Using conda environment clustrix_venv1_abc123
conda run -n clustrix_venv1_abc123 python -c "
import os as _os
_CLUSTRIX_KEY = _os.environ.pop('CLUSTRIX_RESULT_KEY', '')
import pickle
try:
    import dill as _ser
except ImportError:
    try:
        import cloudpickle as _ser
    except ImportError:
        raise RuntimeError(
            'clustrix needs dill (or at least cloudpickle) in this '
            'environment: the function, its arguments and its result '
            'are exchanged as dill bytes, which stdlib pickle cannot '
            'read. Install it on the cluster (pip install dill) and '
            're-submit.')
import sys
import traceback

print('VENV1 - Reading function data')
print('Python version:', sys.version)

try:
    with open('function_data.pkl', 'rb') as f:
        data = pickle.load(f)
    
    # The function, its arguments and (in stage 3) its result are
    # handed on as bytes and never rebuilt here. This environment
    # holds clustrix's serializers and nothing else, so rebuilding
    # them needed every package they touch: a numpy array as an
    # argument or a return value died in this stage on
    # ModuleNotFoundError. Re-serializing the rebuilt function also
    # dropped every project-local module the caller had embedded by
    # value. VENV2 is the one place the user's objects exist.
    _handoff = {}
    for _name in ('function', 'args', 'kwargs'):
        if not isinstance(data.get(_name), bytes):
            raise ValueError('function_data.pkl has no serialized ' + _name)
        _handoff[_name + '_bytes'] = data[_name]
    with open('function_deserialized.pkl', 'wb') as f:
        _ser.dump(_handoff, f, protocol=4)
    
    print('VENV1 - Function data prepared for VENV2 using', _ser.__name__)
    
except Exception as e:
    print('VENV1 - Error during deserialization:', str(e))
    traceback.print_exc()
    import os as _os
    _payload = {'error': str(e), 'traceback': traceback.format_exc(), 'stage': 'venv1_deserialize'}
    try:
        _blob = _ser.dumps(dict(_payload, exception=e), protocol=4)
    except Exception:
        _blob = pickle.dumps(_payload, protocol=4)
    with open('error_venv1_deserialize.pkl', 'wb') as f:
        f.write(_blob)
    if not _os.path.exists('error.pkl'):
        with open('error.pkl', 'wb') as f:
            f.write(_blob)
        import hashlib as _hashlib
        import hmac as _hmac
        if _CLUSTRIX_KEY:
            _tag = _hmac.new(_CLUSTRIX_KEY.encode(), _blob, _hashlib.sha256).hexdigest()
            with open('error.pkl.hmac', 'w') as _sigf:
                _sigf.write(_tag)
    raise
"
_clustrix_rc=$?; [ "$_clustrix_rc" -eq 0 ] || exit "$_clustrix_rc"

# Step 2: Use VENV2 to execute the function
# No deactivation needed for conda run
# Using conda environment clustrix_venv2_abc123
conda run -n clustrix_venv2_abc123 python -c "
import os as _os
_CLUSTRIX_KEY = _os.environ.pop('CLUSTRIX_RESULT_KEY', '')
import pickle
try:
    import dill as _ser
except ImportError:
    try:
        import cloudpickle as _ser
    except ImportError:
        raise RuntimeError(
            'clustrix needs dill (or at least cloudpickle) in this '
            'environment: the function, its arguments and its result '
            'are exchanged as dill bytes, which stdlib pickle cannot '
            'read. Install it on the cluster (pip install dill) and '
            're-submit.')
import sys
import traceback

print('VENV2 - Executing function')
print('Python version:', sys.version)

try:
    import os
    if not os.path.exists('function_deserialized.pkl'):
        raise FileNotFoundError('function_deserialized.pkl not found - VENV1 deserialization may have failed')
    with open('function_deserialized.pkl', 'rb') as f:
        exec_data = _ser.load(f)
    
    # The caller's own bytes: dill first, cloudpickle when dill
    # cannot, which is the order the caller's serializer uses. When
    # neither can, both reasons are reported -- they usually differ,
    # and dill's tends to name the object it could not rebuild.
    try:
        func = _ser.loads(exec_data['function_bytes'])
    except Exception as _first:
        try:
            import cloudpickle
            func = cloudpickle.loads(exec_data['function_bytes'])
        except Exception as _second:
            raise RuntimeError(
                'Could not deserialize the function on the worker. '
                + _ser.__name__ + ' said ' + type(_first).__name__ + ': ' + str(_first)
                + '; cloudpickle said ' + type(_second).__name__ + ': ' + str(_second)
            ) from _second
    args = _ser.loads(exec_data['args_bytes'])
    kwargs = _ser.loads(exec_data['kwargs_bytes'])
    
    # Execute the function
    result = func(*args, **kwargs)
    print('Function execution completed successfully')
    
    # Save result for VENV1 to serialize
    with open('result_raw.pkl', 'wb') as f:
        _ser.dump(result, f, protocol=4)
    
except Exception as e:
    print('VENV2 - Error during execution:', str(e))
    traceback.print_exc()
    import os as _os
    _payload = {'error': str(e), 'traceback': traceback.format_exc(), 'stage': 'venv2_execute'}
    try:
        _blob = _ser.dumps(dict(_payload, exception=e), protocol=4)
    except Exception:
        _blob = pickle.dumps(_payload, protocol=4)
    with open('error_venv2_execute.pkl', 'wb') as f:
        f.write(_blob)
    if not _os.path.exists('error.pkl'):
        with open('error.pkl', 'wb') as f:
            f.write(_blob)
        import hashlib as _hashlib
        import hmac as _hmac
        if _CLUSTRIX_KEY:
            _tag = _hmac.new(_CLUSTRIX_KEY.encode(), _blob, _hashlib.sha256).hexdigest()
            with open('error.pkl.hmac', 'w') as _sigf:
                _sigf.write(_tag)
    raise
"
_clustrix_rc=$?; [ "$_clustrix_rc" -eq 0 ] || exit "$_clustrix_rc"

# Step 3: Use VENV1 to serialize the result
# No deactivation needed for conda run
# Using conda environment clustrix_venv1_abc123
conda run -n clustrix_venv1_abc123 python -c "
import os as _os
_CLUSTRIX_KEY = _os.environ.pop('CLUSTRIX_RESULT_KEY', '')
import pickle
try:
    import dill as _ser
except ImportError:
    try:
        import cloudpickle as _ser
    except ImportError:
        raise RuntimeError(
            'clustrix needs dill (or at least cloudpickle) in this '
            'environment: the function, its arguments and its result '
            'are exchanged as dill bytes, which stdlib pickle cannot '
            'read. Install it on the cluster (pip install dill) and '
            're-submit.')
import sys
import traceback

print('VENV1 - Signing result')

try:
    import os
    if not os.path.exists('result_raw.pkl'):
        raise FileNotFoundError('result_raw.pkl not found - VENV2 execution may have failed')
    # Bytes in, the same bytes out: see stage 1 for why the result
    # is not rebuilt in this environment.
    with open('result_raw.pkl', 'rb') as f:
        _payload_bytes = f.read()
    with open('result.pkl', 'wb') as f:
        f.write(_payload_bytes)
    
    # Tag the result so the caller can tell it apart from anything
    # else that may have been written into this directory. Loading a
    # pickle executes code, so the caller must not do it on trust.
    import hashlib as _hashlib
    import hmac as _hmac
    if _CLUSTRIX_KEY:
        _tag = _hmac.new(_CLUSTRIX_KEY.encode(), _payload_bytes, _hashlib.sha256).hexdigest()
        with open('result.pkl.hmac', 'w') as _sigf:
            _sigf.write(_tag)
    
    print('Result serialized successfully')
    
except Exception as e:
    print('VENV1 - Error during result serialization:', str(e))
    traceback.print_exc()
    import os as _os
    _payload = {'error': str(e), 'traceback': traceback.format_exc(), 'stage': 'venv1_serialize'}
    try:
        _blob = _ser.dumps(dict(_payload, exception=e), protocol=4)
    except Exception:
        _blob = pickle.dumps(_payload, protocol=4)
    with open('error_venv1_serialize.pkl', 'wb') as f:
        f.write(_blob)
    if not _os.path.exists('error.pkl'):
        with open('error.pkl', 'wb') as f:
            f.write(_blob)
        import hashlib as _hashlib
        import hmac as _hmac
        if _CLUSTRIX_KEY:
            _tag = _hmac.new(_CLUSTRIX_KEY.encode(), _blob, _hashlib.sha256).hexdigest()
            with open('error.pkl.hmac', 'w') as _sigf:
                _sigf.write(_tag)
    raise
"
