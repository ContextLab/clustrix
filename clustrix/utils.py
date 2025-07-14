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
) -> Dict[str, Any]:
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

    # Check if conda is available first - on many clusters, only conda Python works
    conda_available = False
    stdin, stdout, stderr = ssh_client.exec_command("conda --version 2>/dev/null")
    if "conda" in stdout.read().decode():
        conda_available = True
        print("Conda available on remote system, using conda for both venvs")

    if conda_available:
        # Use conda for both VENV1 and VENV2 to ensure compatibility
        compatible_python = "conda"  # Special marker to use conda
        remote_python_version = "3.9"  # Default to 3.9 via conda
    else:
        # Fall back to system Python search
        compatible_python = None
        venv1_python = None

        # Try to find system Python (many clusters don't have this accessible)
        venv1_candidates = [
            f"python{local_python_version}",
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

        for python_cmd in venv1_candidates:
            test_cmd = (
                f"{python_cmd} -c 'import sys; print(sys.version_info[:2])' 2>/dev/null"
            )
            stdin, stdout, stderr = ssh_client.exec_command(test_cmd)
            version_output = stdout.read().decode().strip()

            if version_output and "(" in version_output:
                try:
                    version_str = version_output.split("(")[1].split(")")[0]
                    major, minor = map(int, version_str.split(", ")[:2])

                    if major == 3 and minor >= 6:
                        venv1_python = python_cmd
                        remote_python_version = f"{major}.{minor}"
                        break
                except Exception:
                    continue

        compatible_python = venv1_python

        if not compatible_python:
            raise RuntimeError(
                "No compatible Python version found on remote system. Consider installing conda."
            )

    # Create environment names
    venv1_path = f"{work_dir}/venv1_serialization"
    venv2_path = f"{work_dir}/venv2_execution"
    conda_env1_name = f"clustrix_venv1_{work_dir.split('/')[-1]}"
    conda_env2_name = f"clustrix_venv2_{work_dir.split('/')[-1]}"

    commands = [f"cd {work_dir}"]

    if compatible_python == "conda":
        # Use conda for both VENV1 and VENV2 (preferred for clusters)
        commands.extend(
            [
                # Create VENV1 using conda with Python 3.9 for serialization compatibility
                f"conda create -n {conda_env1_name} python=3.9 -y",
                f"conda run -n {conda_env1_name} pip install --upgrade pip --timeout=30 || echo 'pip upgrade failed for conda venv1'",
                f"conda run -n {conda_env1_name} pip install dill cloudpickle --timeout=30 || echo 'Failed to install serialization packages in conda venv1'",
                # Create VENV2 using conda with Python 3.9 for execution
                f"conda create -n {conda_env2_name} python=3.9 -y",
                f"conda run -n {conda_env2_name} pip install --upgrade pip --timeout=30 || echo 'pip upgrade failed for conda venv2'",
            ]
        )
    else:
        # Fall back to regular venv (less common on clusters)
        commands.extend(
            [
                # Create VENV1 (serialization environment)
                f"{compatible_python} -m venv {venv1_path}",
                f"source {venv1_path}/bin/activate",
                "pip install --upgrade pip --timeout=30 || echo 'pip upgrade failed for venv1'",
                "pip install dill cloudpickle --timeout=30 || echo 'Failed to install serialization packages in venv1'",
                "deactivate",
                # Create VENV2 using regular venv
                f"{compatible_python} -m venv {venv2_path}",
                f"source {venv2_path}/bin/activate",
                "pip install --upgrade pip --timeout=30 || echo 'pip upgrade failed for venv2'",
                "deactivate",
            ]
        )

    # Install essential packages in VENV2
    essential_packages = ["dill", "cloudpickle"]
    for pkg in essential_packages:
        if compatible_python == "conda":
            if pkg in requirements:
                commands.append(
                    f"conda run -n {conda_env2_name} pip install {pkg}=={requirements[pkg]} --timeout=30 || echo 'Failed to install {pkg} in conda venv2'"
                )
            else:
                commands.append(
                    f"conda run -n {conda_env2_name} pip install {pkg} --timeout=30 || echo 'Failed to install {pkg} in conda venv2'"
                )
        else:
            commands.append(f"source {venv2_path}/bin/activate")
            if pkg in requirements:
                commands.append(
                    f"pip install {pkg}=={requirements[pkg]} --timeout=30 || echo 'Failed to install {pkg} in venv2'"
                )
            else:
                commands.append(
                    f"pip install {pkg} --timeout=30 || echo 'Failed to install {pkg} in venv2'"
                )
            commands.append("deactivate")

    # Install packages from local environment requirements (selective)
    if requirements:
        # Filter out essential packages already installed
        remaining_reqs = {
            k: v for k, v in requirements.items() if k not in essential_packages
        }

        # Only install core scientific packages that are commonly needed
        core_scientific_packages = {
            "numpy",
            "scipy",
            "pandas",
            "matplotlib",
            "seaborn",
            "scikit-learn",
            "jupyter",
            "ipython",
            "requests",
        }

        # Filter to only install packages that are in both requirements and core list
        filtered_reqs = {
            k: v
            for k, v in remaining_reqs.items()
            if k.lower() in core_scientific_packages
        }

        if filtered_reqs:
            # Install core scientific packages from local environment
            for pkg, version in filtered_reqs.items():
                if compatible_python == "conda":
                    commands.append(
                        f"conda run -n {conda_env2_name} pip install {pkg}=={version} --timeout=120 || echo 'Failed to install {pkg}=={version} in conda venv2'"
                    )
                else:
                    commands.append(f"source {venv2_path}/bin/activate")
                    commands.append(
                        f"pip install {pkg}=={version} --timeout=120 || echo 'Failed to install {pkg}=={version} in venv2'"
                    )
                    commands.append("deactivate")

    # Add cluster-specific package installations from config
    if hasattr(config, "cluster_packages") and config.cluster_packages:
        commands.append("echo 'Installing cluster-specific packages...'")
        for package_spec in config.cluster_packages:
            if isinstance(package_spec, str):
                # Simple package name or package==version
                if compatible_python == "conda":
                    commands.append(
                        f"conda run -n {conda_env2_name} pip install {package_spec} --timeout=300 || echo 'Failed to install cluster package: {package_spec}'"
                    )
                else:
                    commands.append(f"source {venv2_path}/bin/activate")
                    commands.append(
                        f"pip install {package_spec} --timeout=300 || echo 'Failed to install cluster package: {package_spec}'"
                    )
                    commands.append("deactivate")
            elif isinstance(package_spec, dict):
                # Complex package specification with options
                pkg_name = package_spec.get("package", "")
                pip_args = package_spec.get("pip_args", "")
                timeout = package_spec.get("timeout", 300)

                if pkg_name:
                    if compatible_python == "conda":
                        install_cmd = (
                            f"conda run -n {conda_env2_name} pip install {pkg_name}"
                        )
                    else:
                        commands.append(f"source {venv2_path}/bin/activate")
                        install_cmd = f"pip install {pkg_name}"
                    if pip_args:
                        install_cmd += f" {pip_args}"
                    install_cmd += f" --timeout={timeout} || echo 'Failed to install cluster package: {pkg_name}'"
                    commands.append(install_cmd)
                    if compatible_python != "conda":
                        commands.append("deactivate")

    # Add cluster-specific post-installation commands from config
    if (
        hasattr(config, "venv_post_install_commands")
        and config.venv_post_install_commands
    ):
        commands.append("echo 'Running cluster-specific post-installation commands...'")
        for cmd in config.venv_post_install_commands:
            if compatible_python == "conda":
                # Run post-install commands in conda environment
                commands.append(
                    f"conda run -n {conda_env2_name} {cmd} || echo 'Post-install command failed: {cmd}'"
                )
            else:
                commands.append(f"source {venv2_path}/bin/activate")
                commands.append(f"{cmd} || echo 'Post-install command failed: {cmd}'")
                commands.append("deactivate")

    # Execute setup commands
    full_command = " && ".join(commands)
    stdin, stdout, stderr = ssh_client.exec_command(full_command)

    # Wait for completion with extended timeout
    exit_status = stdout.channel.recv_exit_status()

    if exit_status == 0:
        result: Dict[str, Any] = {
            "compatible_python": compatible_python,
            "remote_python_version": remote_python_version,
        }

        if compatible_python == "conda":
            # Both venvs use conda
            result.update(
                {
                    "venv1_python": f"conda run -n {conda_env1_name} python",
                    "venv1_path": f"conda:{conda_env1_name}",
                    "venv2_python": f"conda run -n {conda_env2_name} python",
                    "venv2_path": f"conda:{conda_env2_name}",
                    "conda_env1_name": conda_env1_name,
                    "conda_env2_name": conda_env2_name,
                    "conda_env_name": conda_env2_name,  # For backward compatibility with job script generation
                    "uses_conda": True,
                }
            )
        else:
            # Both venvs use regular virtualenv
            result.update(
                {
                    "venv1_python": f"{venv1_path}/bin/python",
                    "venv1_path": venv1_path,
                    "venv2_python": f"{venv2_path}/bin/python",
                    "venv2_path": venv2_path,
                    "conda_env1_name": None,
                    "conda_env2_name": None,
                    "uses_conda": False,
                }
            )

        return result
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


def generate_two_venv_execution_commands(
    remote_job_dir: str,
    conda_env1_name: Optional[str] = None,
    conda_env2_name: Optional[str] = None,
) -> list:
    """
    Generate the standardized two-venv execution commands.

    This centralizes the two-venv logic to eliminate code duplication across
    different cluster types (SLURM, SSH, PBS, SGE).

    Args:
        remote_job_dir: Remote working directory path

    Returns:
        List of command strings for two-venv execution
    """
    return [
        "# Two-venv approach for cross-version compatibility",
        "# VENV1: Serialization/deserialization with compatible Python",
        "# VENV2: Function execution with proper environment",
        "",
        "# Step 1: Use VENV1 to deserialize function data",
        (
            f"# Using conda environment {conda_env1_name}"
            if conda_env1_name
            else f"source {remote_job_dir}/venv1_serialization/bin/activate"
        ),
        (
            f'conda run -n {conda_env1_name} python -c "'
            if conda_env1_name
            else 'python -c "'
        ),
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
        ("# No deactivation needed for conda run" if conda_env1_name else "deactivate"),
        (
            f"# Using conda environment {conda_env2_name}"
            if conda_env2_name
            else f"source {remote_job_dir}/venv2_execution/bin/activate"
        ),
        (
            f'conda run -n {conda_env2_name} python -c "'
            if conda_env2_name
            else f'{remote_job_dir}/venv2_execution/bin/python -c "'
        ),
        "import pickle",
        "import sys",
        "import traceback",
        "",
        "print('VENV2 - Executing function')",
        "print('Python version:', sys.version)",
        "",
        "try:",
        "    import os",
        "    if not os.path.exists('function_deserialized.pkl'):",
        "        raise FileNotFoundError('function_deserialized.pkl not found - VENV1 deserialization may have failed')",
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
        "    print('Executing function with args:', args)",
        "    result = func(*args, **kwargs)",
        "    print('Function execution completed successfully')",
        "    ",
        "    # Save result for VENV1 to serialize",
        "    with open('result_raw.pkl', 'wb') as f:",
        "        pickle.dump(result, f, protocol=4)",
        "    ",
        "except Exception as e:",
        "    print('VENV2 - Error during execution:', str(e))",
        "    traceback.print_exc()",
        "    with open('error.pkl', 'wb') as f:",
        "        pickle.dump({'error': str(e), 'traceback': traceback.format_exc()}, f, protocol=4)",
        "    raise",
        '"',
        "",
        "# Step 3: Use VENV1 to serialize the result",
        ("# No deactivation needed for conda run" if conda_env2_name else "deactivate"),
        (
            f"# Using conda environment {conda_env1_name}"
            if conda_env1_name
            else f"source {remote_job_dir}/venv1_serialization/bin/activate"
        ),
        (
            f'conda run -n {conda_env1_name} python -c "'
            if conda_env1_name
            else 'python -c "'
        ),
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
        "    print('Result loaded from VENV2:', type(result))",
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
        "",
    ]


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
    if hasattr(config, "venv_info") and config.venv_info:
        # Use the centralized two-venv approach for cross-version compatibility
        script_lines.append(f"cd {remote_job_dir}")
        conda_env1_name = config.venv_info.get("conda_env1_name", None)
        conda_env2_name = config.venv_info.get("conda_env2_name", None)
        script_lines.extend(
            generate_two_venv_execution_commands(
                remote_job_dir, conda_env1_name, conda_env2_name
            )
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
    if hasattr(config, "venv_info") and config.venv_info:
        # Use the centralized two-venv approach for cross-version compatibility
        conda_env1_name = config.venv_info.get("conda_env1_name", None)
        conda_env2_name = config.venv_info.get("conda_env2_name", None)
        script_lines.extend(
            generate_two_venv_execution_commands(
                remote_job_dir, conda_env1_name, conda_env2_name
            )
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


def detect_gpu_capabilities(
    ssh_client, config: Optional[ClusterConfig] = None
) -> Dict[str, Any]:
    """
    Detect GPU capabilities on remote cluster for job distribution.

    This function is designed to run in VENV1 and provides information
    needed for distributing GPU-enabled jobs across the cluster.

    Args:
        ssh_client: SSH client connection
        config: Cluster configuration

    Returns:
        Dictionary with GPU information including:
        - gpu_available: bool
        - gpu_count: int
        - gpu_devices: List[Dict] with device info
        - cuda_available: bool
        - cuda_version: str
        - pytorch_gpu_support: bool
        - tensorflow_gpu_support: bool
    """
    gpu_info: Dict[str, Any] = {
        "gpu_available": False,
        "gpu_count": 0,
        "gpu_devices": [],
        "cuda_available": False,
        "cuda_version": None,
        "pytorch_gpu_support": False,
        "tensorflow_gpu_support": False,
        "detection_method": "unknown",
        "detection_errors": [],
    }

    # Method 1: Try nvidia-smi (most reliable)
    try:
        stdin, stdout, stderr = ssh_client.exec_command(
            "nvidia-smi --query-gpu=index,name,memory.total,memory.free,compute_cap --format=csv,noheader,nounits 2>/dev/null"
        )
        exit_status = stdout.channel.recv_exit_status()

        if exit_status == 0:
            smi_output = stdout.read().decode().strip()
            if smi_output:
                gpu_info["gpu_available"] = True
                gpu_info["detection_method"] = "nvidia-smi"

                # Parse nvidia-smi output
                devices = []
                for line in smi_output.split("\n"):
                    if line.strip():
                        parts = [p.strip() for p in line.split(",")]
                        if len(parts) >= 5:
                            devices.append(
                                {
                                    "index": int(parts[0]),
                                    "name": parts[1],
                                    "memory_total_mb": int(parts[2]),
                                    "memory_free_mb": int(parts[3]),
                                    "compute_capability": parts[4],
                                }
                            )

                gpu_info["gpu_count"] = len(devices)
                gpu_info["gpu_devices"] = devices
    except Exception as e:
        gpu_info["detection_errors"].append(f"nvidia-smi failed: {str(e)}")

    # Method 2: Check CUDA installation
    try:
        stdin, stdout, stderr = ssh_client.exec_command(
            "nvcc --version 2>/dev/null | grep 'release' | sed 's/.*release \\([0-9.]*\\).*/\\1/'"
        )
        exit_status = stdout.channel.recv_exit_status()

        if exit_status == 0:
            cuda_version = stdout.read().decode().strip()
            if cuda_version:
                gpu_info["cuda_available"] = True
                gpu_info["cuda_version"] = cuda_version
    except Exception as e:
        gpu_info["detection_errors"].append(f"CUDA detection failed: {str(e)}")

    # Method 3: Check /proc/driver/nvidia if nvidia-smi fails
    if not gpu_info["gpu_available"]:
        try:
            stdin, stdout, stderr = ssh_client.exec_command(
                "ls -la /proc/driver/nvidia/gpus/ 2>/dev/null | wc -l"
            )
            exit_status = stdout.channel.recv_exit_status()

            if exit_status == 0:
                gpu_count_str = stdout.read().decode().strip()
                try:
                    # Subtract 2 for . and .. entries
                    gpu_count = max(0, int(gpu_count_str) - 2)
                    if gpu_count > 0:
                        gpu_info["gpu_available"] = True
                        gpu_info["gpu_count"] = gpu_count
                        gpu_info["detection_method"] = "/proc/driver/nvidia"
                except ValueError:
                    pass
        except Exception as e:
            gpu_info["detection_errors"].append(
                f"/proc/driver/nvidia detection failed: {str(e)}"
            )

    # Method 4: Check for GPU via lspci (fallback)
    if not gpu_info["gpu_available"]:
        try:
            stdin, stdout, stderr = ssh_client.exec_command(
                "lspci | grep -i nvidia | wc -l"
            )
            exit_status = stdout.channel.recv_exit_status()

            if exit_status == 0:
                nvidia_count_str = stdout.read().decode().strip()
                try:
                    nvidia_count = int(nvidia_count_str)
                    if nvidia_count > 0:
                        gpu_info["gpu_available"] = True
                        gpu_info["gpu_count"] = nvidia_count
                        gpu_info["detection_method"] = "lspci"
                except ValueError:
                    pass
        except Exception as e:
            gpu_info["detection_errors"].append(f"lspci detection failed: {str(e)}")

    return gpu_info


def setup_gpu_enabled_venv2(
    ssh_client,
    work_dir: str,
    requirements: Dict[str, str],
    gpu_info: Dict[str, Any],
    config: Optional[ClusterConfig] = None,
) -> Dict[str, Any]:
    """
    Setup VENV2 with GPU support based on remote cluster capabilities.

    This function ensures that VENV2 has appropriate GPU-enabled packages
    even if the local environment doesn't have GPU support.

    Args:
        ssh_client: SSH client connection
        work_dir: Remote working directory
        requirements: Package requirements from local environment
        gpu_info: GPU capabilities from detect_gpu_capabilities()
        config: Cluster configuration

    Returns:
        Dict with VENV2 setup information
    """
    if config is None:
        from .config import get_config

        config = get_config()

    venv2_info: Dict[str, Any] = {
        "gpu_packages_installed": False,
        "cuda_support_added": False,
        "pytorch_gpu_installed": False,
        "tensorflow_gpu_installed": False,
        "installation_errors": [],
    }

    # Only proceed if GPUs are available on remote cluster
    if not gpu_info.get("gpu_available", False):
        return venv2_info

    # Determine if we're using conda or venv
    conda_env2_name = f"clustrix_venv2_{work_dir.split('/')[-1]}"
    venv2_path = f"{work_dir}/venv2_execution"

    # Check if conda is available
    conda_available = False
    stdin, stdout, stderr = ssh_client.exec_command("conda --version 2>/dev/null")
    if "conda" in stdout.read().decode():
        conda_available = True

    commands = []

    # Install GPU-enabled packages based on what's in local requirements
    gpu_package_mapping = {
        "torch": {
            "conda": "pytorch torchvision torchaudio pytorch-cuda -c pytorch -c nvidia",
            "pip": "torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118",
        },
        "tensorflow": {"conda": "tensorflow-gpu", "pip": "tensorflow[and-cuda]"},
        "cupy": {
            "conda": "cupy",
            "pip": "cupy-cuda11x",  # Adjust based on CUDA version
        },
        "jax": {"conda": "jax", "pip": "jax[cuda]"},
    }

    # Check which GPU packages are in local requirements
    packages_to_install = []
    for local_pkg in requirements.keys():
        local_pkg_lower = local_pkg.lower()
        for gpu_pkg, install_info in gpu_package_mapping.items():
            if gpu_pkg in local_pkg_lower or local_pkg_lower.startswith(gpu_pkg):
                packages_to_install.append((gpu_pkg, install_info))
                break

    # Install GPU-enabled versions
    for gpu_pkg, install_info in packages_to_install:
        try:
            if conda_available:
                install_cmd = f"conda run -n {conda_env2_name} conda install {install_info['conda']} -y"
                commands.append(
                    f"{install_cmd} || echo 'Failed to install {gpu_pkg} via conda'"
                )
            else:
                commands.append(f"source {venv2_path}/bin/activate")
                install_cmd = f"pip install {install_info['pip']} --timeout=600"
                commands.append(
                    f"{install_cmd} || echo 'Failed to install {gpu_pkg} via pip'"
                )
                commands.append("deactivate")

            venv2_info["gpu_packages_installed"] = True

            if gpu_pkg == "torch":
                venv2_info["pytorch_gpu_installed"] = True
            elif gpu_pkg == "tensorflow":
                venv2_info["tensorflow_gpu_installed"] = True

        except Exception as e:
            venv2_info["installation_errors"].append(
                f"Failed to install {gpu_pkg}: {str(e)}"
            )

    # Install additional CUDA support packages if needed
    cuda_support_packages = ["numba", "cudf", "cuml", "cugraph"]  # Rapids ecosystem

    # Only install CUDA support packages if user had scientific computing packages
    has_scientific_packages = any(
        pkg in requirements for pkg in ["numpy", "scipy", "pandas", "scikit-learn"]
    )

    if has_scientific_packages and gpu_info.get("cuda_available", False):
        for cuda_pkg in cuda_support_packages:
            try:
                if conda_available:
                    # Use conda-forge for rapids packages
                    install_cmd = f"conda run -n {conda_env2_name} conda install {cuda_pkg} -c conda-forge -c rapidsai -y"
                    commands.append(
                        f"{install_cmd} || echo 'Failed to install {cuda_pkg} via conda'"
                    )
                else:
                    commands.append(f"source {venv2_path}/bin/activate")
                    install_cmd = f"pip install {cuda_pkg} --timeout=300"
                    commands.append(
                        f"{install_cmd} || echo 'Failed to install {cuda_pkg} via pip'"
                    )
                    commands.append("deactivate")

                venv2_info["cuda_support_added"] = True

            except Exception as e:
                venv2_info["installation_errors"].append(
                    f"Failed to install CUDA support package {cuda_pkg}: {str(e)}"
                )

    # Execute all GPU package installations
    if commands:
        full_command = " && ".join(commands)
        stdin, stdout, stderr = ssh_client.exec_command(full_command)
        exit_status = stdout.channel.recv_exit_status()

        if exit_status != 0:
            error_output = stderr.read().decode()
            venv2_info["installation_errors"].append(
                f"GPU package installation failed: {error_output}"
            )

    return venv2_info


def enhanced_setup_two_venv_environment(
    ssh_client,
    work_dir: str,
    requirements: Dict[str, str],
    config: Optional[ClusterConfig] = None,
) -> Dict[str, Any]:
    """
    Enhanced two-venv setup with automatic GPU detection and support.

    This function combines the original two-venv setup with GPU detection
    and GPU-enabled package installation as per user requirements.

    Args:
        ssh_client: SSH client connection
        work_dir: Remote working directory
        requirements: Package requirements from local environment
        config: Cluster configuration

    Returns:
        Dict containing both venv paths and GPU capabilities
    """
    if config is None:
        from .config import get_config

        config = get_config()

    # Step 1: Detect GPU capabilities (for VENV1 job distribution)
    print("Detecting GPU capabilities on remote cluster...")
    gpu_info = detect_gpu_capabilities(ssh_client, config)

    # Step 2: Setup basic two-venv environment
    print("Setting up two-venv environment...")
    venv_info = setup_two_venv_environment(ssh_client, work_dir, requirements, config)

    # Step 3: Enhanced VENV2 with GPU support if GPUs are available
    if gpu_info.get("gpu_available", False):
        print(
            f"GPU detected ({gpu_info['gpu_count']} devices), setting up GPU-enabled VENV2..."
        )
        gpu_venv2_info = setup_gpu_enabled_venv2(
            ssh_client, work_dir, requirements, gpu_info, config
        )
        venv_info.update(gpu_venv2_info)
    else:
        print("No GPUs detected, using standard VENV2 setup...")

    # Step 4: Add GPU detection results to venv_info
    venv_info["gpu_info"] = gpu_info

    return venv_info
