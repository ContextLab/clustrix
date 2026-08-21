#!/bin/bash
#SBATCH --job-name=clustrix
#SBATCH --output=/remote/job/slurm-%j.out
#SBATCH --error=/remote/job/slurm-%j.err
#SBATCH --cpus-per-task=2
#SBATCH --mem=4G
#SBATCH --time=01:00:00
cd /remote/job
export CLUSTRIX_RESULT_KEY=$(cat /remote/job/.clustrix_result_key 2>/dev/null || true)
source /opt/conda/etc/profile.d/conda.sh
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

try:
    import dill
except ImportError:
    dill = None
try:
    import cloudpickle
except ImportError:
    cloudpickle = None

print('VENV1 - Deserializing function data')
print('Python version:', sys.version)

try:
    with open('function_data.pkl', 'rb') as f:
        data = pickle.load(f)
    
    # Try to deserialize function
    func = None
    clean_source = None
    func_info = data.get('func_info', {})
    try:
        func = dill.loads(data['function']) if dill else None
        print('Successfully deserialized function with dill')
    except Exception as e:
        print('Dill deserialization failed:', str(e))
        try:
            func = cloudpickle.loads(data['function']) if cloudpickle else None
            print('Successfully deserialized function with cloudpickle')
        except Exception as e2:
            print('Cloudpickle deserialization failed:', str(e2))
            # Try source code fallback
            if func_info.get('source'):
                print('Using source code fallback')
                # Remove @cluster decorator from source
                import textwrap
                source = func_info['source']
                lines = source.split('\n')
                clean_lines = []
                for line in lines:
                    if not line.strip().startswith('@'):
                        clean_lines.append(line)
                clean_source = '\n'.join(clean_lines)
                clean_source = textwrap.dedent(clean_source)
                
                # Create function from source
                namespace = {}
                exec(clean_source, namespace)
                func = namespace[func_info['name']]
                print('Successfully created function from source code')
            else:
                raise Exception('All deserialization methods failed')
    
    # _ser, not stdlib pickle: args may carry classes defined in
    # the caller's __main__, which pickle can only store by name.
    args = _ser.loads(data['args'])
    kwargs = _ser.loads(data['kwargs'])
    
    # Pass data to VENV2 for execution. _ser (dill/cloudpickle) is
    # required here: stdlib pickle cannot serialize a function that
    # is not importable by name in this interpreter.
    with open('function_deserialized.pkl', 'wb') as f:
        if clean_source is not None:
            # Function was created from source code, pass the source
            _ser.dump({'source': clean_source, 'func_name': func_info['name'], 'args': args, 'kwargs': kwargs}, f, protocol=4)
        else:
            # Function was deserialized from binary, pass the function object
            _ser.dump({'func': func, 'args': args, 'kwargs': kwargs}, f, protocol=4)
    
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
    
    if 'func' in exec_data:
        # Function object was passed
        func = exec_data['func']
    elif 'source' in exec_data:
        # Source code was passed, recreate function
        print('Recreating function from source code in VENV2')
        namespace = {}
        exec(exec_data['source'], namespace)
        func = namespace[exec_data['func_name']]
    else:
        raise Exception('No function or source code found')
    
    args = exec_data['args']
    kwargs = exec_data['kwargs']
    
    # Execute the function
    print('Executing function with args:', args)
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

print('VENV1 - Serializing result')

try:
    import os
    if not os.path.exists('result_raw.pkl'):
        raise FileNotFoundError('result_raw.pkl not found - VENV2 execution may have failed')
    with open('result_raw.pkl', 'rb') as f:
        result = _ser.load(f)
    
    print('Result loaded from VENV2:', type(result))
    
    _payload_bytes = _ser.dumps(result, protocol=4)
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
