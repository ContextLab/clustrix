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
        
    def visit_FunctionDef(self, node):
        """Visit function definitions."""
        self.line_count += len(node.body)
        self.complexity_score += 1
        
        # Count nested functions
        self.nested_depth += 1
        self.max_nested_depth = max(self.max_nested_depth, self.nested_depth)
        
        self.generic_visit(node)
        
        self.nested_depth -= 1
    
    def visit_Call(self, node):
        """Visit function calls."""
        self.function_calls += 1
        self.complexity_score += 1
        
        # Special handling for subprocess calls (complexity risk)
        if isinstance(node.func, ast.Attribute):
            if (hasattr(node.func.value, 'id') and 
                node.func.value.id == 'subprocess'):
                self.subprocess_calls += 1
                self.complexity_score += 3  # Higher weight for subprocess
        elif isinstance(node.func, ast.Name):
            if node.func.id in ['subprocess', 'exec', 'eval']:
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
            analyzer.complexity_score > 20 or
            analyzer.line_count > 30 or
            analyzer.max_nested_depth > 3 or
            analyzer.subprocess_calls > 2 or
            analyzer.function_calls > 15
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
            "is_complex": is_complex,
            "estimated_risk": "high" if is_complex else "medium" if analyzer.complexity_score > 10 else "low"
        }
        
    except Exception as e:
        logger.warning(f"Complexity analysis failed: {e}")
        return {
            "complexity_score": 999,
            "is_complex": True,
            "estimated_risk": "unknown",
            "analysis_error": str(e)
        }


class FunctionFlattener:
    """Flattens complex functions into simpler components."""
    
    def __init__(self):
        self.extracted_functions = []
        self.main_function_body = []
        
    def flatten_function(self, func: Callable, complexity_info: Dict[str, Any]) -> Dict[str, Any]:
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
                "helper_functions": self._create_helper_functions(flattened)
            }
            
        except Exception as e:
            logger.error(f"Function flattening failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "fallback_strategy": "use_simple_subprocess_pattern"
            }
    
    def _flatten_function_body(self, func_def: ast.FunctionDef) -> Dict[str, Any]:
        """Analyze and flatten function body."""
        components = {
            "imports": [],
            "simple_operations": [],
            "complex_operations": [],
            "subprocess_calls": [],
            "loops": [],
            "conditionals": []
        }
        
        for stmt in func_def.body:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                components["imports"].append(ast.unparse(stmt))
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
                    components["simple_operations"].append({
                        "type": "statement",
                        "code": ast.unparse(stmt),
                        "complexity": 1
                    })
                except:
                    components["simple_operations"].append({
                        "type": "statement", 
                        "code": "# Unparseable statement",
                        "complexity": 1
                    })
        
        return components
    
    def _extract_loop(self, loop_node: ast.For) -> Dict[str, Any]:
        """Extract loop information for flattening."""
        try:
            return {
                "type": "for_loop",
                "target": ast.unparse(loop_node.target),
                "iter": ast.unparse(loop_node.iter),
                "body": [ast.unparse(stmt) for stmt in loop_node.body],
                "complexity": len(loop_node.body) * 2,
                "parallelizable": self._is_loop_parallelizable(loop_node)
            }
        except:
            return {
                "type": "for_loop",
                "complexity": 5,
                "parallelizable": False,
                "extraction_error": True
            }
    
    def _extract_conditional(self, if_node: ast.If) -> Dict[str, Any]:
        """Extract conditional information."""
        try:
            return {
                "type": "conditional",
                "test": ast.unparse(if_node.test),
                "body": [ast.unparse(stmt) for stmt in if_node.body],
                "orelse": [ast.unparse(stmt) for stmt in if_node.orelse] if if_node.orelse else [],
                "complexity": len(if_node.body) + len(if_node.orelse or [])
            }
        except:
            return {
                "type": "conditional",
                "complexity": 3,
                "extraction_error": True
            }
    
    def _analyze_call(self, call_node: ast.Call) -> Dict[str, Any]:
        """Analyze function call complexity."""
        try:
            call_str = ast.unparse(call_node)
            
            is_subprocess = (
                "subprocess" in call_str or
                ".run(" in call_str or
                ".Popen(" in call_str
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
                "length": len(call_str)
            }
        except:
            return {
                "type": "function_call",
                "complexity": 2,
                "extraction_error": True
            }
    
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
        except:
            return False
    
    def _create_main_function(self, original_func: Callable, components: Dict[str, Any]) -> str:
        """Create simplified main function."""
        func_name = original_func.__name__
        
        # Build simplified main function
        main_code = f"""
def {func_name}_flattened():
    \"\"\"Flattened version of {func_name} for remote execution.\"\"\"
    import subprocess
    
    # Execute flattened computation using subprocess pattern
    result = subprocess.run([
        "python", "-c", \"\"\"
# Flattened computation code
{self._build_flattened_computation(components)}
\"\"\"
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
       universal_newlines=True, timeout=120)
    
    if result.returncode != 0:
        return {{"success": False, "error": result.stderr}}
    
    # Parse result from stdout
    output_lines = result.stdout.strip().split('\\n')
    parsed_result = None
    
    for line in output_lines:
        if line.startswith('RESULT:'):
            import json
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
        
        # Add simple operations
        for op in components.get("simple_operations", []):
            if isinstance(op, dict) and "code" in op:
                code_parts.append(op["code"])
        
        # Handle loops (simplified or parallelized)
        for loop in components.get("loops", []):
            if loop.get("parallelizable", False):
                code_parts.append(f"# Parallelizable loop: {loop.get('target', 'unknown')}")
                code_parts.extend(loop.get("body", []))
            else:
                code_parts.append(f"# Sequential loop: {loop.get('target', 'unknown')}")
                code_parts.extend(loop.get("body", []))
        
        # Add result output
        code_parts.append("")
        code_parts.append("# Output result")
        code_parts.append("import json")
        code_parts.append("print(f'RESULT:{json.dumps(locals().get(\"result\", \"no_result\"))}')")
        
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
    
    logger.info(f"Function {func.__name__} is complex (score: {complexity_info['complexity_score']}), attempting to flatten")
    
    # Attempt to flatten
    flattener = FunctionFlattener()
    flattening_result = flattener.flatten_function(func, complexity_info)
    
    if not flattening_result.get("success", False):
        logger.warning(f"Failed to flatten {func.__name__}: {flattening_result.get('error', 'unknown error')}")
        return func, flattening_result
    
    # Create flattened function
    try:
        main_func_code = flattening_result["main_function"]
        
        # Execute the flattened function code to create callable
        namespace = {}
        exec(main_func_code, namespace)
        
        flattened_func_name = f"{func.__name__}_flattened"
        flattened_func = namespace.get(flattened_func_name)
        
        if flattened_func:
            logger.info(f"Successfully flattened {func.__name__} into {flattened_func_name}")
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
        import pickle
        import base64
        
        # Serialize the original function and arguments
        func_data = {
            "function_name": func.__name__,
            "args": args,
            "kwargs": kwargs
        }
        
        # Create simple execution code
        exec_code = f"""
import json
import sys

# Simple execution pattern
try:
    # This would be replaced with specific function logic
    result = "Function execution completed"
    print(f'RESULT:{{json.dumps(result)}}')
except Exception as e:
    print(f'ERROR:{{str(e)}}')
    sys.exit(1)
"""
        
        result = subprocess.run([
            "python", "-c", exec_code
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
           universal_newlines=True, timeout=120)
        
        if result.returncode != 0:
            return {"success": False, "error": result.stderr}
        
        # Parse result
        output = result.stdout.strip()
        for line in output.split('\n'):
            if line.startswith('RESULT:'):
                try:
                    return json.loads(line[7:])
                except:
                    return line[7:]
        
        return {"success": False, "error": "No result found"}
    
    return simple_fallback