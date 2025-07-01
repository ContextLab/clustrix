"""
Dependency Analysis System for Clustrix

This module provides AST-based analysis to identify function dependencies,
including imports, local function calls, file references, and cluster filesystem operations.
"""

import ast
import inspect
import os
import types
import textwrap
from typing import List, Dict, Set, Optional, Callable, Any
from pathlib import Path


class FilesystemCall:
    """Represents a call to cluster filesystem function."""

    def __init__(
        self, function: str, args: List[str], lineno: int, context: Optional[str] = None
    ):
        self.function = function
        self.args = args
        self.lineno = lineno
        self.context = context

    def __repr__(self):
        return f"FilesystemCall(function='{self.function}', args={self.args}, lineno={self.lineno})"


class ImportInfo:
    """Information about an import statement."""

    def __init__(
        self,
        module: str,
        names: List[str],
        alias: Optional[str] = None,
        is_from_import: bool = False,
        lineno: int = 0,
    ):
        self.module = module
        self.names = names
        self.alias = alias
        self.is_from_import = is_from_import
        self.lineno = lineno

    def __repr__(self):
        return f"ImportInfo(module='{self.module}', names={self.names}, is_from_import={self.is_from_import})"


class LocalFunctionCall:
    """Information about a call to a locally-defined function."""

    def __init__(
        self,
        function_name: str,
        lineno: int,
        defined_in_scope: bool = False,
        source_file: Optional[str] = None,
    ):
        self.function_name = function_name
        self.lineno = lineno
        self.defined_in_scope = defined_in_scope
        self.source_file = source_file

    def __repr__(self):
        return f"LocalFunctionCall(function_name='{self.function_name}', lineno={self.lineno})"


class FileReference:
    """Reference to a file in the code."""

    def __init__(
        self, path: str, operation: str, lineno: int, is_relative: bool = True
    ):
        self.path = path
        self.operation = operation  # 'read', 'write', 'open', etc.
        self.lineno = lineno
        self.is_relative = is_relative

    def __repr__(self):
        return f"FileReference(path='{self.path}', operation='{self.operation}', lineno={self.lineno})"


class DependencyGraph:
    """Complete dependency graph for a function."""

    def __init__(self, function_name: str, source_code: str):
        self.function_name = function_name
        self.source_code = source_code
        self.imports: List[ImportInfo] = []
        self.local_function_calls: List[LocalFunctionCall] = []
        self.file_references: List[FileReference] = []
        self.filesystem_calls: List[FilesystemCall] = []
        self.source_files: Set[str] = set()
        self.local_modules: Set[str] = set()
        self.data_files: Set[str] = set()
        self.requires_cluster_filesystem: bool = False

    def add_imports(self, imports: List[ImportInfo]):
        """Add import dependencies."""
        self.imports.extend(imports)

    def add_local_function_calls(self, calls: List[LocalFunctionCall]):
        """Add local function call dependencies."""
        self.local_function_calls.extend(calls)

    def add_file_references(self, refs: List[FileReference]):
        """Add file reference dependencies."""
        self.file_references.extend(refs)
        for ref in refs:
            if ref.is_relative:
                self.data_files.add(ref.path)

    def add_filesystem_calls(self, calls: List[FilesystemCall]):
        """Add cluster filesystem call dependencies."""
        self.filesystem_calls.extend(calls)
        if calls:
            self.requires_cluster_filesystem = True


class DependencyAnalyzer:
    """Analyzes Python functions to identify all dependencies."""

    def __init__(self):
        self.cluster_fs_functions = {
            "cluster_ls",
            "cluster_find",
            "cluster_stat",
            "cluster_exists",
            "cluster_isdir",
            "cluster_isfile",
            "cluster_glob",
            "cluster_du",
            "cluster_count_files",
        }

        self.file_operations = {"open", "read", "write", "load", "dump", "save"}

    def analyze_function(self, func: Callable) -> DependencyGraph:
        """
        Analyze a function for all dependencies.

        Args:
            func: The function to analyze

        Returns:
            DependencyGraph containing all identified dependencies
        """
        try:
            # Get function source code
            source = inspect.getsource(func)
            func_name = func.__name__
        except (OSError, TypeError) as e:
            raise ValueError(f"Cannot get source for function {func.__name__}: {e}")

        # Parse the source into AST
        try:
            # Remove common leading whitespace to handle indented functions
            dedented_source = textwrap.dedent(source)
            tree = ast.parse(dedented_source)
        except SyntaxError as e:
            raise ValueError(f"Invalid syntax in function {func_name}: {e}")

        # Initialize dependency graph
        dependencies = DependencyGraph(
            function_name=func_name, source_code=dedented_source
        )

        # Analyze different types of dependencies
        self._analyze_imports(tree, dependencies)
        self._analyze_function_calls(tree, dependencies, func)
        self._analyze_file_references(tree, dependencies)
        self._analyze_filesystem_calls(tree, dependencies)

        # Identify source files and modules
        self._identify_source_dependencies(func, dependencies)

        return dependencies

    def _analyze_imports(self, tree: ast.AST, dependencies: DependencyGraph):
        """Analyze import statements in the function."""
        imports = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(
                        ImportInfo(
                            module=alias.name,
                            names=[alias.name],
                            alias=alias.asname,
                            is_from_import=False,
                            lineno=node.lineno,
                        )
                    )

            elif isinstance(node, ast.ImportFrom):
                if node.module:  # Skip relative imports without module
                    imports.append(
                        ImportInfo(
                            module=node.module,
                            names=[alias.name for alias in node.names],
                            is_from_import=True,
                            lineno=node.lineno,
                        )
                    )

        dependencies.add_imports(imports)

    def _analyze_function_calls(
        self, tree: ast.AST, dependencies: DependencyGraph, func: Callable
    ):
        """Analyze function calls to identify local dependencies."""
        local_calls = []

        # Get the function's global namespace
        func_globals = getattr(func, "__globals__", {})

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id

                    # Check if this is a locally-defined function
                    if func_name in func_globals:
                        obj = func_globals[func_name]
                        if isinstance(obj, types.FunctionType):
                            # This is a local function
                            source_file = None
                            try:
                                source_file = inspect.getfile(obj)
                            except (OSError, TypeError):
                                pass

                            local_calls.append(
                                LocalFunctionCall(
                                    function_name=func_name,
                                    lineno=node.lineno,
                                    defined_in_scope=True,
                                    source_file=source_file,
                                )
                            )

        dependencies.add_local_function_calls(local_calls)

    def _analyze_file_references(self, tree: ast.AST, dependencies: DependencyGraph):
        """Analyze file operations and path references."""
        file_refs = []

        for node in ast.walk(tree):
            # Look for function calls that operate on files
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id

                    if func_name in self.file_operations:
                        # Extract file path if it's a string literal
                        if node.args and isinstance(node.args[0], ast.Constant):
                            if isinstance(node.args[0].value, str):
                                path = node.args[0].value
                                file_refs.append(
                                    FileReference(
                                        path=path,
                                        operation=func_name,
                                        lineno=node.lineno,
                                        is_relative=not os.path.isabs(path),
                                    )
                                )

                # Look for method calls on file-like objects
                elif isinstance(node.func, ast.Attribute):
                    method_name = node.func.attr
                    if method_name in {"read", "write", "readline", "writelines"}:
                        # This is a file operation, but we can't easily get the path
                        # from method calls, so we'll note it exists
                        file_refs.append(
                            FileReference(
                                path="<unknown>",
                                operation=method_name,
                                lineno=node.lineno,
                                is_relative=True,
                            )
                        )

            # Look for string literals that look like file paths
            elif isinstance(node, ast.Constant):
                if isinstance(node.value, str):
                    value = node.value
                    # Simple heuristic: contains path separators and common extensions
                    if ("/" in value or "\\" in value) and ("." in value):
                        # Check for common file extensions
                        extensions = {
                            ".txt",
                            ".csv",
                            ".json",
                            ".xml",
                            ".yaml",
                            ".yml",
                            ".h5",
                            ".hdf5",
                            ".pickle",
                            ".pkl",
                            ".npy",
                            ".npz",
                            ".dat",
                            ".log",
                            ".conf",
                            ".cfg",
                            ".ini",
                        }
                        if any(value.lower().endswith(ext) for ext in extensions):
                            file_refs.append(
                                FileReference(
                                    path=value,
                                    operation="reference",
                                    lineno=node.lineno,
                                    is_relative=not os.path.isabs(value),
                                )
                            )

        dependencies.add_file_references(file_refs)

    def _analyze_filesystem_calls(self, tree: ast.AST, dependencies: DependencyGraph):
        """Analyze calls to cluster filesystem functions."""
        fs_calls = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id

                    if func_name in self.cluster_fs_functions:
                        # Extract arguments as string representations
                        args = []
                        for arg in node.args:
                            try:
                                if isinstance(arg, ast.Constant):
                                    args.append(repr(arg.value))
                                else:
                                    # Use ast.unparse if available (Python 3.9+), otherwise use a fallback
                                    if hasattr(ast, "unparse"):
                                        args.append(ast.unparse(arg))
                                    else:
                                        args.append(str(arg))
                            except Exception:
                                args.append("<unparseable>")

                        fs_calls.append(
                            FilesystemCall(
                                function=func_name, args=args, lineno=node.lineno
                            )
                        )

        dependencies.add_filesystem_calls(fs_calls)

    def _identify_source_dependencies(
        self, func: Callable, dependencies: DependencyGraph
    ):
        """Identify source files and modules that need to be packaged."""

        # Add the function's own source file
        try:
            source_file = inspect.getfile(func)
            if source_file and source_file != "<stdin>":
                dependencies.source_files.add(source_file)
        except (OSError, TypeError):
            pass

        # Add source files for local function calls
        for call in dependencies.local_function_calls:
            if call.source_file:
                dependencies.source_files.add(call.source_file)

        # Identify local modules based on imports
        for import_info in dependencies.imports:
            module_name = import_info.module

            # Check if this is a local module (not in standard library or site-packages)
            try:
                module = __import__(module_name)
                module_file = getattr(module, "__file__", None)

                if module_file:
                    module_path = Path(module_file)

                    # Check if it's in the current working directory or subdirectories
                    cwd = Path.cwd()
                    try:
                        module_path.relative_to(cwd)
                        # It's a local module
                        dependencies.local_modules.add(str(module_path))
                    except ValueError:
                        # Not a local module
                        pass
            except ImportError:
                # Module not found - might be a local module that's not in path
                pass


class LoopAnalyzer:
    """Analyzes loops in functions to identify parallelization opportunities."""

    def __init__(self):
        self.parallelizable_patterns = {
            "for_loop_with_list_comprehension",
            "for_loop_with_independent_iterations",
            "map_like_operations",
        }

    def analyze_loops(self, tree: ast.AST) -> List[Dict[str, Any]]:
        """
        Analyze loops in the AST to identify parallelization opportunities.

        Returns:
            List of loop analysis results
        """
        loops = []

        for node in ast.walk(tree):
            if isinstance(node, ast.For):
                loop_info = self._analyze_for_loop(node)
                loops.append(loop_info)
            elif isinstance(node, ast.While):
                loop_info = self._analyze_while_loop(node)
                loops.append(loop_info)

        return loops

    def _analyze_for_loop(self, node: ast.For) -> Dict[str, Any]:
        """Analyze a for loop for parallelization potential."""
        # Use ast.unparse if available (Python 3.9+), otherwise use a simple fallback
        if hasattr(ast, "unparse"):
            target_str = ast.unparse(node.target)
            iter_str = ast.unparse(node.iter)
        else:
            target_str = getattr(node.target, "id", str(node.target))
            iter_str = str(node.iter)

        return {
            "type": "for",
            "lineno": node.lineno,
            "target": target_str,
            "iter": iter_str,
            "is_parallelizable": self._is_loop_parallelizable(node),
            "dependencies": self._find_loop_dependencies(node),
        }

    def _analyze_while_loop(self, node: ast.While) -> Dict[str, Any]:
        """Analyze a while loop for parallelization potential."""
        # Use ast.unparse if available (Python 3.9+), otherwise use a simple fallback
        if hasattr(ast, "unparse"):
            test_str = ast.unparse(node.test)
        else:
            test_str = str(node.test)

        return {
            "type": "while",
            "lineno": node.lineno,
            "test": test_str,
            "is_parallelizable": False,  # While loops are generally not parallelizable
            "dependencies": [],
        }

    def _is_loop_parallelizable(self, node: ast.For) -> bool:
        """
        Determine if a for loop can be parallelized.

        A loop is potentially parallelizable if:
        1. Each iteration is independent
        2. No shared mutable state
        3. No break/continue statements that depend on previous iterations
        """
        # Simple heuristic: check for common non-parallelizable patterns
        for child in ast.walk(node):
            # Break/continue make parallelization complex
            if isinstance(child, (ast.Break, ast.Continue)):
                return False

            # Global variable modifications can create dependencies
            if isinstance(child, ast.Global):
                return False

        # More sophisticated analysis would be needed for production use
        return True

    def _find_loop_dependencies(self, node: ast.For) -> List[str]:
        """Find variables that the loop depends on."""
        dependencies = []

        for child in ast.walk(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                dependencies.append(child.id)

        return list(set(dependencies))  # Remove duplicates


def analyze_function_dependencies(func: Callable) -> DependencyGraph:
    """
    Convenience function to analyze a function's dependencies.

    Args:
        func: The function to analyze

    Returns:
        DependencyGraph containing all identified dependencies
    """
    analyzer = DependencyAnalyzer()
    return analyzer.analyze_function(func)


def analyze_function_loops(func: Callable) -> List[Dict[str, Any]]:
    """
    Convenience function to analyze loops in a function.

    Args:
        func: The function to analyze

    Returns:
        List of loop analysis results
    """
    try:
        source = inspect.getsource(func)
        # Remove common leading whitespace to handle indented functions
        dedented_source = textwrap.dedent(source)
        tree = ast.parse(dedented_source)
        analyzer = LoopAnalyzer()
        return analyzer.analyze_loops(tree)
    except (OSError, SyntaxError) as e:
        raise ValueError(f"Cannot analyze function {func.__name__}: {e}")
