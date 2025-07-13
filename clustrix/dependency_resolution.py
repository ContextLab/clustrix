"""
Advanced function dependency resolution for ClustriX.

This module provides comprehensive dependency analysis and resolution
for functions, including nested functions, cross-file dependencies,
and local vs external function detection.
"""

import ast
import inspect
import os
from typing import Any, Dict, List, Optional, Callable, Set, Tuple
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class FunctionNode:
    """Represents a function in the dependency graph."""

    name: str
    source_code: str
    module_path: str
    is_nested: bool
    is_local: bool
    dependencies: List[str]
    closure_vars: List[str]  # Variables captured from outer scope
    ast_node: Optional[ast.FunctionDef] = None


@dataclass
class DependencyInfo:
    """Complete dependency information for a function."""

    main_function: FunctionNode
    dependencies: List[FunctionNode]  # All required functions
    modules_to_import: List[str]  # External modules needed
    global_variables: Dict[str, Any]  # Global vars to preserve
    circular_dependencies: List[Tuple[str, str]]  # Detected cycles


class FunctionCallVisitor(ast.NodeVisitor):
    """AST visitor to find all function calls and imports."""

    def __init__(self):
        self.function_calls = set()
        self.attribute_calls = set()
        self.imports = {}
        self.from_imports = {}

    def visit_Call(self, node):
        """Visit function calls."""
        if isinstance(node.func, ast.Name):
            # Direct function call: func()
            self.function_calls.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            # Attribute call: module.func() or obj.method()
            full_name = self._extract_full_name(node.func)
            if full_name:
                self.attribute_calls.add(full_name)

        self.generic_visit(node)

    def visit_Import(self, node):
        """Visit import statements."""
        for alias in node.names:
            self.imports[alias.asname or alias.name] = alias.name

    def visit_ImportFrom(self, node):
        """Visit from import statements."""
        module = node.module or ""
        for alias in node.names:
            local_name = alias.asname or alias.name
            full_name = f"{module}.{alias.name}" if module else alias.name
            self.from_imports[local_name] = full_name

    def _extract_full_name(self, node: ast.Attribute) -> Optional[str]:
        """Extract full dotted name from attribute access."""
        parts = []
        current = node

        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value  # type: ignore

        if isinstance(current, ast.Name):
            parts.append(current.id)
            return ".".join(reversed(parts))

        return None


class FunctionDependencyAnalyzer:
    """Analyzes function dependencies across the entire codebase."""

    def __init__(
        self, root_dir: Optional[str] = None, package_dirs: Optional[List[str]] = None
    ):
        self.root_dir = root_dir or os.getcwd()
        self.package_dirs = package_dirs or []
        self.external_packages = self._get_known_external_packages()
        self.local_modules: Dict[str, Dict[str, Any]] = (
            {}
        )  # Cache of parsed local modules
        self.dependency_graph: Dict[str, FunctionNode] = (
            {}
        )  # Function name -> FunctionNode mapping

        # Only load local modules if root_dir is specified
        if root_dir is not None:
            self._load_local_modules()

    def _get_known_external_packages(self) -> Set[str]:
        """Get set of known external packages."""
        # Common external packages that should not be flattened
        known_external = {
            "torch",
            "numpy",
            "pandas",
            "matplotlib",
            "sklearn",
            "scipy",
            "requests",
            "flask",
            "django",
            "tensorflow",
            "keras",
            "PIL",
            "cv2",
            "boto3",
            "paramiko",
            "subprocess",
            "os",
            "sys",
            "json",
            "pickle",
            "dill",
            "cloudpickle",
            "time",
            "datetime",
            "logging",
            "argparse",
            "collections",
            "itertools",
            "functools",
            "multiprocessing",
            "threading",
            "concurrent",
            "asyncio",
        }

        # Add any packages found in site-packages
        try:
            import site

            for site_dir in site.getsitepackages():
                if os.path.exists(site_dir):
                    for item in os.listdir(site_dir):
                        if os.path.isdir(
                            os.path.join(site_dir, item)
                        ) and not item.startswith("."):
                            # Remove version info (e.g., 'numpy-1.21.0.dist-info' -> 'numpy')
                            clean_name = item.split("-")[0].replace("_", "").lower()
                            known_external.add(clean_name)
        except Exception:
            pass  # If we can't determine site packages, use defaults

        return known_external

    def _load_local_modules(self):
        """Find and parse all local Python modules."""
        logger.info(f"Loading local modules from {self.root_dir}")

        # Find all Python files in the project
        python_files = []
        for root, dirs, files in os.walk(self.root_dir):
            # Skip common non-source directories
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".")
                and d
                not in [
                    "__pycache__",
                    "build",
                    "dist",
                    "egg-info",
                    ".git",
                    ".venv",
                    "venv",
                ]
            ]

            for file in files:
                if file.endswith(".py") and not file.startswith("."):
                    file_path = os.path.join(root, file)
                    python_files.append(file_path)

        # Parse each module
        for file_path in python_files:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    source = f.read()

                module_ast = ast.parse(source, filename=file_path)
                rel_path = os.path.relpath(file_path, self.root_dir)
                self.local_modules[rel_path] = {
                    "ast": module_ast,
                    "source": source,
                    "path": file_path,
                }

                # Extract function definitions
                self._extract_functions_from_module(module_ast, rel_path)

            except (SyntaxError, UnicodeDecodeError) as e:
                logger.warning(f"Could not parse {file_path}: {e}")
                continue

    def _extract_functions_from_module(self, module_ast: ast.Module, module_path: str):
        """Extract all function definitions from a module."""
        for node in ast.walk(module_ast):
            if isinstance(node, ast.FunctionDef):
                func_node = FunctionNode(
                    name=node.name,
                    source_code=ast.unparse(node) if hasattr(ast, "unparse") else "",
                    module_path=module_path,
                    is_nested=self._is_nested_function(node, module_ast),
                    is_local=True,
                    dependencies=[],
                    closure_vars=[],
                    ast_node=node,
                )

                # Use fully qualified name for functions
                qualified_name = f"{module_path}::{node.name}"
                self.dependency_graph[qualified_name] = func_node

    def _is_nested_function(
        self, func_node: ast.FunctionDef, module_ast: ast.Module
    ) -> bool:
        """Check if a function is nested inside another function."""
        for node in ast.walk(module_ast):
            if isinstance(node, ast.FunctionDef) and node != func_node:
                # Check if func_node is in the body of this function
                for stmt in ast.walk(node):
                    if stmt is func_node:
                        return True
        return False

    def is_external_function(self, func: Callable) -> bool:
        """Determine if function is from external package."""
        try:
            func_file = inspect.getfile(func)

            # Check if in site-packages or other external locations
            external_indicators = [
                "site-packages",
                "dist-packages",
                "/usr/lib/python",
                "/System/Library",
                "conda/envs",
                "conda/lib",
            ]

            # Check if file path indicates external package
            for indicator in external_indicators:
                if indicator in func_file:
                    return True

            # Check if function module is in known external packages
            func_module = getattr(func, "__module__", "")
            if func_module:
                module_parts = func_module.split(".")
                for part in module_parts:
                    if part.lower() in self.external_packages:
                        return True

            # Check if file is outside our project directory
            try:
                rel_path = os.path.relpath(func_file, self.root_dir)
                if rel_path.startswith(".."):
                    return True  # Outside project directory
            except ValueError:
                return True  # Different drive on Windows

            return False

        except (TypeError, OSError):
            # Built-in functions, C extensions, etc.
            return True

    def analyze_function_dependencies(self, func: Callable) -> DependencyInfo:
        """Analyze all dependencies of a function."""
        try:
            # Get function source and parse
            source = inspect.getsource(func)
            # Remove common indentation to avoid parsing issues
            import textwrap

            source = textwrap.dedent(source)
            func_ast = ast.parse(source)

            # Find the main function definition
            main_func_def = None
            for node in ast.walk(func_ast):
                if isinstance(node, ast.FunctionDef):
                    main_func_def = node
                    break

            if not main_func_def:
                raise ValueError("Could not find function definition in source")

            # Analyze function calls
            visitor = FunctionCallVisitor()
            visitor.visit(func_ast)

            # Create main function node
            main_function = FunctionNode(
                name=func.__name__,
                source_code=source,
                module_path=getattr(func, "__module__", ""),
                is_nested=False,
                is_local=not self.is_external_function(func),
                dependencies=list(visitor.function_calls | visitor.attribute_calls),
                closure_vars=self._extract_closure_vars(func),
                ast_node=main_func_def,
            )

            # Resolve dependencies
            dependencies = []
            modules_to_import = []

            for dep_name in main_function.dependencies:
                dep_info = self._resolve_dependency(
                    dep_name, visitor.imports, visitor.from_imports
                )

                if dep_info["is_local"]:
                    # Add to local dependencies
                    if dep_info["function_node"]:
                        dependencies.append(dep_info["function_node"])
                else:
                    # Add to external imports
                    if dep_info["module"]:
                        modules_to_import.append(dep_info["module"])

            # Check for circular dependencies
            circular_deps = self._detect_circular_dependencies(
                main_function, dependencies
            )

            return DependencyInfo(
                main_function=main_function,
                dependencies=dependencies,
                modules_to_import=modules_to_import,
                global_variables={},  # TODO: Extract global variables
                circular_dependencies=circular_deps,
            )

        except Exception as e:
            logger.error(f"Dependency analysis failed for {func.__name__}: {e}")
            raise

    def _extract_closure_vars(self, func: Callable) -> List[str]:
        """Extract closure variables from function."""
        closure_vars = []

        if hasattr(func, "__closure__") and func.__closure__:
            # Get variable names from closure
            if hasattr(func, "__code__") and hasattr(func.__code__, "co_freevars"):
                closure_vars = list(func.__code__.co_freevars)

        return closure_vars

    def _resolve_dependency(
        self, dep_name: str, imports: Dict[str, str], from_imports: Dict[str, str]
    ) -> Dict[str, Any]:
        """Resolve a single dependency to determine if it's local or external."""

        # Check if it's a direct import alias
        if dep_name in imports:
            module_name = imports[dep_name]
            return {"is_local": False, "module": module_name, "function_node": None}

        # Check if it's a from import
        if dep_name in from_imports:
            full_name = from_imports[dep_name]
            module_name = full_name.split(".")[0]

            # Check if module is external
            is_external = module_name.lower() in self.external_packages

            return {
                "is_local": not is_external,
                "module": module_name if is_external else None,
                "function_node": (
                    self._find_local_function(dep_name) if not is_external else None
                ),
            }

        # Try to find in local modules
        local_func = self._find_local_function(dep_name)
        if local_func:
            return {"is_local": True, "module": None, "function_node": local_func}

        # Assume external if not found locally
        return {
            "is_local": False,
            "module": dep_name,  # Best guess
            "function_node": None,
        }

    def _find_local_function(self, func_name: str) -> Optional[FunctionNode]:
        """Find function definition in local modules."""
        for qualified_name, func_node in self.dependency_graph.items():
            if func_node.name == func_name:
                return func_node
        return None

    def _detect_circular_dependencies(
        self, main_func: FunctionNode, dependencies: List[FunctionNode]
    ) -> List[Tuple[str, str]]:
        """Detect circular dependencies between functions."""
        circular_deps = []

        # For now, implement simple direct circular dependency detection
        # More sophisticated cycle detection could be added using graph algorithms

        for dep in dependencies:
            if main_func.name in dep.dependencies:
                circular_deps.append((main_func.name, dep.name))

        return circular_deps
