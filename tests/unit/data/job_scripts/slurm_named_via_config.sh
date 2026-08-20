#!/bin/bash
#SBATCH --job-name=clustrix
#SBATCH --output=/remote/job/slurm-%j.out
#SBATCH --error=/remote/job/slurm-%j.err
#SBATCH --cpus-per-task=2
#SBATCH --mem=4G
#SBATCH --time=01:00:00
export CLUSTRIX_RESULT_KEY=$(cat /remote/job/.clustrix_result_key 2>/dev/null || true)
cd /remote/job
# clustrix: a batch shell does not initialise conda, and this job was
# not preceded by environment replication, so no conda installation
# was measured for this cluster. Find one now, or stop with a reason.
_clustrix_conda_base() { command -v conda >/dev/null 2>&1 || return 0; if command -v timeout >/dev/null 2>&1; then timeout 10 conda info --base 2>/dev/null; else conda info --base 2>/dev/null; fi | grep -E "^/" | head -1 || true; return 0; }
_clustrix_conda_works() { command -v conda >/dev/null 2>&1 || return 1; if command -v timeout >/dev/null 2>&1; then timeout 10 conda --version >/dev/null 2>&1; else conda --version >/dev/null 2>&1; fi; }
if ! _clustrix_conda_works; then
  _clustrix_conda_sh=""
  for _clustrix_base in "${CONDA_PREFIX:-}" "$(_clustrix_conda_base)" "${HOME:-}/miniconda3" "${HOME:-}/anaconda3" "${HOME:-}/miniforge3" /opt/conda /usr/local/miniconda3 /usr/local/anaconda3; do
    if [ -n "$_clustrix_base" ] && [ -f "$_clustrix_base/etc/profile.d/conda.sh" ]; then
      _clustrix_conda_sh="$_clustrix_base/etc/profile.d/conda.sh"
      break
    fi
  done
  if [ -n "$_clustrix_conda_sh" ]; then
    . "$_clustrix_conda_sh" || true
  fi
fi
if ! _clustrix_conda_works; then
  echo 'clustrix: cannot run this job in conda environment legacy: no conda installation was found on this node.' >&2
  echo 'clustrix: looked for etc/profile.d/conda.sh under $CONDA_PREFIX, $(conda info --base), $HOME/miniconda3, $HOME/anaconda3, $HOME/miniforge3, /opt/conda, /usr/local/miniconda3, /usr/local/anaconda3.' >&2
  echo 'clustrix: if this cluster initialises conda some other way, put that in module_loads (e.g. module_loads=["anaconda"]) or pre_execution_commands; both run before this point.' >&2
  exit 1
fi
conda run -n legacy python -c "
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