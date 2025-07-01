"""
File Packaging System for Clustrix

This module handles packaging of function dependencies into transferable archives
for remote execution, including local modules, data files, and filesystem utilities.
"""

import os
import sys
import json
import hashlib
import zipfile
import tempfile
import inspect
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Set, Optional, Any, Callable, List
from .dependency_analysis import DependencyGraph, analyze_function_dependencies
from .config import ClusterConfig


class PackageInfo:
    """Information about a created package."""

    def __init__(
        self,
        package_id: str,
        package_path: str,
        function_name: str,
        dependencies: DependencyGraph,
        size_bytes: int,
        created_at: datetime,
        config_hash: str,
        metadata: Dict[str, Any],
    ):
        self.package_id = package_id
        self.package_path = package_path
        self.function_name = function_name
        self.dependencies = dependencies
        self.size_bytes = size_bytes
        self.created_at = created_at
        self.config_hash = config_hash
        self.metadata = metadata

    def __repr__(self):
        return f"PackageInfo(package_id='{self.package_id}', function_name='{self.function_name}')"


class ExecutionContext:
    """Context information for function execution."""

    def __init__(
        self,
        working_directory: str,
        python_version: str,
        environment_variables: Dict[str, str],
        cluster_config: ClusterConfig,
        function_args: tuple,
        function_kwargs: dict,
    ):
        self.working_directory = working_directory
        self.python_version = python_version
        self.environment_variables = environment_variables
        self.cluster_config = cluster_config
        self.function_args = function_args
        self.function_kwargs = function_kwargs

    def __repr__(self):
        return f"ExecutionContext(working_directory='{self.working_directory}')"


class FilePackager:
    """Packages function dependencies for remote execution."""

    def __init__(self):
        self.temp_dir = tempfile.mkdtemp(prefix="clustrix_packaging_")

    def __del__(self):
        """Clean up temporary directory."""
        if hasattr(self, "temp_dir") and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def package_function(
        self, func: Callable, context: ExecutionContext
    ) -> PackageInfo:
        """
        Package a function with all its dependencies.

        Args:
            func: The function to package
            context: Execution context information

        Returns:
            PackageInfo with details about the created package
        """
        # Analyze function dependencies
        dependencies = analyze_function_dependencies(func)

        # Detect external dependencies
        external_deps = self._detect_external_dependencies(dependencies)

        # Generate unique package ID
        package_id = self._generate_package_id(func, dependencies, context)

        # Create package archive
        package_path = self._create_package_archive(
            func, dependencies, context, package_id, external_deps
        )

        # Get package size
        size_bytes = os.path.getsize(package_path)

        # Create package info
        package_info = PackageInfo(
            package_id=package_id,
            package_path=package_path,
            function_name=func.__name__,
            dependencies=dependencies,
            size_bytes=size_bytes,
            created_at=datetime.now(),
            config_hash=self._hash_config(context.cluster_config),
            metadata={
                "python_version": context.python_version,
                "working_directory": context.working_directory,
                "has_filesystem_ops": dependencies.requires_cluster_filesystem,
                "import_count": len(dependencies.imports),
                "local_function_count": len(dependencies.local_function_calls),
                "file_reference_count": len(dependencies.file_references),
                "external_dependencies": external_deps,
            },
        )

        return package_info

    def _generate_package_id(
        self, func: Callable, dependencies: DependencyGraph, context: ExecutionContext
    ) -> str:
        """Generate a unique package identifier."""
        # Create hash based on function source, dependencies, and context
        hash_input = [
            func.__name__,
            dependencies.source_code,
            str(sorted(dependencies.source_files)),
            str(sorted(dependencies.local_modules)),
            str(sorted(dependencies.data_files)),
            context.python_version,
            self._hash_config(context.cluster_config),
        ]

        combined = "".join(hash_input)
        hash_obj = hashlib.sha256(combined.encode("utf-8"))
        return hash_obj.hexdigest()[:16]  # Use first 16 characters

    def _hash_config(self, config: ClusterConfig) -> str:
        """Create a hash of the cluster configuration."""
        config_dict = {
            "cluster_type": config.cluster_type,
            "cluster_host": getattr(config, "cluster_host", None),
            "remote_work_dir": getattr(config, "remote_work_dir", None),
            "local_work_dir": getattr(config, "local_work_dir", None),
        }
        config_str = json.dumps(config_dict, sort_keys=True)
        return hashlib.md5(config_str.encode()).hexdigest()[:8]

    def _create_package_archive(
        self,
        func: Callable,
        dependencies: DependencyGraph,
        context: ExecutionContext,
        package_id: str,
        external_deps: List[str],
    ) -> str:
        """Create the package archive with all dependencies."""
        package_path = os.path.join(self.temp_dir, f"clustrix_package_{package_id}.zip")

        with zipfile.ZipFile(package_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Add function source and metadata
            self._add_function_metadata(
                zf, func, dependencies, context, package_id, external_deps
            )

            # Add cluster configuration
            self._add_cluster_config(zf, context.cluster_config)

            # Add source files
            self._add_source_files(zf, dependencies.source_files)

            # Add local modules
            self._add_local_modules(zf, dependencies.local_modules)

            # Add data files
            self._add_data_files(zf, dependencies.data_files, context.working_directory)

            # Add filesystem utilities if needed
            if dependencies.requires_cluster_filesystem:
                self._add_filesystem_utilities(zf)

            # Add execution script
            self._add_execution_script(zf, dependencies, context)

            # Add environment information
            self._add_environment_info(zf, context)

        return package_path

    def _add_function_metadata(
        self,
        zf: zipfile.ZipFile,
        func: Callable,
        dependencies: DependencyGraph,
        context: ExecutionContext,
        package_id: str,
        external_deps: List[str],
    ):
        """Add function metadata to the package."""
        metadata = {
            "package_id": package_id,
            "function_info": {
                "name": func.__name__,
                "source": dependencies.source_code,
                "module": getattr(func, "__module__", None),
                "qualname": getattr(func, "__qualname__", None),
            },
            "dependencies": {
                "imports": [
                    {
                        "module": imp.module,
                        "names": imp.names,
                        "alias": imp.alias,
                        "is_from_import": imp.is_from_import,
                        "lineno": imp.lineno,
                    }
                    for imp in dependencies.imports
                ],
                "local_functions": [
                    {
                        "function_name": call.function_name,
                        "lineno": call.lineno,
                        "defined_in_scope": call.defined_in_scope,
                        "source_file": call.source_file,
                    }
                    for call in dependencies.local_function_calls
                ],
                "file_references": [
                    {
                        "path": ref.path,
                        "operation": ref.operation,
                        "lineno": ref.lineno,
                        "is_relative": ref.is_relative,
                    }
                    for ref in dependencies.file_references
                ],
                "filesystem_calls": [
                    {
                        "function": call.function,
                        "args": call.args,
                        "lineno": call.lineno,
                        "context": call.context,
                    }
                    for call in dependencies.filesystem_calls
                ],
                "requires_cluster_filesystem": dependencies.requires_cluster_filesystem,
                "external_dependencies": external_deps,
            },
            "execution_info": {
                "args": context.function_args,
                "kwargs": context.function_kwargs,
                "working_directory": context.working_directory,
                "python_version": context.python_version,
            },
            "created_at": datetime.now().isoformat(),
        }

        metadata_json = json.dumps(metadata, indent=2, default=str)
        zf.writestr("metadata.json", metadata_json)

    def _add_cluster_config(self, zf: zipfile.ZipFile, config: ClusterConfig):
        """Add cluster configuration to the package."""
        # Convert config to serializable dict
        config_dict = {
            "cluster_type": config.cluster_type,
            "cluster_host": getattr(config, "cluster_host", None),
            "username": getattr(config, "username", None),
            "remote_work_dir": getattr(config, "remote_work_dir", None),
            "local_work_dir": getattr(config, "local_work_dir", None),
            "module_loads": getattr(config, "module_loads", []),
            "environment_variables": getattr(config, "environment_variables", {}),
        }

        config_json = json.dumps(config_dict, indent=2)
        zf.writestr("cluster_config.json", config_json)

    def _add_source_files(self, zf: zipfile.ZipFile, source_files: Set[str]):
        """Add source files to the package."""
        for source_file in source_files:
            if os.path.exists(source_file):
                # Use relative path within the package
                arcname = f"sources/{os.path.basename(source_file)}"
                zf.write(source_file, arcname)

    def _add_local_modules(self, zf: zipfile.ZipFile, local_modules: Set[str]):
        """Add local modules to the package."""
        for module_file in local_modules:
            if os.path.exists(module_file):
                # Preserve directory structure for modules
                module_path = Path(module_file)

                # Try to find the package root
                package_root = self._find_package_root(module_path)
                if package_root:
                    rel_path = module_path.relative_to(package_root)
                    arcname = f"modules/{rel_path}"
                else:
                    arcname = f"modules/{module_path.name}"

                zf.write(module_file, arcname)

    def _add_data_files(
        self, zf: zipfile.ZipFile, data_files: Set[str], working_dir: str
    ):
        """Add data files to the package."""
        for data_file in data_files:
            if data_file == "<unknown>":
                continue

            # Handle relative paths
            if not os.path.isabs(data_file):
                full_path = os.path.join(working_dir, data_file)
            else:
                full_path = data_file

            if os.path.exists(full_path):
                # Preserve relative path structure
                if os.path.isabs(data_file):
                    # For absolute paths, just use the filename
                    arcname = f"data/{os.path.basename(data_file)}"
                else:
                    # For relative paths, preserve the structure
                    arcname = f"data/{data_file}"

                zf.write(full_path, arcname)

    def _add_filesystem_utilities(self, zf: zipfile.ZipFile):
        """Add cluster filesystem utilities to the package."""
        try:
            # Import the filesystem module to get its source
            from . import filesystem

            # Get the filesystem module source and fix relative imports
            fs_source = inspect.getsource(filesystem)

            # Replace relative imports with inline definitions to make it standalone
            fs_source_fixed = fs_source.replace(
                "from .config import ClusterConfig",
                '''# ClusterConfig class definition (inline for standalone packaging)
class ClusterConfig:
    """Minimal ClusterConfig for packaged execution."""
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
    
    @property
    def cluster_type(self):
        return getattr(self, '_cluster_type', 'local')
    
    @cluster_type.setter
    def cluster_type(self, value):
        self._cluster_type = value''',
            )

            zf.writestr("clustrix_filesystem.py", fs_source_fixed)

            # Create a clustrix module with the filesystem functions
            clustrix_module = '''
"""
Clustrix module for remote execution with filesystem utilities.
"""

from clustrix_filesystem import ClusterFilesystem

# Global config will be set during execution
_global_config = None

def _get_global_config():
    """Get the global config, or raise an error if not set."""
    if _global_config is None:
        raise RuntimeError("Cluster config not initialized. This should be set automatically during package execution.")
    return _global_config

def _set_global_config(config):
    """Set the global config for filesystem operations."""
    global _global_config
    _global_config = config

# Filesystem convenience functions that use the global config
def cluster_ls(path=".", config=None):
    """List directory contents locally or remotely based on config."""
    config = config or _get_global_config()
    fs = ClusterFilesystem(config)
    return fs.ls(path)

def cluster_find(pattern, path=".", config=None):
    """Find files matching pattern locally or remotely based on config."""
    config = config or _get_global_config()
    fs = ClusterFilesystem(config)
    return fs.find(pattern, path)

def cluster_stat(path, config=None):
    """Get file information locally or remotely based on config."""
    config = config or _get_global_config()
    fs = ClusterFilesystem(config)
    return fs.stat(path)

def cluster_exists(path, config=None):
    """Check if file/directory exists locally or remotely based on config."""
    config = config or _get_global_config()
    fs = ClusterFilesystem(config)
    return fs.exists(path)

def cluster_isdir(path, config=None):
    """Check if path is directory locally or remotely based on config."""
    config = config or _get_global_config()
    fs = ClusterFilesystem(config)
    return fs.isdir(path)

def cluster_isfile(path, config=None):
    """Check if path is file locally or remotely based on config."""
    config = config or _get_global_config()
    fs = ClusterFilesystem(config)
    return fs.isfile(path)

def cluster_glob(pattern, path=".", config=None):
    """Pattern matching for files locally or remotely based on config."""
    config = config or _get_global_config()
    fs = ClusterFilesystem(config)
    return fs.glob(pattern, path)

def cluster_du(path, config=None):
    """Get directory usage locally or remotely based on config."""
    config = config or _get_global_config()
    fs = ClusterFilesystem(config)
    return fs.du(path)

def cluster_count_files(path, pattern="*", config=None):
    """Count files matching pattern locally or remotely based on config."""
    config = config or _get_global_config()
    fs = ClusterFilesystem(config)
    return fs.count_files(path, pattern)
'''
            zf.writestr("clustrix.py", clustrix_module)

            # Also add the convenience functions as a separate module
            convenience_functions = '''
"""
Cluster filesystem convenience functions for remote execution.
"""

from clustrix_filesystem import ClusterFilesystem

def setup_filesystem_functions(cluster_config):
    """Set up filesystem functions with the given config."""
    fs = ClusterFilesystem(cluster_config)
    
    # Create convenience functions bound to the config
    functions = {
        'cluster_ls': lambda path=".": fs.ls(path),
        'cluster_find': lambda pattern, path=".": fs.find(pattern, path),
        'cluster_stat': lambda path: fs.stat(path),
        'cluster_exists': lambda path: fs.exists(path),
        'cluster_isdir': lambda path: fs.isdir(path),
        'cluster_isfile': lambda path: fs.isfile(path),
        'cluster_glob': lambda pattern, path=".": fs.glob(pattern, path),
        'cluster_du': lambda path: fs.du(path),
        'cluster_count_files': lambda path, pattern="*": fs.count_files(path, pattern)
    }
    
    return functions
'''
            zf.writestr("filesystem_utils.py", convenience_functions)

        except Exception as e:
            # If we can't get the filesystem source, create a minimal version
            print(f"Warning: Could not package filesystem utilities: {e}")

    def _add_execution_script(
        self,
        zf: zipfile.ZipFile,
        dependencies: DependencyGraph,
        context: ExecutionContext,
    ):
        """Add the execution script that will run on the remote cluster."""
        script_content = self._generate_execution_script(dependencies, context)
        zf.writestr("execute.py", script_content)

    def _add_environment_info(self, zf: zipfile.ZipFile, context: ExecutionContext):
        """Add environment information to the package."""
        env_info = {
            "python_version": context.python_version,
            "platform": sys.platform,
            "environment_variables": context.environment_variables,
            "python_path": sys.path,
            "installed_packages": self._get_installed_packages(),
        }

        env_json = json.dumps(env_info, indent=2)
        zf.writestr("environment.json", env_json)

    def _get_installed_packages(self) -> Dict[str, str]:
        """Get information about installed packages."""
        try:
            import pkg_resources

            installed = {}
            for dist in pkg_resources.working_set:
                installed[dist.project_name] = dist.version
            return installed
        except ImportError:
            return {}

    def _detect_external_dependencies(self, dependencies: DependencyGraph) -> List[str]:
        """Detect external packages that need to be installed in remote environment."""
        external_deps = []
        stdlib_modules = self._get_stdlib_modules()

        for import_info in dependencies.imports:
            module_name = import_info.module

            # Skip standard library modules
            if module_name in stdlib_modules:
                continue

            # Skip local modules (already detected)
            if module_name in [
                os.path.basename(mod).replace(".py", "")
                for mod in dependencies.local_modules
            ]:
                continue

            # Try to determine if this is an external package
            try:
                # Try to import to see if it's available
                __import__(module_name)

                # Check if it's in site-packages (external dependency)
                module = sys.modules.get(module_name)
                if module and hasattr(module, "__file__") and module.__file__:
                    module_path = Path(module.__file__)

                    # If it contains 'site-packages' in the path, it's external
                    if "site-packages" in str(module_path):
                        # Map known module names to package names
                        package_name = self._map_module_to_package(module_name)
                        if package_name and package_name not in external_deps:
                            external_deps.append(package_name)

            except ImportError:
                # Module not available, but might be external - add it anyway
                package_name = self._map_module_to_package(module_name)
                if package_name and package_name not in external_deps:
                    external_deps.append(package_name)

        # Always add paramiko if filesystem operations are needed
        if dependencies.requires_cluster_filesystem and "paramiko" not in external_deps:
            external_deps.append("paramiko")

        return external_deps

    def _get_stdlib_modules(self) -> Set[str]:
        """Get a set of standard library module names."""
        # This is a basic set - in production, could use more comprehensive detection
        stdlib_modules = {
            "os",
            "sys",
            "json",
            "pickle",
            "traceback",
            "pathlib",
            "tempfile",
            "datetime",
            "hashlib",
            "zipfile",
            "inspect",
            "shutil",
            "glob",
            "textwrap",
            "ast",
            "types",
            "socket",
            "subprocess",
            "re",
            "time",
            "collections",
            "itertools",
            "functools",
            "operator",
            "math",
            "random",
            "string",
            "urllib",
            "http",
            "email",
            "csv",
            "configparser",
            "logging",
            "unittest",
            "io",
            "contextlib",
            "copy",
            "weakref",
        }
        return stdlib_modules

    def _map_module_to_package(self, module_name: str) -> Optional[str]:
        """Map a module name to its pip package name."""
        # Common mappings of import names to package names
        module_to_package = {
            "paramiko": "paramiko",
            "numpy": "numpy",
            "np": "numpy",
            "pandas": "pandas",
            "pd": "pandas",
            "sklearn": "scikit-learn",
            "cv2": "opencv-python",
            "PIL": "Pillow",
            "yaml": "PyYAML",
            "requests": "requests",
            "matplotlib": "matplotlib",
            "plt": "matplotlib",
            "scipy": "scipy",
            "torch": "torch",
            "tensorflow": "tensorflow",
            "tf": "tensorflow",
        }

        return module_to_package.get(module_name, module_name)

    def _find_package_root(self, module_path: Path) -> Optional[Path]:
        """Find the root directory of a Python package."""
        current = module_path.parent

        while current != current.parent:  # Not at filesystem root
            if (current / "__init__.py").exists():
                # This directory has __init__.py, keep going up
                current = current.parent
            else:
                # This is the package root
                return current.parent if current.parent != current else current

        return None

    def _generate_execution_script(
        self, dependencies: DependencyGraph, context: ExecutionContext
    ) -> str:
        """Generate the execution script for remote execution."""

        script = f'''#!/usr/bin/env python3
"""
Clustrix Remote Execution Script
Generated automatically for function: {dependencies.function_name}
"""

import sys
import os
import json
import pickle
import traceback
from pathlib import Path

def setup_environment():
    """Set up the execution environment."""
    # Add package directories to Python path
    package_dir = Path(__file__).parent
    
    # Add sources and modules to path
    sources_dir = package_dir / "sources"
    modules_dir = package_dir / "modules"
    
    if sources_dir.exists():
        sys.path.insert(0, str(sources_dir))
    if modules_dir.exists():
        sys.path.insert(0, str(modules_dir))
    
    # Change to data directory if it exists
    data_dir = package_dir / "data"
    if data_dir.exists():
        os.chdir(str(data_dir))

def install_external_dependencies():
    """Install external dependencies if needed."""
    package_dir = Path(__file__).parent
    
    # Load metadata to get external dependencies
    metadata_file = package_dir / "metadata.json"
    if not metadata_file.exists():
        return
    
    with open(metadata_file, 'r') as f:
        metadata = json.load(f)
    
    external_deps = metadata.get("dependencies", {{}}).get("external_dependencies", [])
    
    if external_deps:
        print(f"Installing external dependencies: {{', '.join(external_deps)}}")
        for dep in external_deps:
            try:
                import subprocess
                result = subprocess.run([
                    sys.executable, "-m", "pip", "install", dep
                ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
                
                if result.returncode == 0:
                    print(f"Successfully installed {{dep}}")
                else:
                    stderr_output = result.stderr.decode() if result.stderr else "Unknown error"
                    print(f"Failed to install {{dep}}: {{stderr_output}}")
            except Exception as e:
                print(f"Error installing {{dep}}: {{e}}")
    else:
        print("No external dependencies to install")

def setup_cluster_filesystem():
    """Set up cluster filesystem functions if needed."""
    package_dir = Path(__file__).parent
    
    # Load cluster config
    config_file = package_dir / "cluster_config.json"
    if not config_file.exists():
        return {{}}
    
    with open(config_file, 'r') as f:
        config_data = json.load(f)
    
    # Try to set up filesystem functions
    try:
        sys.path.insert(0, str(package_dir))
        from filesystem_utils import setup_filesystem_functions
        
        # Create a simple config object
        class Config:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)
        
        config = Config(**config_data)
        return setup_filesystem_functions(config)
    except Exception as e:
        print(f"Warning: Could not set up filesystem functions: {{e}}")
        return {{}}

def load_and_execute():
    """Load function metadata and execute."""
    package_dir = Path(__file__).parent
    
    # Load metadata
    with open(package_dir / "metadata.json", "r") as f:
        metadata = json.load(f)
    
    function_info = metadata["function_info"]
    execution_info = metadata["execution_info"]
    
    # Set up environment
    setup_environment()
    
    # Install external dependencies first
    install_external_dependencies()
    
    # Set up filesystem functions if needed
    if metadata["dependencies"]["requires_cluster_filesystem"]:
        fs_functions = setup_cluster_filesystem()
        globals().update(fs_functions)
    
    try:
        # Execute function source code
        function_source = function_info["source"]
        local_namespace = {{}}
        exec(function_source, globals(), local_namespace)
        
        # Get the function
        function_name = function_info["name"]
        if function_name in local_namespace:
            func = local_namespace[function_name]
        elif function_name in globals():
            func = globals()[function_name]
        else:
            raise ValueError(f"Function {{function_name}} not found after execution")
        
        # Get arguments and reconstruct config objects if needed
        args = execution_info["args"]
        kwargs = execution_info["kwargs"]
        
        # Load cluster config for reconstruction
        config_file = package_dir / "cluster_config.json"
        if config_file.exists():
            with open(config_file, 'r') as f:
                config_data = json.load(f)
            
            # Create a proper config object
            class ClusterConfig:
                def __init__(self, **kwargs):
                    # Set cluster_type explicitly to avoid recursion
                    self.cluster_type = kwargs.get('cluster_type', 'local')
                    # Set default values for filesystem operations
                    self.cluster_port = kwargs.get('cluster_port', 22)
                    self.key_file = kwargs.get('key_file', None)
                    self.password = kwargs.get('password', None)
                    # Set all other attributes
                    for k, v in kwargs.items():
                        setattr(self, k, v)
            
            config_obj = ClusterConfig(**config_data)
            
            # Replace any string config arguments with proper config object
            new_args = []
            for arg in args:
                if isinstance(arg, str) and "cluster_type" in str(arg):
                    # This looks like a serialized config, replace with proper object
                    new_args.append(config_obj)
                else:
                    new_args.append(arg)
            args = tuple(new_args)
            
            # Also check kwargs
            for key, value in kwargs.items():
                if isinstance(value, str) and "cluster_type" in str(value):
                    kwargs[key] = config_obj
        
        # Execute the function
        result = func(*args, **kwargs)
        
        # Save result to accessible directory (where SLURM job was submitted)
        # Use environment variable set by wrapper script
        result_working_dir = os.environ.get("CLUSTRIX_ORIGINAL_CWD", "/tmp")
        
        # Create result file in accessible location
        result_file = f"result_{{function_name}}_{{os.environ.get('SLURM_JOB_ID', 'unknown')}}.json"
        result_path = os.path.join(result_working_dir, result_file)
        
        # Save result as JSON for easy access
        result_data = {{
            "function_name": function_name,
            "status": "SUCCESS",
            "result": result,
            "metadata": {{
                "hostname": os.environ.get("HOSTNAME", "unknown"),
                "slurm_job_id": os.environ.get("SLURM_JOB_ID", "not_set"),
                "python_version": sys.version.split()[0],
                "execution_directory": str(package_dir),
                "timestamp": __import__("datetime").datetime.now().isoformat()
            }}
        }}
        
        with open(result_path, "w") as f:
            json.dump(result_data, f, indent=2, default=str)
        
        print(f"Function {{function_name}} executed successfully")
        print(f"Result saved to: {{result_path}}")
        return result
        
    except Exception as e:
        # Save error information to accessible location
        result_working_dir = os.environ.get("CLUSTRIX_ORIGINAL_CWD", "/tmp")
        function_name = metadata.get("function_info", {{}}).get("name", "unknown")
        
        error_file = f"error_{{function_name}}_{{os.environ.get('SLURM_JOB_ID', 'unknown')}}.json"
        error_path = os.path.join(result_working_dir, error_file)
        
        error_info = {{
            "function_name": function_name,
            "status": "ERROR",
            "error": str(e),
            "error_type": type(e).__name__,
            "traceback": traceback.format_exc(),
            "metadata": {{
                "hostname": os.environ.get("HOSTNAME", "unknown"),
                "slurm_job_id": os.environ.get("SLURM_JOB_ID", "not_set"),
                "timestamp": __import__("datetime").datetime.now().isoformat()
            }}
        }}
        
        with open(error_path, "w") as f:
            json.dump(error_info, f, indent=2)
        
        print(f"Function execution failed: {{e}}")
        print(f"Error saved to: {{error_path}}")
        raise

if __name__ == "__main__":
    try:
        result = load_and_execute()
        print("Execution completed successfully")
    except Exception as e:
        print(f"Execution failed: {{e}}")
        sys.exit(1)
'''

        return script


def create_execution_context(
    cluster_config: ClusterConfig,
    func_args: tuple = (),
    func_kwargs: Optional[dict] = None,
) -> ExecutionContext:
    """
    Create an execution context for function packaging.

    Args:
        cluster_config: The cluster configuration
        func_args: Arguments to pass to the function
        func_kwargs: Keyword arguments to pass to the function

    Returns:
        ExecutionContext for the function
    """
    if func_kwargs is None:
        func_kwargs = {}

    return ExecutionContext(
        working_directory=os.getcwd(),
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        environment_variables=dict(os.environ),
        cluster_config=cluster_config,
        function_args=func_args,
        function_kwargs=func_kwargs,
    )


def package_function_for_execution(
    func: Callable,
    cluster_config: ClusterConfig,
    func_args: tuple = (),
    func_kwargs: Optional[dict] = None,
) -> PackageInfo:
    """
    Convenience function to package a function for remote execution.

    Args:
        func: The function to package
        cluster_config: Cluster configuration
        func_args: Arguments to pass to the function
        func_kwargs: Keyword arguments to pass to the function

    Returns:
        PackageInfo with details about the created package
    """
    context = create_execution_context(cluster_config, func_args, func_kwargs)
    packager = FilePackager()

    # Create the package
    package_info = packager.package_function(func, context)

    # Move the package to a more permanent location for testing/convenience use
    persistent_dir = tempfile.mkdtemp(prefix="clustrix_packages_")
    new_package_path = os.path.join(
        persistent_dir, os.path.basename(package_info.package_path)
    )

    # Copy the package to the new location
    import shutil

    shutil.copy2(package_info.package_path, new_package_path)

    # Update the package info with the new path
    package_info.package_path = new_package_path

    return package_info
