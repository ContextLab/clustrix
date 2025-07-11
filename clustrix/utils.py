import ast
import os
import sys
import pickle
import inspect
import importlib
import subprocess
from typing import Any, Dict, Optional, Callable
import dill  # type: ignore
import cloudpickle  # type: ignore

from .config import ClusterConfig


def detect_loops(func: Callable, args: tuple, kwargs: dict) -> Optional[Dict[str, Any]]:
    """
    Analyze function to detect parallelizable loops.

    Args:
        func: Function to analyze
        args: Function arguments
        kwargs: Function keyword arguments

    Returns:
        Dictionary with loop information or None if no loops detected
    """

    try:
        source = inspect.getsource(func)
        tree = ast.parse(source)

        class LoopVisitor(ast.NodeVisitor):
            def __init__(self):
                self.loops = []

            def visit_For(self, node):
                # Analyze for loops
                if isinstance(node.target, ast.Name):
                    loop_info = {
                        "type": "for",
                        "variable": node.target.id,
                        "iterable": (
                            ast.unparse(node.iter)
                            if hasattr(ast, "unparse")
                            else "unknown"
                        ),
                    }
                    self.loops.append(loop_info)
                self.generic_visit(node)

            def visit_While(self, node):
                # Analyze while loops
                loop_info = {
                    "type": "while",
                    "condition": (
                        ast.unparse(node.test) if hasattr(ast, "unparse") else "unknown"
                    ),
                }
                self.loops.append(loop_info)
                self.generic_visit(node)

        visitor = LoopVisitor()
        visitor.visit(tree)

        if visitor.loops:
            # Return info about the first loop for now
            # In practice, you'd want more sophisticated analysis
            loop = visitor.loops[0]
            if loop["type"] == "for" and "range(" in loop["iterable"]:
                # Try to extract range information
                try:
                    # This is a simplified extraction
                    range_str = loop["iterable"]
                    if "range(" in range_str:
                        range_part = range_str[
                            range_str.find("range(") : range_str.find(
                                ")", range_str.find("range(")
                            )
                            + 1
                        ]
                        range_obj = eval(
                            range_part
                        )  # Dangerous in practice, needs safer evaluation
                        loop["range"] = range_obj
                except Exception:
                    loop["range"] = range(10)  # Default fallback

                return loop

        return None

    except Exception:
        # If analysis fails, assume no parallelizable loops
        return None


def serialize_function(func: Callable, args: tuple, kwargs: dict) -> Dict[str, Any]:
    """
    Serialize function and all its dependencies.

    Args:
        func: Function to serialize
        args: Function arguments
        kwargs: Function keyword arguments

    Returns:
        Dictionary containing serialized function and metadata
    """

    # Get current environment info
    requirements = get_environment_requirements()
    # Get environment info (not used here but needed for compatibility)
    _ = get_environment_info()  # For compatibility with tests

    # Try to get function source code for better cross-Python compatibility
    func_source = None
    try:
        func_source = inspect.getsource(func)
    except Exception:
        pass

    # Serialize function using dill for better cross-Python compatibility
    try:
        func_bytes = dill.dumps(func, protocol=4)
    except Exception:
        # Fallback to cloudpickle
        func_bytes = cloudpickle.dumps(func, protocol=4)

    # Serialize arguments
    args_bytes = pickle.dumps(args, protocol=4)
    kwargs_bytes = pickle.dumps(kwargs, protocol=4)

    # Get function metadata
    func_info = {
        "name": func.__name__,
        "module": func.__module__,
        "file": inspect.getfile(func) if hasattr(func, "__file__") else None,
        "source": None,
    }

    try:
        func_info["source"] = inspect.getsource(func)
    except Exception:
        pass

    return {
        "function": func_bytes,
        "function_source": func_source,
        "args": args_bytes,
        "kwargs": kwargs_bytes,
        "requirements": requirements,
        "func_info": func_info,
        "python_version": sys.version,
        "working_directory": os.getcwd(),
    }


def deserialize_function(func_data: bytes) -> tuple:
    """
    Deserialize function data back to function, args, and kwargs.

    Args:
        func_data: Serialized function data (bytes or dict)

    Returns:
        Tuple of (function, args, kwargs)
    """
    if isinstance(func_data, bytes):
        # Simple pickle format
        return pickle.loads(func_data)
    elif isinstance(func_data, dict):
        # Dictionary format from serialize_function
        try:
            func = dill.loads(func_data["function"])
        except Exception:
            func = cloudpickle.loads(func_data["function"])

        args = pickle.loads(func_data["args"])
        kwargs = pickle.loads(func_data["kwargs"])

        return func, args, kwargs
    else:
        raise ValueError("Invalid function data format")


def get_environment_requirements() -> Dict[str, str]:
    """Get current Python environment requirements."""

    requirements = {}

    try:
        # Use pip list --format=freeze to capture all packages including conda-installed ones
        result = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--format=freeze"],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if "==" in line and not line.startswith("-e"):
                    package, version = line.split("==", 1)
                    requirements[package] = version
    except Exception:
        pass

    # Always include essential packages
    essential_packages = ["cloudpickle", "dill"]
    for pkg in essential_packages:
        if pkg not in requirements:
            try:
                mod = importlib.import_module(pkg)
                if hasattr(mod, "__version__"):
                    requirements[pkg] = mod.__version__
            except ImportError:
                pass

    return requirements


def get_environment_info() -> str:
    """Get current Python environment information as string (for compatibility)."""
    try:
        # Use pip list --format=freeze to capture all packages including conda-installed ones
        result = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--format=freeze"],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass

    return ""


def is_uv_available() -> bool:
    """Check if uv package manager is available."""
    try:
        result = subprocess.run(
            ["uv", "--version"], capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def is_conda_available() -> bool:
    """Check if conda package manager is available."""
    try:
        result = subprocess.run(
            ["conda", "--version"], capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def get_package_manager_command(config: ClusterConfig) -> str:
    """
    Get the appropriate package manager command based on configuration.

    Args:
        config: Cluster configuration

    Returns:
        Package manager command (pip, uv, or conda)
    """
    if config.package_manager == "uv":
        return "uv pip"
    elif config.package_manager == "conda":
        return "conda"
    elif config.package_manager == "auto":
        # Auto-detect: prefer uv if available, then conda, fallback to pip
        if is_uv_available():
            return "uv pip"
        elif is_conda_available():
            return "conda"
        else:
            return "pip"
    else:
        # Default to pip (handles "pip" and any unrecognized values)
        return "pip"


def setup_environment(
    work_dir: str, requirements: Dict[str, str], config: ClusterConfig
) -> str:
    """
    Setup Python environment on cluster.

    Args:
        work_dir: Working directory path
        requirements: Package requirements
        config: Cluster configuration

    Returns:
        Path to Python executable
    """

    if config.conda_env_name:
        # Use existing conda environment
        return f"conda run -n {config.conda_env_name} python"

    # Get package manager to determine environment type
    pkg_manager = get_package_manager_command(config)

    if pkg_manager == "conda":
        # Create conda environment
        env_name = f"clustrix_env_{hash(work_dir) % 10000}"
        env_path = f"{work_dir}/conda_envs/{env_name}"

        setup_commands = [
            f"mkdir -p {work_dir}/conda_envs",
            f"conda create -p {env_path} python={config.python_executable.replace('python', '3.11')} -y",
        ]

        # Install requirements with conda
        if requirements:
            # Create conda environment.yml file
            env_file = f"{work_dir}/environment.yml"
            env_content = f"""name: {env_name}
dependencies:
  - python={config.python_executable.replace('python', '3.11')}
  - pip
  - pip:"""
            for pkg, version in requirements.items():
                env_content += f"\n    - {pkg}=={version}"

            setup_commands.extend(
                [
                    f"echo '{env_content}' > {env_file}",
                    f"conda env update -p {env_path} -f {env_file}",
                ]
            )

        return f"conda run -p {env_path} python"

    else:
        # Create virtual environment (for pip/uv)
        venv_path = f"{work_dir}/venv"

        setup_commands = [
            f"python -m venv {venv_path}",
            f"source {venv_path}/bin/activate",
        ]

        # Install requirements
        if requirements:
            req_file = f"{work_dir}/requirements.txt"
            req_content = "\n".join(
                [f"{pkg}=={version}" for pkg, version in requirements.items()]
            )

            # This would need to be written to remote file
            setup_commands.extend(
                [
                    f"echo '{req_content}' > {req_file}",
                    f"{venv_path}/bin/{pkg_manager} install -r {req_file}",
                ]
            )

        return f"{venv_path}/bin/python"


def setup_python_compatible_environment(
    ssh_client,
    work_dir: str,
    requirements: Dict[str, str],
    config: Optional[ClusterConfig] = None,
) -> str:
    """Setup a Python-compatible environment on remote cluster.

    This function creates a separate venv with a compatible Python version
    to ensure cross-version compatibility for function serialization.

    Args:
        ssh_client: SSH client connection
        work_dir: Remote working directory
        requirements: Package requirements
        config: Cluster configuration

    Returns:
        Path to the compatible Python executable
    """
    if config is None:
        from .config import get_config

        config = get_config()

    # Try to detect available Python versions on the remote system
    detect_cmd = "python3 --version 2>/dev/null || python --version 2>/dev/null || echo 'no python'"
    stdin, stdout, stderr = ssh_client.exec_command(detect_cmd)
    python_version_output = stdout.read().decode().strip()

    # Check if we can create a compatible venv
    compatible_python = None

    # Try different Python versions in order of preference
    python_candidates = [
        "python3.9",
        "python3.8",
        "python3.7",
        "python3.6",
        "python3",
        "python",
    ]

    for python_cmd in python_candidates:
        test_cmd = (
            f"{python_cmd} -c 'import sys; print(sys.version_info[:2])' 2>/dev/null"
        )
        stdin, stdout, stderr = ssh_client.exec_command(test_cmd)
        version_output = stdout.read().decode().strip()

        if version_output and "(" in version_output:
            try:
                # Extract version tuple
                version_str = version_output.split("(")[1].split(")")[0]
                major, minor = map(int, version_str.split(", ")[:2])

                # Check if version is compatible (3.6+)
                if major == 3 and minor >= 6:
                    compatible_python = python_cmd
                    break
            except:
                continue

    if compatible_python:
        # Create a separate venv with the compatible Python version
        compat_venv_path = f"{work_dir}/compat_venv"

        commands = [
            f"cd {work_dir}",
            f"{compatible_python} -m venv {compat_venv_path}",
            f"source {compat_venv_path}/bin/activate",
        ]

        # Install requirements in the compatible venv
        if requirements:
            # Filter out requirements that might not be available on older systems
            # and only install essential packages for function execution
            essential_requirements = {}
            for pkg, version in requirements.items():
                if pkg.lower() in [
                    "dill",
                    "cloudpickle",
                    "pickle",
                    "numpy",
                    "pandas",
                    "scipy",
                    "matplotlib",
                ]:
                    essential_requirements[pkg] = version

            if essential_requirements:
                req_content = "\n".join(
                    [
                        f"{pkg}=={version}"
                        for pkg, version in essential_requirements.items()
                    ]
                )

                # Write requirements file
                sftp = ssh_client.open_sftp()
                with sftp.open(f"{work_dir}/compat_requirements.txt", "w") as f:
                    f.write(req_content)
                sftp.close()

                commands.extend(
                    [
                        "pip install --upgrade pip",
                        f"pip install -r {work_dir}/compat_requirements.txt || echo 'Some packages failed to install, continuing...'",
                    ]
                )
            else:
                # Just install essential packages
                commands.extend(
                    [
                        "pip install --upgrade pip",
                        "pip install dill cloudpickle || echo 'Failed to install serialization packages, using built-in pickle'",
                    ]
                )

        # Execute setup commands
        full_command = " && ".join(commands)
        stdin, stdout, stderr = ssh_client.exec_command(full_command)

        # Wait for completion
        exit_status = stdout.channel.recv_exit_status()
        if exit_status == 0:
            return f"{compat_venv_path}/bin/python"
        else:
            # Fall back to original approach
            return setup_remote_environment(ssh_client, work_dir, requirements, config)
    else:
        # Fall back to original approach
        return setup_remote_environment(ssh_client, work_dir, requirements, config)


def setup_remote_environment(
    ssh_client,
    work_dir: str,
    requirements: Dict[str, str],
    config: Optional[ClusterConfig] = None,
) -> str:
    """Setup environment on remote cluster via SSH (original approach)."""

    # Get appropriate package manager
    if config is None:
        from .config import get_config

        config = get_config()

    pkg_manager = get_package_manager_command(config)

    if pkg_manager == "conda":
        # Create conda environment
        env_name = f"clustrix_env_{hash(work_dir) % 10000}"
        env_path = f"{work_dir}/conda_envs/{env_name}"

        commands = [
            f"cd {work_dir}",
            "mkdir -p conda_envs",
            f"conda create -p {env_path} python={config.python_executable.replace('python', '3.11')} -y",
        ]

        if requirements:
            # Create conda environment.yml file
            env_content = f"""name: {env_name}
dependencies:
  - python={config.python_executable.replace('python', '3.11')}
  - pip
  - pip:"""
            for pkg, version in requirements.items():
                env_content += f"\n    - {pkg}=={version}"

            # Write environment file
            sftp = ssh_client.open_sftp()
            with sftp.open(f"{work_dir}/environment.yml", "w") as f:
                f.write(env_content)
            sftp.close()

            commands.append(f"conda env update -p {env_path} -f environment.yml")

    else:
        # Create virtual environment (for pip/uv)
        commands = [f"cd {work_dir}"]

        # Add module loads if specified in config
        if config.module_loads:
            for module in config.module_loads:
                commands.append(f"module load {module}")

        # Add environment variables if specified in config
        if config.environment_variables:
            for var, value in config.environment_variables.items():
                commands.append(f"export {var}={value}")

        # Add pre-execution commands if specified in config
        if config.pre_execution_commands:
            for cmd in config.pre_execution_commands:
                commands.append(cmd)

        # Now create virtual environment using the configured python executable
        python_cmd = config.python_executable if config.python_executable else "python"
        commands.extend(
            [
                f"{python_cmd} -m venv venv",
                "source venv/bin/activate",
            ]
        )

        if requirements:
            # Create requirements file
            req_content = "\n".join(
                [f"{pkg}=={version}" for pkg, version in requirements.items()]
            )

            # Write requirements file
            sftp = ssh_client.open_sftp()
            with sftp.open(f"{work_dir}/requirements.txt", "w") as f:
                f.write(req_content)
            sftp.close()

            commands.append(f"{pkg_manager} install -r requirements.txt")

    # Execute setup commands
    full_command = " && ".join(commands)
    stdin, stdout, stderr = ssh_client.exec_command(full_command)

    # Wait for completion
    exit_status = stdout.channel.recv_exit_status()
    if exit_status != 0:
        error = stderr.read().decode()
        raise RuntimeError(f"Environment setup failed: {error}")


def create_job_script(
    cluster_type: str,
    job_config: Dict[str, Any],
    remote_job_dir: str,
    config: ClusterConfig,
) -> str:
    """Create job submission script for different cluster types."""

    if cluster_type == "slurm":
        return _create_slurm_script(job_config, remote_job_dir, config)
    elif cluster_type == "pbs":
        return _create_pbs_script(job_config, remote_job_dir, config)
    elif cluster_type == "sge":
        return _create_sge_script(job_config, remote_job_dir, config)
    elif cluster_type == "ssh":
        return _create_ssh_script(job_config, remote_job_dir, config)
    else:
        raise ValueError(f"Unsupported cluster type: {cluster_type}")


def _create_slurm_script(
    job_config: Dict[str, Any], remote_job_dir: str, config: ClusterConfig
) -> str:
    """Create SLURM job script."""

    script_lines = [
        "#!/bin/bash",
        "#SBATCH --job-name=clustrix",
        f"#SBATCH --output={remote_job_dir}/slurm-%j.out",
        f"#SBATCH --error={remote_job_dir}/slurm-%j.err",
        f"#SBATCH --cpus-per-task={job_config['cores']}",
        f"#SBATCH --mem={job_config['memory']}",
        f"#SBATCH --time={job_config['time']}",
    ]

    if job_config.get("partition"):
        script_lines.append(f"#SBATCH --partition={job_config['partition']}")

    # Add environment setup
    if config.module_loads:
        for module in config.module_loads:
            script_lines.append(f"module load {module}")

    if config.environment_variables:
        for var, value in config.environment_variables.items():
            script_lines.append(f"export {var}={value}")

    if config.pre_execution_commands:
        for cmd in config.pre_execution_commands:
            script_lines.append(cmd)

    # Add execution commands
    python_cmd = config.python_executable if config.python_executable else "python"
    script_lines.extend(
        [
            f"cd {remote_job_dir}",
            "source venv/bin/activate",
            f'{python_cmd} -c "',
            "import pickle",
            "import sys",
            "import traceback",
            "",
            "try:",
            "    import dill",
            "except ImportError:",
            "    dill = None",
            "try:",
            "    import cloudpickle",
            "except ImportError:",
            "    cloudpickle = None",
            "",
            "try:",
            "    with open('function_data.pkl', 'rb') as f:",
            "        data = pickle.load(f)",
            "    ",
            "    # Try dill first, then cloudpickle",
            "    try:",
            "        func = dill.loads(data['function']) if dill else None",
            "    except:",
            "        func = cloudpickle.loads(data['function']) if cloudpickle else None",
            "    ",
            "    args = pickle.loads(data['args'])",
            "    kwargs = pickle.loads(data['kwargs'])",
            "    ",
            "    result = func(*args, **kwargs)",
            "    ",
            "    with open('result.pkl', 'wb') as f:",
            "        pickle.dump(result, f, protocol=4)",
            "        ",
            "except Exception as e:",
            "    with open('error.pkl', 'wb') as f:",
            "        pickle.dump({'error': str(e), 'traceback': traceback.format_exc()}, f, protocol=4)",
            "    raise",
            '"',
        ]
    )

    return "\n".join(script_lines)


def _create_pbs_script(
    job_config: Dict[str, Any], remote_job_dir: str, config: ClusterConfig
) -> str:
    """Create PBS job script."""

    script_lines = [
        "#!/bin/bash",
        "#PBS -N clustrix",
        f"#PBS -o {remote_job_dir}/job.out",
        f"#PBS -e {remote_job_dir}/job.err",
        f"#PBS -l nodes=1:ppn={job_config['cores']}",
        f"#PBS -l mem={job_config['memory']}",
        f"#PBS -l walltime={job_config['time']}",
    ]

    if job_config.get("queue"):
        script_lines.append(f"#PBS -q {job_config['queue']}")

    # Add similar execution logic as SLURM
    script_lines.extend(
        [
            f"cd {remote_job_dir}",
            "source venv/bin/activate",
            "python execute_function.py",
        ]
    )

    return "\n".join(script_lines)


def _create_sge_script(
    job_config: Dict[str, Any], remote_job_dir: str, config: ClusterConfig
) -> str:
    """Create SGE job script."""

    script_lines = [
        "#!/bin/bash",
        "#$ -N clustrix",
        f"#$ -o {remote_job_dir}/job.out",
        f"#$ -e {remote_job_dir}/job.err",
        f"#$ -pe smp {job_config['cores']}",
        f"#$ -l h_vmem={job_config['memory']}",
        f"#$ -l h_rt={job_config['time']}",
        "#$ -cwd",
        "",
        f"cd {remote_job_dir}",
        "source venv/bin/activate",
        f'{config.python_executable if config.python_executable else "python"} -c "',
        "import pickle",
        "import sys",
        "import traceback",
        "",
        "try:",
        "    import dill",
        "except ImportError:",
        "    dill = None",
        "try:",
        "    import cloudpickle",
        "except ImportError:",
        "    cloudpickle = None",
        "",
        "try:",
        "    with open('function_data.pkl', 'rb') as f:",
        "        data = pickle.load(f)",
        "    ",
        "    # Try dill first, then cloudpickle, then source code",
        "    func = None",
        "    if dill:",
        "        try:",
        "            func = dill.loads(data['function'])",
        "        except Exception as e:",
        "            pass",
        "    if func is None and cloudpickle:",
        "        try:",
        "            func = cloudpickle.loads(data['function'])",
        "        except Exception as e:",
        "            pass",
        "    if func is None and data.get('function_source'):",
        "        try:",
        "            import textwrap",
        "            source = data['function_source']",
        "            # Execute the source code to create the function",
        "            namespace = {}",
        "            exec(source, namespace)",
        "            # Get the function name from func_info",
        "            func_name = data['func_info']['name']",
        "            func = namespace[func_name]",
        "        except Exception as e:",
        "            pass",
        "    if func is None:",
        "        error_msg = 'Could not deserialize function with dill, cloudpickle, or source code. '",
        "        error_msg += f'dill available: {dill is not None}, cloudpickle available: {cloudpickle is not None}, '",
        "        raise RuntimeError('Could not deserialize function with dill, cloudpickle, or source code')",
        "    ",
        "    args = pickle.loads(data['args'])",
        "    kwargs = pickle.loads(data['kwargs'])",
        "    ",
        "    result = func(*args, **kwargs)",
        "    ",
        "    with open('result.pkl', 'wb') as f:",
        "        pickle.dump(result, f, protocol=4)",
        "except Exception as e:",
        "    with open('error.pkl', 'wb') as f:",
        "        pickle.dump({'error': str(e), 'traceback': traceback.format_exc()}, f, protocol=4)",
        "    raise",
        '"',
    ]

    return "\n".join(script_lines)


def _create_ssh_script(
    job_config: Dict[str, Any], remote_job_dir: str, config: ClusterConfig
) -> str:
    """Create simple execution script for SSH."""

    python_cmd = config.python_executable if config.python_executable else "python"

    # Check if we have a compatible venv setup
    if "compat_venv" in python_cmd:
        # Use the compatible venv directly
        script_lines = [
            "#!/bin/bash",
            f"cd {remote_job_dir}",
            f"source {remote_job_dir}/compat_venv/bin/activate",
        ]
    else:
        # Use the original venv setup
        script_lines = [
            "#!/bin/bash",
            f"cd {remote_job_dir}",
            "source venv/bin/activate",
        ]

    # Add the Python execution part
    script_lines.extend(
        [
            f'{python_cmd} -c "',
            "import pickle",
            "import sys",
            "import traceback",
            "",
            "try:",
            "    import dill",
            "except ImportError:",
            "    dill = None",
            "try:",
            "    import cloudpickle",
            "except ImportError:",
            "    cloudpickle = None",
            "",
            "try:",
            "    with open('function_data.pkl', 'rb') as f:",
            "        data = pickle.load(f)",
            "    ",
            "    # Try dill first, then cloudpickle, then source code",
            "    func = None",
            "    if dill:",
            "        try:",
            "            func = dill.loads(data['function'])",
            "        except Exception as e:",
            "            pass",
            "    if func is None and cloudpickle:",
            "        try:",
            "            func = cloudpickle.loads(data['function'])",
            "        except Exception as e:",
            "            pass",
            "    if func is None and data.get('function_source'):",
            "        try:",
            "            import textwrap",
            "            source = data['function_source']",
            "            # Execute the source code to create the function",
            "            namespace = {}",
            "            exec(source, namespace)",
            "            # Get the function name from func_info",
            "            func_name = data['func_info']['name']",
            "            func = namespace[func_name]",
            "        except Exception as e:",
            "            pass",
            "    if func is None:",
            "        error_msg = 'Could not deserialize function with dill, cloudpickle, or source code. '",
            "        error_msg += f'dill available: {dill is not None}, cloudpickle available: {cloudpickle is not None}, '",
            "        raise RuntimeError('Could not deserialize function with dill, cloudpickle, or source code')",
            "    ",
            "    args = pickle.loads(data['args'])",
            "    kwargs = pickle.loads(data['kwargs'])",
            "    ",
            "    result = func(*args, **kwargs)",
            "    ",
            "    with open('result.pkl', 'wb') as f:",
            "        pickle.dump(result, f, protocol=4)",
            "        ",
            "except Exception as e:",
            "    with open('error.pkl', 'wb') as f:",
            "        pickle.dump({'error': str(e), 'traceback': traceback.format_exc()}, f, protocol=4)",
            "    sys.exit(1)",
            '"',
        ]
    )

    return "\n".join(script_lines)
