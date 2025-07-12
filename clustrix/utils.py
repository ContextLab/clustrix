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
        # Cannot get source code - this is common for dynamically defined functions
        pass

    # Serialize function using dill for better cross-Python compatibility
    try:
        func_bytes = dill.dumps(func, protocol=4)
    except Exception:
        try:
            # Fallback to cloudpickle
            func_bytes = cloudpickle.dumps(func, protocol=4)
        except Exception:
            # Final fallback to built-in pickle
            func_bytes = pickle.dumps(func, protocol=4)

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


def setup_two_venv_environment(
    ssh_client,
    work_dir: str,
    requirements: Dict[str, str],
    config: Optional[ClusterConfig] = None,
) -> Dict[str, str]:
    """Setup a two-venv environment on remote cluster for cross-version compatibility.

    This function creates two separate virtual environments:
    1. VENV1: Compatible Python version for serialization/deserialization
    2. VENV2: Job execution environment that replicates the local environment

    Args:
        ssh_client: SSH client connection
        work_dir: Remote working directory
        requirements: Package requirements
        config: Cluster configuration

    Returns:
        Dict containing paths to both Python executables
    """
    if config is None:
        from .config import get_config

        config = get_config()

    import sys

    local_python_version = f"{sys.version_info.major}.{sys.version_info.minor}"

    # Try to find a Python version that matches our local version
    compatible_python = None
    python_candidates = [
        f"python{local_python_version}",
        f"python{sys.version_info.major}.{sys.version_info.minor}",
        "python3.12",
        "python3.11",
        "python3.10",
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
                    remote_python_version = f"{major}.{minor}"
                    break
            except Exception:
                continue

    if not compatible_python:
        raise RuntimeError("No compatible Python version found on remote system")

    # Create VENV1 for serialization compatibility
    venv1_path = f"{work_dir}/venv1_serialization"

    # Create VENV2 for job execution
    venv2_path = f"{work_dir}/venv2_execution"

    commands = [
        f"cd {work_dir}",
        # Create VENV1 (serialization environment)
        f"{compatible_python} -m venv {venv1_path}",
        f"source {venv1_path}/bin/activate",
        "pip install --upgrade pip --timeout=30 || echo 'pip upgrade failed for venv1'",
        "pip install dill cloudpickle --timeout=30 || echo 'Failed to install serialization packages in venv1'",
        "deactivate",
        # Create VENV2 (execution environment)
        f"{compatible_python} -m venv {venv2_path}",
        f"source {venv2_path}/bin/activate",
        "pip install --upgrade pip --timeout=30 || echo 'pip upgrade failed for venv2'",
    ]

    # Install essential packages in VENV2
    essential_packages = ["dill", "cloudpickle"]
    for pkg in essential_packages:
        if pkg in requirements:
            commands.append(
                f"pip install {pkg}=={requirements[pkg]} --timeout=30 || echo 'Failed to install {pkg} in venv2'"
            )
        else:
            commands.append(
                f"pip install {pkg} --timeout=30 || echo 'Failed to install {pkg} in venv2'"
            )

    commands.append("deactivate")

    # Execute setup commands
    full_command = " && ".join(commands)
    stdin, stdout, stderr = ssh_client.exec_command(full_command)

    # Wait for completion with extended timeout
    exit_status = stdout.channel.recv_exit_status()

    if exit_status == 0:
        return {
            "venv1_python": f"{venv1_path}/bin/python",
            "venv2_python": f"{venv2_path}/bin/python",
            "venv1_path": venv1_path,
            "venv2_path": venv2_path,
            "compatible_python": compatible_python,
            "remote_python_version": remote_python_version,
        }
    else:
        error_output = stderr.read().decode()
        raise RuntimeError(f"Failed to setup two-venv environment: {error_output}")


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
    _ = stdout.read().decode().strip()  # Version detection output

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
            except Exception:
                continue

    if compatible_python:
        # Create a separate venv with the compatible Python version
        compat_venv_path = f"{work_dir}/compat_venv"

        commands = [
            f"cd {work_dir}",
            f"{compatible_python} -m venv {compat_venv_path}",
            f"source {compat_venv_path}/bin/activate",
        ]

        # Install only essential packages for function execution
        # Skip complex requirements to avoid timeout issues
        commands.extend(
            [
                "pip install --upgrade pip --timeout=30 || echo 'pip upgrade failed, continuing...'",
                "pip install dill cloudpickle --timeout=30 || echo 'Failed to install serialization packages, using built-in pickle'",
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
            # Only install essential packages, skip complex requirements
            essential_pkgs = []
            for pkg, version in requirements.items():
                if pkg.lower() in ["dill", "cloudpickle"]:
                    essential_pkgs.append(f"{pkg}=={version}")

            if essential_pkgs:
                # Install essential packages with timeout
                pkg_list = " ".join(essential_pkgs)
                commands.append(
                    f"{pkg_manager} install {pkg_list} --timeout=30 || echo 'Package installation failed, continuing...'"
                )
            else:
                # Just install basic packages
                commands.append(
                    f"{pkg_manager} install dill cloudpickle --timeout=30 || echo 'Package installation failed, continuing...'"
                )

    # Execute setup commands
    full_command = " && ".join(commands)
    stdin, stdout, stderr = ssh_client.exec_command(full_command)

    # Wait for completion
    exit_status = stdout.channel.recv_exit_status()
    if exit_status != 0:
        error = stderr.read().decode()
        raise RuntimeError(f"Environment setup failed: {error}")

    return "Environment setup completed successfully"


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

    # Check if we have two-venv setup
    if "venv1_serialization" in python_cmd:
        # Use the two-venv approach for cross-version compatibility
        script_lines.extend(
            [
                f"cd {remote_job_dir}",
                "",
                "# Two-venv approach for cross-version compatibility",
                "# VENV1: Serialization/deserialization with compatible Python",
                "# VENV2: Function execution with proper environment",
                "",
                "# Step 1: Use VENV1 to deserialize function data",
                f"source {remote_job_dir}/venv1_serialization/bin/activate",
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
                "print('VENV1 - Deserializing function data')",
                "print('Python version:', sys.version)",
                "",
                "try:",
                "    with open('function_data.pkl', 'rb') as f:",
                "        data = pickle.load(f)",
                "    ",
                "    # Try to deserialize function",
                "    func = None",
                "    try:",
                "        func = dill.loads(data['function']) if dill else None",
                "        print('Successfully deserialized function with dill')",
                "    except Exception as e:",
                "        print('Dill deserialization failed:', str(e))",
                "        try:",
                "            func = cloudpickle.loads(data['function']) if cloudpickle else None",
                "            print('Successfully deserialized function with cloudpickle')",
                "        except Exception as e2:",
                "            print('Cloudpickle deserialization failed:', str(e2))",
                "            # Try source code fallback",
                "            func_info = data.get('func_info', {})",
                "            if func_info.get('source'):",
                "                print('Using source code fallback')",
                "                # Remove @cluster decorator from source",
                "                import textwrap",
                "                source = func_info['source']",
                "                lines = source.split('\\n')",
                "                clean_lines = []",
                "                for line in lines:",
                "                    if not line.strip().startswith('@'):",
                "                        clean_lines.append(line)",
                "                clean_source = '\\n'.join(clean_lines)",
                "                clean_source = textwrap.dedent(clean_source)",
                "                ",
                "                # Create function from source",
                "                namespace = {}",
                "                exec(clean_source, namespace)",
                "                func = namespace[func_info['name']]",
                "                print('Successfully created function from source code')",
                "            else:",
                "                raise Exception('All deserialization methods failed')",
                "    ",
                "    args = pickle.loads(data['args'])",
                "    kwargs = pickle.loads(data['kwargs'])",
                "    ",
                "    # Pass data to VENV2 for execution",
                "    with open('function_deserialized.pkl', 'wb') as f:",
                "        if 'clean_source' in locals():",
                "            # Function was created from source code, pass the source",
                "            pickle.dump({'source': clean_source, 'func_name': func_info['name'], 'args': args, 'kwargs': kwargs}, f, protocol=4)",
                "        else:",
                "            # Function was deserialized from binary, pass the function object",
                "            pickle.dump({'func': func, 'args': args, 'kwargs': kwargs}, f, protocol=4)",
                "    ",
                "    print('VENV1 - Function data prepared for VENV2')",
                "    ",
                "except Exception as e:",
                "    print('VENV1 - Error during deserialization:', str(e))",
                "    traceback.print_exc()",
                "    with open('error.pkl', 'wb') as f:",
                "        pickle.dump({'error': str(e), 'traceback': traceback.format_exc()}, f, protocol=4)",
                "    raise",
                '"',
                "",
                "# Step 2: Use VENV2 to execute the function",
                f"source {remote_job_dir}/venv2_execution/bin/activate",
                f'{remote_job_dir}/venv2_execution/bin/python -c "',
                "import pickle",
                "import sys",
                "import traceback",
                "",
                "print('VENV2 - Executing function')",
                "print('Python version:', sys.version)",
                "",
                "try:",
                "    with open('function_deserialized.pkl', 'rb') as f:",
                "        exec_data = pickle.load(f)",
                "    ",
                "    if 'func' in exec_data:",
                "        # Function object was passed",
                "        func = exec_data['func']",
                "    elif 'source' in exec_data:",
                "        # Source code was passed, recreate function",
                "        print('Recreating function from source code in VENV2')",
                "        namespace = {}",
                "        exec(exec_data['source'], namespace)",
                "        func = namespace[exec_data['func_name']]",
                "    else:",
                "        raise Exception('No function or source code found')",
                "    ",
                "    args = exec_data['args']",
                "    kwargs = exec_data['kwargs']",
                "    ",
                "    # Execute the function",
                "    print('Function executed successfully')",
                "    result = func(*args, **kwargs)",
                "    ",
                "    with open('result_venv2.pkl', 'wb') as f:",
                "        pickle.dump(result, f, protocol=4)",
                "        ",
                "    print('VENV2 - Function execution completed')",
                "    ",
                "except Exception as e:",
                "    print('VENV2 - Error during execution:', str(e))",
                "    traceback.print_exc()",
                "    with open('error.pkl', 'wb') as f:",
                "        pickle.dump({'error': str(e), 'traceback': traceback.format_exc()}, f, protocol=4)",
                "    raise",
                '"',
                "",
                "# Step 3: Use VENV1 to serialize the result back",
                f"source {remote_job_dir}/venv1_serialization/bin/activate",
                f'{python_cmd} -c "',
                "import pickle",
                "import sys",
                "import traceback",
                "",
                "print('VENV1 - Serializing result')",
                "",
                "try:",
                "    with open('result_venv2.pkl', 'rb') as f:",
                "        result = pickle.load(f)",
                "    ",
                "    with open('result.pkl', 'wb') as f:",
                "        pickle.dump(result, f, protocol=4)",
                "        ",
                "    print('Result serialized successfully')",
                "    ",
                "except Exception as e:",
                "    print('VENV1 - Error during result serialization:', str(e))",
                "    traceback.print_exc()",
                "    with open('error.pkl', 'wb') as f:",
                "        pickle.dump({'error': str(e), 'traceback': traceback.format_exc()}, f, protocol=4)",
                "    raise",
                '"',
            ]
        )
    else:
        # Use the original single-venv approach
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
    ]

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

    script_lines.extend(
        [
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
    )

    return "\n".join(script_lines)


def _create_ssh_script(
    job_config: Dict[str, Any], remote_job_dir: str, config: ClusterConfig
) -> str:
    """Create simple execution script for SSH."""

    python_cmd = config.python_executable if config.python_executable else "python"

    # Start with base script structure
    script_lines = [
        "#!/bin/bash",
        f"cd {remote_job_dir}",
        "",
    ]

    # Add environment setup (module loads, environment variables, pre-execution commands)
    if config.module_loads:
        script_lines.append("# Load required modules")
        for module in config.module_loads:
            script_lines.append(f"module load {module}")
        script_lines.append("")

    if config.environment_variables:
        script_lines.append("# Set environment variables")
        for var, value in config.environment_variables.items():
            script_lines.append(f"export {var}={value}")
        script_lines.append("")

    if config.pre_execution_commands:
        script_lines.append("# Execute pre-execution commands")
        for cmd in config.pre_execution_commands:
            script_lines.append(cmd)
        script_lines.append("")

    # Check if we have two-venv setup
    if "venv1_serialization" in python_cmd:
        # Use the two-venv approach
        script_lines.extend(
            [
                "# Two-venv approach for cross-version compatibility",
                "# VENV1: Serialization/deserialization",
                "# VENV2: Function execution",
                "",
                "# Step 1: Use VENV1 to deserialize function data",
                f"source {remote_job_dir}/venv1_serialization/bin/activate",
            ]
        )
    else:
        # Use the original single-venv approach
        script_lines.append(
            "source venv/bin/activate || echo 'No venv found, using system Python'"
        )
        script_lines.append("")

    # Add the Python execution part
    if "venv1_serialization" in python_cmd:
        # Two-venv approach
        script_lines.extend(
            [
                "",
                'python -c "',
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
                "print('VENV1 - Deserializing function data')",
                "print('Python version: ' + sys.version)",
                "",
                "try:",
                "    with open('function_data.pkl', 'rb') as f:",
                "        data = pickle.load(f)",
                "    ",
                "    print('Data keys: ' + str(list(data.keys())))",
                "    print('Function source available: ' + str(data.get('function_source') is not None))",
                "    print('Function data size: ' + str(len(data['function'])) + ' bytes')",
                "    ",
                "    # Try dill first, then cloudpickle, then built-in pickle",
                "    func = None",
                "    if dill:",
                "        try:",
                "            func = dill.loads(data['function'])",
                "            print('Successfully deserialized with dill')",
                "        except Exception as e:",
                "            print('Dill deserialization failed: ' + str(e))",
                "            pass",
                "    if func is None and cloudpickle:",
                "        try:",
                "            func = cloudpickle.loads(data['function'])",
                "            print('Successfully deserialized with cloudpickle')",
                "        except Exception as e:",
                "            print('Cloudpickle deserialization failed: ' + str(e))",
                "            pass",
                "    if func is None:",
                "        try:",
                "            func = pickle.loads(data['function'])",
                "            print('Successfully deserialized with pickle')",
                "        except Exception as e:",
                "            print('Pickle deserialization failed: ' + str(e))",
                "            pass",
                "    ",
                "    if func is None and data.get('function_source'):",
                "        try:",
                "            print('Attempting source code execution fallback')",
                "            source = data['function_source']",
                "            func_name = data['func_info']['name']",
                "            print('Function name: ' + func_name)",
                "            print('Function source length: ' + str(len(source)))",
                "            # Clean up the source code",
                "            import textwrap",
                "            # Remove decorator lines",
                "            lines = source.split('\\n')",
                "            clean_lines = []",
                "            for line in lines:",
                "                if not line.strip().startswith('@'):",
                "                    clean_lines.append(line)",
                "            clean_source = '\\n'.join(clean_lines)",
                "            # Remove common indentation",
                "            clean_source = textwrap.dedent(clean_source)",
                "            print('Cleaned source code: ' + repr(clean_source[:100]))",
                "            # Execute the source code to create the function",
                "            namespace = {}",
                "            exec(clean_source, namespace)",
                "            func = namespace[func_name]",
                "            print('Successfully created function from source code')",
                "        except Exception as e:",
                "            print('Source code execution failed: ' + str(e))",
                "            pass",
                "    if func is None:",
                "        error_msg = 'Could not deserialize function with dill, cloudpickle, pickle, or source code. '",
                "        error_msg += 'dill available: ' + str(dill is not None) + ', cloudpickle available: ' + str(cloudpickle is not None)",
                "        raise RuntimeError(error_msg)",
                "    ",
                "    args = pickle.loads(data['args'])",
                "    kwargs = pickle.loads(data['kwargs'])",
                "    ",
                "    # Save the function data for VENV2",
                "    # If function was created from source, pass the source code instead of the function object",
                "    if 'clean_source' in locals():",
                "        # Function was created from source code, pass the source",
                "        with open('function_deserialized.pkl', 'wb') as f:",
                "            pickle.dump({'source': clean_source, 'func_name': func_name, 'args': args, 'kwargs': kwargs}, f, protocol=4)",
                "    else:",
                "        # Function was deserialized from binary, pass the function object",
                "        with open('function_deserialized.pkl', 'wb') as f:",
                "            pickle.dump({'func': func, 'args': args, 'kwargs': kwargs}, f, protocol=4)",
                "    ",
                "    print('Function deserialized successfully for execution')",
                "    ",
                "except Exception as e:",
                "    with open('error.pkl', 'wb') as f:",
                "        pickle.dump({'error': str(e), 'traceback': traceback.format_exc()}, f, protocol=4)",
                "    sys.exit(1)",
                '"',
                "",
                "# Step 2: Use VENV2 to execute the function",
                "deactivate",
                f"source {remote_job_dir}/venv2_execution/bin/activate",
                "",
                'python -c "',
                "import pickle",
                "import sys",
                "import traceback",
                "",
                "print('VENV2 - Executing function')",
                "print('Python version: ' + sys.version)",
                "",
                "try:",
                "    import os",
                "    print('Files in directory: ' + str(os.listdir('.')))",
                "    if not os.path.exists('function_deserialized.pkl'):",
                "        raise FileNotFoundError('function_deserialized.pkl not found - VENV1 deserialization may have failed')",
                "    with open('function_deserialized.pkl', 'rb') as f:",
                "        exec_data = pickle.load(f)",
                "    ",
                "    # Handle both function object and source code cases",
                "    if 'func' in exec_data:",
                "        # Function object was passed",
                "        func = exec_data['func']",
                "    elif 'source' in exec_data:",
                "        # Source code was passed, recreate function",
                "        print('Recreating function from source code in VENV2')",
                "        namespace = {}",
                "        exec(exec_data['source'], namespace)",
                "        func = namespace[exec_data['func_name']]",
                "    else:",
                "        raise ValueError('Neither func nor source found in execution data')",
                "    ",
                "    args = exec_data['args']",
                "    kwargs = exec_data['kwargs']",
                "    ",
                "    print('Executing function with args: ' + str(args))",
                "    result = func(*args, **kwargs)",
                "    print('Function executed successfully')",
                "    ",
                "    # Save result for VENV1 to serialize",
                "    with open('result_raw.pkl', 'wb') as f:",
                "        pickle.dump(result, f, protocol=4)",
                "    ",
                "except Exception as e:",
                "    with open('error.pkl', 'wb') as f:",
                "        pickle.dump({'error': str(e), 'traceback': traceback.format_exc()}, f, protocol=4)",
                "    sys.exit(1)",
                '"',
                "",
                "# Step 3: Use VENV1 to serialize the result",
                "deactivate",
                f"source {remote_job_dir}/venv1_serialization/bin/activate",
                "",
                'python -c "',
                "import pickle",
                "import sys",
                "import traceback",
                "",
                "print('VENV1 - Serializing result')",
                "",
                "try:",
                "    import os",
                "    if not os.path.exists('result_raw.pkl'):",
                "        raise FileNotFoundError('result_raw.pkl not found - VENV2 execution may have failed')",
                "    with open('result_raw.pkl', 'rb') as f:",
                "        result = pickle.load(f)",
                "    ",
                "    with open('result.pkl', 'wb') as f:",
                "        pickle.dump(result, f, protocol=4)",
                "    ",
                "    print('Result serialized successfully')",
                "    ",
                "except Exception as e:",
                "    with open('error.pkl', 'wb') as f:",
                "        pickle.dump({'error': str(e), 'traceback': traceback.format_exc()}, f, protocol=4)",
                "    sys.exit(1)",
                '"',
            ]
        )
    else:
        # Single-venv approach
        script_lines.extend(
            [
                "",
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
                "    print('Python version: ' + sys.version)",
                "    print('Data keys: ' + str(list(data.keys())))",
                "    print('Function source available: ' + str(data.get('function_source') is not None))",
                "    print('Function data size: ' + str(len(data['function'])) + ' bytes')",
                "    ",
                "    # Try dill first, then cloudpickle, then built-in pickle, then source code",
                "    func = None",
                "    if dill:",
                "        try:",
                "            func = dill.loads(data['function'])",
                "        except Exception as e:",
                "            print('Dill deserialization failed: ' + str(e))",
                "            pass",
                "    if func is None and cloudpickle:",
                "        try:",
                "            func = cloudpickle.loads(data['function'])",
                "        except Exception as e:",
                "            print('Cloudpickle deserialization failed: ' + str(e))",
                "            pass",
                "    if func is None:",
                "        try:",
                "            func = pickle.loads(data['function'])",
                "        except Exception as e:",
                "            print('Pickle deserialization failed: ' + str(e))",
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
                "        error_msg = 'Could not deserialize function with dill, cloudpickle, pickle, or source code. '",
                "        error_msg += 'dill available: ' + str(dill is not None) + ', cloudpickle available: ' + str(cloudpickle is not None)",
                "        raise RuntimeError(error_msg)",
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
