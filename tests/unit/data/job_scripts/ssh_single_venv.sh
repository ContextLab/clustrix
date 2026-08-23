#!/bin/bash
cd /remote/job

export CLUSTRIX_RESULT_KEY=$(cat /remote/job/.clustrix_result_key 2>/dev/null || true)
cd /remote/job
. venv/bin/activate
python -c "
import os as _os
_CLUSTRIX_KEY = _os.environ.pop('CLUSTRIX_RESULT_KEY', '')
import pickle
import sys
import traceback

try:
    import dill
except ImportError:
    dill = None
try:
    import cloudpickle
except ImportError:
    cloudpickle = None

try:
    with open('function_data.pkl', 'rb') as f:
        data = pickle.load(f)
    
    if dill is None and cloudpickle is None:
        raise RuntimeError(
            'clustrix needs dill (or at least cloudpickle) in the job '
            'environment: this function and its arguments were '
            'serialized with dill, and stdlib pickle cannot read those '
            'bytes. Install it on the cluster (pip install dill) and '
            're-submit.')
    
    # dill, not stdlib pickle: args may carry classes defined
    # in the caller's __main__, which pickle stores only by name.
    _argser = dill or cloudpickle
    try:
        func = _argser.loads(data['function'])
    except Exception:
        func = cloudpickle.loads(data['function']) if cloudpickle else None
    
    args = _argser.loads(data['args'])
    kwargs = _argser.loads(data['kwargs'])
    
    result = func(*args, **kwargs)
    
    _payload_bytes = pickle.dumps(result, protocol=4)
    with open('result.pkl', 'wb') as f:
        f.write(_payload_bytes)
    import hashlib as _hashlib
    import hmac as _hmac
    if _CLUSTRIX_KEY:
        _tag = _hmac.new(_CLUSTRIX_KEY.encode(), _payload_bytes, _hashlib.sha256).hexdigest()
        with open('result.pkl.hmac', 'w') as _sigf:
            _sigf.write(_tag)
    
except Exception as e:
    _payload = {'error': str(e), 'traceback': traceback.format_exc()}
    _errser = dill or cloudpickle or pickle
    try:
        _blob = _errser.dumps(dict(_payload, exception=e), protocol=4)
    except Exception:
        _blob = pickle.dumps(_payload, protocol=4)
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