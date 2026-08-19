"""
Automatic function flattening for complexity threshold management.

This module provides automatic refactoring of complex functions to meet
the complexity requirements for remote execution, particularly for two-venv
environments that have strict function complexity limits.

NOT USED BY THE EXECUTION PATH, AND MUST NOT BE.
--------------------------------------------------
``clustrix.decorator._execute_single`` no longer calls anything here. Do not
wire it back in. A flattener rewrites a function's source and hands back a
different callable; whether that callable still computes the caller's answer
cannot be verified without running the caller's function, so substituting it
into a job submission is a way to return a wrong answer with no error. That is
not hypothetical -- it is what this module did (see the git history for
``create_simple_subprocess_fallback``, deleted, which ran a hardcoded script
whose entire body was ``result = "Function execution completed"``).

It is also unnecessary. ``clustrix.utils.serialize_function`` pickles by value
via ``dill(recurse=True)`` / cloudpickle, which already round-trips every case
flattening was built to work around: nested functions, closures, module-level
globals, and functions with no retrievable source. This is proven end to end,
through a real subprocess worker, in
``tests/unit/test_execute_single_no_fabrication.py``.

The generators below are retained only because tests under ``tests/`` still
import them, and they are known to emit code that does not compile (the
generated body is dedented to column 0), drops ``for`` headers, drops
``return`` statements and emits ``import`` lines for local names and builtins.
``auto_flatten_if_needed`` therefore reports ``flattened: False`` for every
input tried so far. Recommendation on record: delete this module,
``clustrix/dependency_resolution.py``, and the tests that exist only to
exercise them.
"""

import ast
import inspect
import textwrap
from typing import Callable, Dict, List, Any, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


def _ast_unparse(node: ast.AST) -> str:
    """Fallback for ast.unparse if not available."""
    if hasattr(ast, "unparse"):
        return ast.unparse(node)  # type: ignore
    else:
        # For older Python versions, try using astor or just return a placeholder
        try:
            import astor  # type: ignore

            return astor.to_source(node).strip()
        except ImportError:
            # Fallback to basic representation
            return f"# {type(node).__name__} statement"


class ComplexityAnalyzer(ast.NodeVisitor):
    """AST visitor to analyze function complexity."""

    def __init__(self):
        self.complexity_score = 0
        self.line_count = 0
        self.nested_depth = 0
        self.max_nested_depth = 0
        self.function_calls = 0
        self.import_statements = 0
        self.loop_count = 0
        self.conditional_count = 0
        self.subprocess_calls = 0
        self.nested_functions = 0  # Track nested function definitions
        self.is_main_function = True  # Track if we're in the main function

    def visit_FunctionDef(self, node):
        """Visit function definitions."""
        if self.is_main_function:
            # This is the main function we're analyzing
            self.is_main_function = False
            self.line_count += len(node.body)
            self.complexity_score += 1
        else:
            # This is a nested function - MAJOR complexity factor for serialization
            self.nested_functions += 1
            self.complexity_score += 10  # Nested functions require flattening
            self.line_count += len(node.body)
            logger.info(f"Found nested function: {node.name}")

        # Track nesting depth
        old_depth = self.nested_depth
        self.nested_depth += 1
        self.max_nested_depth = max(self.max_nested_depth, self.nested_depth)

        # Visit child nodes
        self.generic_visit(node)

        # Restore state
        self.nested_depth = old_depth
        if not self.is_main_function:
            self.is_main_function = True

    def visit_Call(self, node):
        """Visit function calls."""
        self.function_calls += 1
        self.complexity_score += 1

        # Special handling for subprocess calls (complexity risk)
        if isinstance(node.func, ast.Attribute):
            if hasattr(node.func.value, "id") and node.func.value.id == "subprocess":
                self.subprocess_calls += 1
                self.complexity_score += 3  # Higher weight for subprocess
        elif isinstance(node.func, ast.Name):
            if node.func.id in ["subprocess", "exec", "eval"]:
                self.subprocess_calls += 1
                self.complexity_score += 3

        self.generic_visit(node)

    def visit_Import(self, node):
        """Visit import statements."""
        self.import_statements += len(node.names)
        self.complexity_score += len(node.names)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        """Visit from-import statements."""
        self.import_statements += len(node.names) if node.names else 1
        self.complexity_score += len(node.names) if node.names else 1
        self.generic_visit(node)

    def visit_For(self, node):
        """Visit for loops."""
        self.loop_count += 1
        self.complexity_score += 2
        self.generic_visit(node)

    def visit_While(self, node):
        """Visit while loops."""
        self.loop_count += 1
        self.complexity_score += 2
        self.generic_visit(node)

    def visit_If(self, node):
        """Visit if statements."""
        self.conditional_count += 1
        self.complexity_score += 1
        self.generic_visit(node)

    def visit_Try(self, node):
        """Visit try-except blocks."""
        self.complexity_score += 2  # Exception handling adds complexity
        self.generic_visit(node)


def analyze_function_complexity(func: Callable) -> Dict[str, Any]:
    """
    Analyze the complexity of a function.

    Args:
        func: Function to analyze

    Returns:
        Dictionary with complexity metrics. Always contains ``source_available``:

        * ``source_available: True``  -- the source was read and parsed, so
          every other metric (including ``is_complex``) is a real measurement.
        * ``source_available: False`` -- ``inspect.getsource`` could not
          recover the source (REPL, notebook cell, ``exec``-created function,
          C function). Nothing was measured, so the metrics are ``None`` and
          ``is_complex`` is ``False``.

        ``is_complex: False`` on the failure branch means "not known to be
        complex", never "measured and found simple". Callers that care about
        the difference must check ``source_available``; any caller that would
        rewrite the function based on its source has to skip it, because there
        is no source to rewrite. This branch used to return
        ``complexity_score: 999, is_complex: True``, which made every
        source-rewriting caller fire on exactly the functions it could not
        possibly handle.
    """
    try:
        source = inspect.getsource(func)
        source = textwrap.dedent(source)
        tree = ast.parse(source)

        analyzer = ComplexityAnalyzer()
        analyzer.visit(tree)

        # Calculate overall complexity assessment
        is_complex = (
            analyzer.complexity_score > 20
            or analyzer.line_count > 30
            or analyzer.max_nested_depth > 3
            or analyzer.subprocess_calls > 2
            or analyzer.function_calls > 15
            or analyzer.nested_functions > 0  # ANY nested function requires flattening
        )

        return {
            "source_available": True,
            "complexity_score": analyzer.complexity_score,
            "line_count": analyzer.line_count,
            "max_nested_depth": analyzer.max_nested_depth,
            "function_calls": analyzer.function_calls,
            "import_statements": analyzer.import_statements,
            "loop_count": analyzer.loop_count,
            "conditional_count": analyzer.conditional_count,
            "subprocess_calls": analyzer.subprocess_calls,
            "nested_functions": analyzer.nested_functions,
            "is_complex": is_complex,
            "estimated_risk": (
                "high"
                if is_complex
                else "medium" if analyzer.complexity_score > 10 else "low"
            ),
        }

    except Exception as e:
        # No source means nothing was measured. Report that, and report it as
        # "unknown", not as a 999-point complexity score that no real function
        # could reach.
        logger.warning(
            "Complexity analysis unavailable for %s: %s",
            getattr(func, "__name__", repr(func)),
            e,
        )
        return {
            "source_available": False,
            "complexity_score": None,
            "line_count": None,
            "max_nested_depth": None,
            "function_calls": None,
            "import_statements": None,
            "loop_count": None,
            "conditional_count": None,
            "subprocess_calls": None,
            "nested_functions": None,
            "is_complex": False,
            "estimated_risk": "unknown",
            "analysis_error": str(e),
        }


class FunctionFlattener:
    """Flattens complex functions into simpler components."""

    def __init__(self):
        self.extracted_functions = []
        self.main_function_body = []

    def flatten_function(
        self, func: Callable, complexity_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Flatten a complex function into simpler components.

        Args:
            func: Function to flatten
            complexity_info: Complexity analysis results

        Returns:
            Dictionary with flattened function components
        """
        try:
            source = inspect.getsource(func)
            source = textwrap.dedent(source)
            tree = ast.parse(source)

            # Extract the function definition
            func_def = None
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    func_def = node
                    break

            if not func_def:
                raise ValueError("Could not find function definition")

            # Analyze function body for flattening opportunities
            flattened = self._flatten_function_body(func_def)

            return {
                "success": True,
                "original_complexity": complexity_info,
                "flattened_components": flattened,
                "main_function": self._create_main_function(func, flattened),
                "helper_functions": self._create_helper_functions(flattened),
            }

        except Exception as e:
            logger.error(f"Function flattening failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "fallback_strategy": "use_simple_subprocess_pattern",
            }

    def _flatten_function_body(self, func_def: ast.FunctionDef) -> Dict[str, Any]:
        """Analyze and flatten function body."""
        components: Dict[str, List[Any]] = {
            "imports": [],
            "simple_operations": [],
            "complex_operations": [],
            "subprocess_calls": [],
            "loops": [],
            "conditionals": [],
            "nested_functions": [],  # Store extracted nested functions
        }

        for stmt in func_def.body:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                components["imports"].append(_ast_unparse(stmt))
            elif isinstance(stmt, ast.FunctionDef):
                # Extract nested function definition
                nested_func = self._extract_nested_function(stmt)
                components["nested_functions"].append(nested_func)
            elif isinstance(stmt, ast.For):
                components["loops"].append(self._extract_loop(stmt))
            elif isinstance(stmt, ast.If):
                components["conditionals"].append(self._extract_conditional(stmt))
            elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                call_info = self._analyze_call(stmt.value)
                if call_info.get("is_subprocess"):
                    components["subprocess_calls"].append(call_info)
                elif call_info.get("complexity", 0) > 3:
                    components["complex_operations"].append(call_info)
                else:
                    components["simple_operations"].append(call_info)
            else:
                # Default to simple operation
                try:
                    components["simple_operations"].append(
                        {
                            "type": "statement",
                            "code": _ast_unparse(stmt),
                            "complexity": 1,
                        }
                    )
                except Exception:
                    components["simple_operations"].append(
                        {
                            "type": "statement",
                            "code": "# Unparseable statement",
                            "complexity": 1,
                        }
                    )

        return components

    def _extract_nested_function(self, func_node: ast.FunctionDef) -> Dict[str, Any]:
        """Extract nested function definition for hoisting."""
        try:
            return {
                "name": func_node.name,
                "args": [arg.arg for arg in func_node.args.args],
                "body": [_ast_unparse(stmt) for stmt in func_node.body],
                "source": _ast_unparse(func_node),
                "docstring": (
                    func_node.body[0].value.s
                    if (
                        func_node.body
                        and isinstance(func_node.body[0], ast.Expr)
                        and isinstance(func_node.body[0].value, ast.Str)
                    )
                    else None
                ),
                "type": "nested_function",
            }
        except Exception as e:
            logger.warning(f"Failed to extract nested function {func_node.name}: {e}")
            return {
                "name": func_node.name,
                "type": "nested_function",
                "extraction_error": str(e),
                "source": f"# Failed to extract {func_node.name}",
            }

    def _extract_loop(self, loop_node: ast.For) -> Dict[str, Any]:
        """Extract loop information for flattening."""
        try:
            return {
                "type": "for_loop",
                "target": _ast_unparse(loop_node.target),
                "iter": _ast_unparse(loop_node.iter),
                "body": [_ast_unparse(stmt) for stmt in loop_node.body],
                "complexity": len(loop_node.body) * 2,
                "parallelizable": self._is_loop_parallelizable(loop_node),
            }
        except Exception:
            return {
                "type": "for_loop",
                "complexity": 5,
                "parallelizable": False,
                "extraction_error": True,
            }

    def _extract_conditional(self, if_node: ast.If) -> Dict[str, Any]:
        """Extract conditional information."""
        try:
            return {
                "type": "conditional",
                "test": _ast_unparse(if_node.test),
                "body": [_ast_unparse(stmt) for stmt in if_node.body],
                "orelse": (
                    [_ast_unparse(stmt) for stmt in if_node.orelse]
                    if if_node.orelse
                    else []
                ),
                "complexity": len(if_node.body) + len(if_node.orelse or []),
            }
        except Exception:
            return {"type": "conditional", "complexity": 3, "extraction_error": True}

    def _analyze_call(self, call_node: ast.Call) -> Dict[str, Any]:
        """Analyze function call complexity."""
        try:
            call_str = _ast_unparse(call_node)

            is_subprocess = (
                "subprocess" in call_str or ".run(" in call_str or ".Popen(" in call_str
            )

            complexity = 1
            if is_subprocess:
                complexity = 5
            elif len(call_str) > 100:
                complexity = 3
            elif "torch" in call_str or "cuda" in call_str:
                complexity = 2

            return {
                "type": "function_call",
                "code": call_str,
                "is_subprocess": is_subprocess,
                "complexity": complexity,
                "length": len(call_str),
            }
        except Exception:
            return {"type": "function_call", "complexity": 2, "extraction_error": True}

    def _is_loop_parallelizable(self, loop_node: ast.For) -> bool:
        """Check if a loop can be parallelized."""
        # Simple heuristic: loops with independent iterations
        # More sophisticated analysis could be added here
        try:
            # Check for dependencies between iterations
            for stmt in loop_node.body:
                if isinstance(stmt, ast.Assign):
                    # Look for accumulator patterns
                    if isinstance(stmt.targets[0], ast.Name):
                        target_name = stmt.targets[0].id
                        # Check if target is used in value expression
                        for node in ast.walk(stmt.value):
                            if isinstance(node, ast.Name) and node.id == target_name:
                                return False  # Dependency found
            return True
        except Exception:
            return False

    def _create_main_function(
        self, original_func: Callable, components: Dict[str, Any]
    ) -> str:
        """Create simplified main function with correct signature."""
        func_name = original_func.__name__

        # Get original function signature
        import inspect

        sig = inspect.signature(original_func)
        params = list(sig.parameters.values())

        # Build parameter string for function definition
        param_strs = []
        for param in params:
            if param.default is param.empty:
                param_strs.append(param.name)
            else:
                # Handle default values
                default_repr = repr(param.default)
                param_strs.append(f"{param.name}={default_repr}")

        param_string = ", ".join(param_strs)

        # Build parameter names for passing to subprocess
        [param.name for param in params]

        # Build simplified main function with correct signature
        main_code = f"""
def {func_name}_flattened({param_string}):
    \"\"\"Flattened version of {func_name} for remote execution.\"\"\"
    {self._build_flattened_computation(components)}
"""
        return main_code

    def _build_flattened_computation(self, components: Dict[str, Any]) -> str:
        """Build the flattened computation code."""
        code_parts = []

        # Add imports
        for imp in components.get("imports", []):
            code_parts.append(imp)

        code_parts.append("")  # Blank line

        # Add nested function definitions
        for nested in components.get("nested_functions", []):
            if nested.get("source"):
                code_parts.append(nested["source"])
                code_parts.append("")

        # Add simple operations
        for op in components.get("simple_operations", []):
            if isinstance(op, dict) and "code" in op:
                code = op["code"]
                # Convert return statements to result assignments
                if code.strip().startswith("return "):
                    result_expr = code.strip()[7:]  # Remove "return "
                    code_parts.append(f"result = {result_expr}")
                else:
                    code_parts.append(code)

        # Handle loops (simplified or parallelized)
        for loop in components.get("loops", []):
            if loop.get("parallelizable", False):
                code_parts.append(
                    f"# Parallelizable loop: {loop.get('target', 'unknown')}"
                )
                code_parts.extend(loop.get("body", []))
            else:
                code_parts.append(f"# Sequential loop: {loop.get('target', 'unknown')}")
                code_parts.extend(loop.get("body", []))

        # Add result output
        code_parts.append("")
        code_parts.append("# Output result")
        code_parts.append("import json")
        code_parts.append(
            'print(f\'RESULT:{json.dumps(locals().get("result", "no_result"))}\')'
        )

        # "\\n" here would be a literal backslash followed by n, joining
        # every statement onto one line and making the result fail to
        # compile with "unexpected character after line continuation
        # character" -- which is precisely what every flattening attempt
        # reported before this was a newline.
        return "\n".join(code_parts)

    def _create_helper_functions(self, components: Dict[str, Any]) -> List[str]:
        """Create helper functions for complex operations."""
        helpers = []

        # Create helpers for complex operations
        for i, op in enumerate(components.get("complex_operations", [])):
            helper_code = f"""
def helper_operation_{i}():
    \"\"\"Helper function for complex operation {i}.\"\"\"
    {op.get('code', '# No code available')}
    return result
"""
            helpers.append(helper_code)

        return helpers


class AdvancedFunctionFlattener:
    """Advanced function flattening with full dependency resolution."""

    def __init__(self, root_dir: Optional[str] = None):
        from .dependency_resolution import FunctionDependencyAnalyzer

        self.dependency_analyzer = FunctionDependencyAnalyzer(root_dir)
        self.hoisted_functions: Dict[str, str] = {}  # Name -> source code mapping

    def flatten_with_dependencies(self, func: Callable) -> Dict[str, Any]:
        """
        Flatten function and all its local dependencies.

        Returns:
            Dictionary with flattened function components and metadata
        """
        try:
            # 1. Analyze dependencies
            dep_info = self.dependency_analyzer.analyze_function_dependencies(func)

            logger.info(
                f"Analyzing {func.__name__}: {len(dep_info.dependencies)} local dependencies found"
            )

            # 2. Handle circular dependencies
            if dep_info.circular_dependencies:
                logger.warning(
                    f"Circular dependencies detected: {dep_info.circular_dependencies}"
                )
                return self._handle_circular_dependencies(dep_info)

            # 3. Hoist nested functions from main function
            hoisted_from_main = self._hoist_nested_functions(dep_info.main_function)

            # 4. Process all local dependencies
            all_dependencies = dep_info.dependencies.copy()
            for hoisted in hoisted_from_main:
                all_dependencies.append(hoisted)

            # 5. Topologically sort dependencies
            sorted_deps = self._topological_sort(all_dependencies)

            # 6. Generate flattened code
            flattened_code = self._generate_flattened_code_advanced(
                dep_info.main_function, sorted_deps, dep_info.modules_to_import
            )

            return {
                "success": True,
                "flattened_function": flattened_code,
                "dependencies_count": len(all_dependencies),
                "external_modules": dep_info.modules_to_import,
                "hoisted_functions": len(hoisted_from_main),
                "dependency_info": dep_info,
            }

        except Exception as e:
            logger.error(f"Advanced flattening failed for {func.__name__}: {e}")
            return {
                "success": False,
                "error": str(e),
                "fallback_strategy": "use_basic_flattening",
            }

    def _hoist_nested_functions(self, func_node) -> List:
        """Extract and hoist nested functions to module level."""
        hoisted: List[Any] = []

        if not func_node.ast_node:
            return hoisted

        # Find nested function definitions
        nested_functions = []
        for node in ast.walk(func_node.ast_node):
            if isinstance(node, ast.FunctionDef) and node != func_node.ast_node:
                # This is a nested function
                nested_functions.append(node)

        # Process each nested function
        for nested_func in nested_functions:
            try:
                hoisted_func = self._hoist_single_function(nested_func, func_node)
                if hoisted_func:
                    hoisted.append(hoisted_func)
                    logger.info(f"Hoisted nested function: {nested_func.name}")
            except Exception as e:
                logger.warning(
                    f"Failed to hoist nested function {nested_func.name}: {e}"
                )

        return hoisted

    def _hoist_single_function(
        self, nested_func: ast.FunctionDef, parent_func
    ) -> Optional[Any]:
        """Hoist a single nested function, resolving closure dependencies."""
        from .dependency_resolution import FunctionNode

        # Generate unique name for hoisted function
        hoisted_name = f"{parent_func.name}_{nested_func.name}_hoisted"

        # Analyze closure variables
        closure_vars = self._analyze_closure_variables(
            nested_func, parent_func.ast_node
        )

        # Create new function with closure variables as parameters
        hoisted_func_code = self._create_hoisted_function(
            nested_func, hoisted_name, closure_vars
        )

        return FunctionNode(
            name=hoisted_name,
            source_code=hoisted_func_code,
            module_path=parent_func.module_path,
            is_nested=False,  # No longer nested after hoisting
            is_local=True,
            dependencies=[],
            closure_vars=closure_vars,
            ast_node=None,
        )

    def _analyze_closure_variables(
        self, nested_func: ast.FunctionDef, parent_func: ast.FunctionDef
    ) -> List[str]:
        """Analyze which variables the nested function captures from parent scope."""
        closure_vars = []

        # Get all variable names used in nested function
        used_names = set()
        for node in ast.walk(nested_func):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                used_names.add(node.id)

        # Get parameter names of nested function (these are not closure vars)
        nested_params = {arg.arg for arg in nested_func.args.args}

        # Get all variable names defined in parent function
        parent_vars = set()
        for node in ast.walk(parent_func):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                parent_vars.add(node.id)
            elif isinstance(node, ast.arg):
                parent_vars.add(node.arg)

        # Closure variables are those used in nested but defined in parent
        for name in used_names:
            if name not in nested_params and name in parent_vars:
                closure_vars.append(name)

        return closure_vars

    def _create_hoisted_function(
        self, nested_func: ast.FunctionDef, hoisted_name: str, closure_vars: List[str]
    ) -> str:
        """Create source code for hoisted function with closure vars as parameters."""

        # Create new function arguments: original args + closure vars
        new_args = []

        # Add closure variables as first parameters
        for var_name in closure_vars:
            new_args.append(ast.arg(arg=var_name, annotation=None))

        # Add original parameters
        for arg in nested_func.args.args:
            new_args.append(arg)

        # Create new function definition
        hoisted_func = ast.FunctionDef(
            name=hoisted_name,
            args=ast.arguments(
                posonlyargs=[],
                args=new_args,
                vararg=nested_func.args.vararg,
                kwonlyargs=nested_func.args.kwonlyargs,
                kw_defaults=nested_func.args.kw_defaults,
                kwarg=nested_func.args.kwarg,
                defaults=nested_func.args.defaults,
            ),
            body=nested_func.body,
            decorator_list=[],
            returns=nested_func.returns,
            type_comment=getattr(nested_func, "type_comment", None),
            lineno=getattr(nested_func, "lineno", 1),
            col_offset=getattr(nested_func, "col_offset", 0),
        )

        # Convert back to source code
        if hasattr(ast, "unparse"):
            return ast.unparse(hoisted_func)
        else:
            # Fallback for older Python versions
            args_str = ", ".join(
                closure_vars + [arg.arg for arg in nested_func.args.args]
            )
            return f"def {hoisted_name}({args_str}):\n    # Hoisted function body\n    pass"

    def _topological_sort(self, dependencies: List) -> List:
        """Sort dependencies in topological order."""
        # For now, return as-is
        # More sophisticated topological sorting could be implemented
        return dependencies

    def _generate_flattened_code_advanced(
        self, main_func, sorted_deps: List, external_modules: List[str]
    ) -> str:
        """Generate complete flattened code with all dependencies."""

        code_parts = []

        # Add external imports
        if external_modules:
            code_parts.append("# External imports")
            for module in external_modules:
                code_parts.append(f"import {module}")
            code_parts.append("")

        # Add hoisted function definitions
        if sorted_deps:
            code_parts.append("# Hoisted function definitions")
            for dep in sorted_deps:
                code_parts.append(dep.source_code)
                code_parts.append("")

        # Add main function (potentially modified to call hoisted functions)
        code_parts.append("# Main function")
        main_code = self._modify_main_function_calls(main_func, sorted_deps)
        code_parts.append(main_code)

        return "\n".join(code_parts)

    def _modify_main_function_calls(self, main_func, hoisted_deps: List) -> str:
        """Modify main function to call hoisted functions instead of nested ones."""

        if not main_func.ast_node:
            return main_func.source_code

        # Create a transformer to replace nested function calls
        class NestedCallTransformer(ast.NodeTransformer):
            def __init__(self, hoisted_mapping):
                self.hoisted_mapping = hoisted_mapping  # original_name -> hoisted_name

            def visit_Call(self, node):
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                    if func_name in self.hoisted_mapping:
                        # Replace with hoisted function call.
                        #
                        # The closure variables that _create_hoisted_function
                        # prepended to the hoisted signature are NOT passed
                        # here, so a hoisted function that captured anything
                        # is called with the wrong arity (#90). Not fixed on
                        # purpose: this rewriter has no caller in the
                        # execution path, and giving it one would mean
                        # shipping a callable whose equivalence to the user's
                        # function cannot be checked. See the module
                        # docstring; the recommendation is to delete this
                        # class outright rather than complete it.
                        node.func.id = self.hoisted_mapping[func_name]

                return self.generic_visit(node)

        # Build mapping of original -> hoisted names
        hoisted_mapping = {}
        for dep in hoisted_deps:
            # Extract original name from hoisted name
            if "_hoisted" in dep.name:
                parts = dep.name.split("_")
                if len(parts) >= 3:
                    original_name = parts[-2]  # Function name before _hoisted
                    hoisted_mapping[original_name] = dep.name

        # Transform the AST
        transformer = NestedCallTransformer(hoisted_mapping)
        import copy

        modified_ast = transformer.visit(copy.deepcopy(main_func.ast_node))

        # Convert back to source
        if hasattr(ast, "unparse"):
            return ast.unparse(modified_ast)
        else:
            return main_func.source_code  # Fallback

    def _handle_circular_dependencies(self, dep_info) -> Dict[str, Any]:
        """Handle circular dependencies by merging functions."""
        return {
            "success": False,
            "error": f"Circular dependencies not yet supported: {dep_info.circular_dependencies}",
            "fallback_strategy": "use_subprocess_pattern",
        }


def _exec_flattened_code(code: str, expected_name: str) -> Optional[Callable]:
    """Execute generated code and return the callable named ``expected_name``.

    Returns ``None`` if the code does not compile/run, or if it does not define
    a callable under exactly that name.

    The exact-name requirement matters. The previous implementation picked the
    first namespace entry satisfying ``callable(obj) and func.__name__ in name``,
    and the advanced flattener names its hoisted helpers
    ``{parent}_{nested}_hoisted`` -- which contains the parent's name. So for
    ``def outer(...)`` with a nested ``inner``, the substring test matched
    ``outer_inner_hoisted`` (the helper) before it ever reached ``outer``, and
    the helper was returned as the "successfully flattened" function.
    """
    namespace: Dict[str, Any] = {}
    try:
        exec(code, namespace)
    except Exception as e:
        logger.error("Generated flattened code did not execute: %s", e)
        return None

    candidate = namespace.get(expected_name)
    if not callable(candidate):
        logger.warning(
            "Generated flattened code defined no callable named %r", expected_name
        )
        return None
    return candidate


def _accepts_same_signature(flattened: Callable, original: Callable) -> bool:
    """Check the replacement can be called exactly like the original.

    This is a necessary condition, not a sufficient one: matching signatures do
    not prove matching results. Equivalence of a rewritten function cannot be
    established without running it, which is why nothing in the execution path
    substitutes a flattened function (see ``clustrix.decorator._execute_single``).
    """
    try:
        return inspect.signature(flattened) == inspect.signature(original)
    except (TypeError, ValueError) as e:
        logger.warning("Could not compare signatures: %s", e)
        return False


def auto_flatten_if_needed(func: Callable) -> Tuple[Callable, Optional[Dict[str, Any]]]:
    """
    Attempt to flatten a function if it exceeds complexity thresholds.

    Args:
        func: Function to potentially flatten

    Returns:
        ``(callable, info)``.

        ``info`` is ``None`` when no flattening was attempted at all -- either
        the function is not complex, or its source could not be read so there
        is nothing to rewrite.

        When flattening was attempted, ``info`` is a dict whose keys mean
        exactly what they say:

        * ``flattened`` -- ``True`` iff the returned callable is a genuinely
          different, usable callable produced by a flattener. ``False`` means
          the returned callable *is* ``func``.
        * ``success`` -- kept for backwards compatibility; identical to
          ``flattened``.
        * ``reason`` -- why flattening did not happen, when it did not.
        * ``strategy`` -- ``"advanced"`` or ``"basic"``, when it did.
        * ``details`` -- the raw result dict from the flattener that ran last.

        ``success`` used to be passed straight through from the flattener,
        where it meant "the AST analysis stage did not raise". That stayed
        ``True`` even when code generation produced something that would not
        compile and the original function was handed back instead -- so callers
        that keyed off ``success`` believed they had a flattened function when
        they had the original, and would equally have believed it if the
        generator had produced a callable that computed something else.
    """
    complexity_info = analyze_function_complexity(func)

    if not complexity_info.get("source_available", False):
        # Flattening rewrites source. There is no source. Do not pretend.
        logger.info(
            "Skipping flattening for %s: source is unavailable (%s)",
            getattr(func, "__name__", repr(func)),
            complexity_info.get("analysis_error"),
        )
        return func, None

    if not complexity_info.get("is_complex", False):
        # Function is simple enough, return as-is
        return func, None

    logger.info(
        "Function %s is complex (score: %s), attempting to flatten",
        func.__name__,
        complexity_info["complexity_score"],
    )

    info: Dict[str, Any] = {
        "attempted": True,
        "flattened": False,
        "success": False,
        "strategy": None,
        "reason": None,
        "details": None,
        "original_complexity": complexity_info,
    }

    # Check if function has nested functions - use advanced flattener
    if complexity_info.get("nested_functions", 0) > 0:
        logger.info(
            "Function %s has nested functions, using advanced flattener", func.__name__
        )
        try:
            # Use a minimal dependency analyzer that doesn't scan the whole project
            advanced_flattener = AdvancedFunctionFlattener(root_dir=None)
            advanced_result = advanced_flattener.flatten_with_dependencies(func)
            info["details"] = advanced_result

            if advanced_result.get("success", False):
                # The advanced flattener keeps the original function name.
                candidate = _exec_flattened_code(
                    advanced_result["flattened_function"], func.__name__
                )
                if candidate is not None and _accepts_same_signature(candidate, func):
                    info.update(
                        {"flattened": True, "success": True, "strategy": "advanced"}
                    )
                    logger.info(
                        "Successfully created advanced flattened function for %s",
                        func.__name__,
                    )
                    return candidate, info
                info["reason"] = "advanced flattener produced no usable callable"
            else:
                info["reason"] = (
                    f"advanced flattening failed: {advanced_result.get('error')}"
                )
                logger.warning(info["reason"])
        except Exception as e:
            info["reason"] = f"advanced flattener crashed: {e}"
            logger.error(info["reason"])

    # Use basic flattener (original implementation)
    flattener = FunctionFlattener()
    basic_result = flattener.flatten_function(func, complexity_info)
    info["details"] = basic_result

    if not basic_result.get("success", False):
        info["reason"] = (
            f"basic flattening failed: {basic_result.get('error', 'unknown error')}"
        )
        logger.warning("Failed to flatten %s: %s", func.__name__, info["reason"])
        return func, info

    flattened_name = f"{func.__name__}_flattened"
    candidate = _exec_flattened_code(basic_result["main_function"], flattened_name)
    if candidate is not None and _accepts_same_signature(candidate, func):
        info.update({"flattened": True, "success": True, "strategy": "basic"})
        logger.info("Successfully flattened %s into %s", func.__name__, flattened_name)
        return candidate, info

    if info["reason"] is None:
        info["reason"] = "basic flattener produced no usable callable"
    logger.warning("Not flattening %s: %s", func.__name__, info["reason"])
    return func, info
