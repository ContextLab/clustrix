"""
Automatic function flattening for complexity threshold management.

This module provides automatic refactoring of complex functions to meet
the complexity requirements for remote execution, particularly for two-venv
environments that have strict function complexity limits.
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
        Dictionary with complexity metrics
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
        logger.warning(f"Complexity analysis failed: {e}")
        return {
            "complexity_score": 999,
            "is_complex": True,
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
        param_names = [param.name for param in params]

        # Build simplified main function with correct signature
        main_code = f"""
def {func_name}_flattened({param_string}):
    \"\"\"Flattened version of {func_name} for remote execution.\"\"\"
    import subprocess
    import json

    # Prepare parameter values for injection into subprocess
    param_dict = {{{", ".join([f'"{param}": {param}' for param in param_names])}}}
    
    # Build the computation code with parameter injection
    computation_code = \"\"\"
import json

# Injected parameters
{chr(10).join([f'{param} = json.loads(r"""{{{param}}}""")' for param in param_names]) if param_names else "# No parameters to inject"}

# Flattened computation code
{self._build_flattened_computation(components)}
\"\"\"

    # Execute flattened computation using subprocess pattern
    formatted_code = computation_code.format(**{{k: json.dumps(v) for k, v in param_dict.items()}}) if param_dict else computation_code
    result = subprocess.run([
        "python", "-c", formatted_code
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
       universal_newlines=True, timeout=120)

    if result.returncode != 0:
        return {{"success": False, "error": result.stderr}}

    # Parse result from stdout
    output_lines = result.stdout.strip().split('\\n')
    parsed_result = None

    for line in output_lines:
        if line.startswith('RESULT:'):
            try:
                parsed_result = json.loads(line[7:])  # Remove 'RESULT:' prefix
            except:
                parsed_result = line[7:]  # Fallback to string
            break

    return {{"success": True, "result": parsed_result, "output": result.stdout}}
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

        return "\\n".join(code_parts)

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
                        # Replace with hoisted function call
                        # Need to add closure variables as arguments
                        node.func.id = self.hoisted_mapping[func_name]
                        # TODO: Add closure variable arguments

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


def auto_flatten_if_needed(func: Callable) -> Tuple[Callable, Optional[Dict[str, Any]]]:
    """
    Automatically flatten a function if it exceeds complexity thresholds.

    Args:
        func: Function to potentially flatten

    Returns:
        Tuple of (possibly_flattened_function, flattening_info)
    """
    # Analyze complexity
    complexity_info = analyze_function_complexity(func)

    if not complexity_info.get("is_complex", False):
        # Function is simple enough, return as-is
        return func, None

    logger.info(
        f"Function {func.__name__} is complex (score: {complexity_info['complexity_score']}), attempting to flatten"
    )

    # Check if function has nested functions - use advanced flattener
    if complexity_info.get("nested_functions", 0) > 0:
        logger.info(
            f"Function {func.__name__} has nested functions, using advanced flattener"
        )

        try:
            # Use a minimal dependency analyzer that doesn't scan the whole project
            advanced_flattener = AdvancedFunctionFlattener(root_dir=None)
            flattening_result = advanced_flattener.flatten_with_dependencies(func)

            if flattening_result.get("success", False):
                # Create executable function from flattened code
                try:
                    flattened_code = flattening_result["flattened_function"]

                    # Execute the flattened code to create callable
                    namespace_advanced: Dict[str, Any] = {}
                    exec(flattened_code, namespace_advanced)

                    # Find the main function in the namespace
                    flattened_func = None
                    for name, obj in namespace_advanced.items():
                        if callable(obj) and func.__name__ in name:
                            flattened_func = obj
                            break

                    if flattened_func:
                        logger.info(
                            f"Successfully created advanced flattened function for {func.__name__}"
                        )
                        return flattened_func, flattening_result
                    else:
                        logger.warning("Could not find flattened function in namespace")
                        # Fall back to basic flattening

                except Exception as e:
                    logger.error(f"Error executing advanced flattened code: {e}")
                    # Fall back to basic flattening
            else:
                logger.warning(
                    "Advanced flattening failed: %s", flattening_result.get("error")
                )
                # Fall back to basic flattening

        except Exception as e:
            logger.error(f"Advanced flattener crashed: {e}")
            # Fall back to basic flattening

    # Use basic flattener (original implementation)
    flattener = FunctionFlattener()
    flattening_result = flattener.flatten_function(func, complexity_info)

    if not flattening_result.get("success", False):
        logger.warning(
            f"Failed to flatten {func.__name__}: {flattening_result.get('error', 'unknown error')}"
        )
        return func, flattening_result

    # Create flattened function
    try:
        main_func_code = flattening_result["main_function"]

        # Execute the flattened function code to create callable
        namespace: Dict[str, Any] = {}
        exec(main_func_code, namespace)

        flattened_func_name = f"{func.__name__}_flattened"
        flattened_func = namespace.get(flattened_func_name)

        if flattened_func:
            logger.info(
                f"Successfully flattened {func.__name__} into {flattened_func_name}"
            )
            return flattened_func, flattening_result
        else:
            logger.error(f"Could not create flattened function {flattened_func_name}")
            return func, flattening_result

    except Exception as e:
        logger.error(f"Error creating flattened function: {e}")
        return func, flattening_result


def create_simple_subprocess_fallback(func: Callable, *args, **kwargs) -> Callable:
    """
    Create a simple subprocess-based fallback for complex functions.

    This is used when automatic flattening fails or is not appropriate.
    """

    def simple_fallback():
        """Simple subprocess fallback pattern."""
        import subprocess
        import json

        # Serialize the original function and arguments - simplified approach
        # func_data = {"function_name": func.__name__, "args": args, "kwargs": kwargs}

        # Create simple execution code
        exec_code = """
import json
import sys

# Simple execution pattern
try:
    # This would be replaced with specific function logic
    result = "Function execution completed"
    print(f'RESULT:{json.dumps(result)}')
except Exception as e:
    print(f'ERROR:{str(e)}')
    sys.exit(1)
"""

        result = subprocess.run(
            ["python", "-c", exec_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=120,
        )

        if result.returncode != 0:
            return {"success": False, "error": result.stderr}

        # Parse result
        output = result.stdout.strip()
        for line in output.split("\n"):
            if line.startswith("RESULT:"):
                try:
                    return json.loads(line[7:])
                except Exception:
                    return line[7:]

        return {"success": False, "error": "No result found"}

    return simple_fallback
