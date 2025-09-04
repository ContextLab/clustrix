"""Enhanced loop detection and analysis for parallel execution."""

import ast
import inspect
from typing import Any, Dict, List, Optional, Callable, Set
import logging

logger = logging.getLogger(__name__)


def try_parse_ast_safely(source_code: str) -> Optional[ast.AST]:
    """Safely parse AST with comprehensive error handling.

    Args:
        source_code: Python source code string

    Returns:
        Parsed AST tree or None if parsing fails
    """
    try:
        if not source_code or not isinstance(source_code, str):
            logger.debug("Invalid source code provided for parsing")
            return None

        # Remove common formatting issues
        source_code = source_code.strip()
        if not source_code:
            logger.debug("Empty source code after stripping")
            return None

        # Try to parse the AST
        tree = ast.parse(source_code)
        return tree

    except SyntaxError as e:
        logger.debug(f"Syntax error in source code: {e}")
        return None
    except ValueError as e:
        logger.debug(f"Value error parsing AST: {e}")
        return None
    except Exception as e:
        logger.debug(f"Unexpected error parsing AST: {e}")
        return None


def handle_malformed_nodes(node: ast.AST, node_type_name: str = "Unknown") -> bool:
    """Check if an AST node is malformed and handle gracefully.

    Args:
        node: AST node to check
        node_type_name: Name of the node type for logging

    Returns:
        True if node is valid, False if malformed
    """
    try:
        if node is None:
            logger.debug(f"None {node_type_name} node encountered")
            return False

        # Check for basic AST node attributes
        if not hasattr(node, "__class__"):
            logger.debug(f"Malformed {node_type_name} node: missing __class__")
            return False

        # Try to access common AST attributes that should always be present
        try:
            _ = type(node).__name__
        except Exception:
            logger.debug(f"Malformed {node_type_name} node: cannot access type name")
            return False

        return True

    except Exception as e:
        logger.debug(f"Error checking {node_type_name} node validity: {e}")
        return False


def validate_python_version_compatibility(
    feature_name: str, min_version: tuple = (3, 8)
) -> bool:
    """Validate Python version compatibility for specific features.

    Args:
        feature_name: Name of the feature to check
        min_version: Minimum required Python version tuple

    Returns:
        True if current Python version supports the feature
    """
    import sys

    try:
        current_version = sys.version_info[:2]

        if current_version < min_version:
            logger.debug(
                f"Feature '{feature_name}' requires Python {min_version}, "
                f"current version is {current_version}"
            )
            return False

        return True

    except Exception as e:
        logger.debug(f"Error checking Python version compatibility: {e}")
        # Assume compatibility if we can't check
        return True


def performance_limit_check(structure_size: int, max_size: int = 10000) -> bool:
    """Check if a code structure exceeds performance limits.

    Args:
        structure_size: Size of the structure (e.g., number of nodes, nesting level)
        max_size: Maximum allowed size

    Returns:
        True if within limits, False if exceeds limits
    """
    try:
        if structure_size < 0:
            logger.debug("Invalid structure size (negative value)")
            return False

        if structure_size > max_size:
            logger.debug(f"Structure size {structure_size} exceeds limit {max_size}")
            return False

        return True

    except Exception as e:
        logger.debug(f"Error checking performance limits: {e}")
        # Be conservative and assume it's within limits
        return True


def _ast_to_string(node) -> str:
    """Convert AST node to string for Python < 3.9.

    Enhanced with robust error handling for malformed nodes.
    """
    try:
        if node is None:
            return "None"

        if isinstance(node, ast.Name):
            node_id = getattr(node, "id", None)
            return node_id if node_id is not None else "UnknownName"
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                try:
                    args = [_ast_to_string(arg) for arg in (getattr(node, "args", []))]
                    func_name = getattr(node.func, "id", "unknown_func")
                    return f"{func_name}({', '.join(args)})"
                except (AttributeError, TypeError):
                    return "malformed_call"
            return "call"
        elif isinstance(node, ast.Constant):
            try:
                return str(node.value)
            except (AttributeError, TypeError):
                return "malformed_constant"
        elif isinstance(node, ast.Num):  # Python < 3.8
            try:
                return str(node.n)
            except (AttributeError, TypeError):
                return "malformed_num"
        else:
            return str(type(node).__name__)
    except Exception as e:
        logger.debug(f"Error converting AST node to string: {e}")
        return f"ast_error_{type(node).__name__}"


class LoopInfo:
    """Information about a detected loop."""

    def __init__(
        self,
        loop_type: str,
        variable: Optional[str] = None,
        iterable: Optional[str] = None,
        range_info: Optional[Dict[str, int]] = None,
        nested_level: int = 0,
        dependencies: Optional[Set[str]] = None,
    ):
        self.loop_type = loop_type  # 'for' or 'while'
        self.variable = variable  # loop variable name
        self.iterable = iterable  # string representation of iterable
        self.range_info = range_info  # {start, stop, step} for range loops
        self.nested_level = nested_level  # nesting depth
        self.dependencies = dependencies or set()  # variables this loop depends on
        self.is_parallelizable = self._assess_parallelizability()

    def _assess_parallelizability(self) -> bool:
        """Assess if this loop can be parallelized."""
        # While loops are generally harder to parallelize due to unknown iteration count
        if self.loop_type == "while":
            return False

        # Check for dependencies that prevent parallelization
        if self.dependencies and len(self.dependencies) > 0:
            # If loop depends on external variables that might be modified,
            # parallelization is risky
            return False

        # Range-based loops with known bounds are excellent candidates
        if self.range_info:
            start = self.range_info["start"]
            stop = self.range_info["stop"]
            step = self.range_info["step"]

            # Ensure we have positive iteration count
            if step > 0 and stop > start:
                iteration_count = (stop - start + step - 1) // step
                # Only parallelize if we have enough work to justify overhead
                return iteration_count >= 3  # Lowered threshold for compatibility
            elif step < 0 and stop < start:
                iteration_count = (start - stop - step - 1) // (-step)
                return iteration_count >= 3  # Lowered threshold for compatibility
            else:
                return False

        # Check for common parallelizable patterns
        if self.iterable:
            # Lists, tuples, and other collections are often parallelizable
            # if they don't involve complex iteration patterns
            parallelizable_patterns = [
                "list(",
                "tuple(",
                "enumerate(",
                "zip(",
                "itertools.",
                "numpy.",
                "np.",
                "pandas.",
                "pd.",
            ]

            iterable_lower = self.iterable.lower()
            for pattern in parallelizable_patterns:
                if pattern in iterable_lower:
                    return True

        # Conservative default: only parallelize if we're confident
        return False

    def estimate_parallelization_benefit(self) -> float:
        """
        Estimate the potential benefit of parallelizing this loop.

        Returns:
            Float between 0.0 and 1.0 indicating parallelization benefit
        """
        if not self.is_parallelizable:
            return 0.0

        benefit_score = 0.0

        # Factor 1: Iteration count (more iterations = more benefit)
        if self.range_info:
            start = self.range_info["start"]
            stop = self.range_info["stop"]
            step = self.range_info["step"]

            if step > 0 and stop > start:
                iteration_count = (stop - start + step - 1) // step
            elif step < 0 and stop < start:
                iteration_count = (start - stop - step - 1) // (-step)
            else:
                iteration_count = 0

            # Scale benefit based on iteration count
            if iteration_count >= 1000:
                benefit_score += 0.4
            elif iteration_count >= 100:
                benefit_score += 0.3
            elif iteration_count >= 10:
                benefit_score += 0.2
        else:
            # Unknown iteration count, assume moderate benefit
            benefit_score += 0.2

        # Factor 2: Nested level (lower nesting = easier parallelization)
        if self.nested_level == 0:
            benefit_score += 0.3
        elif self.nested_level == 1:
            benefit_score += 0.2
        else:
            benefit_score += 0.1

        # Factor 3: No dependencies (independent iterations = high benefit)
        if not self.dependencies:
            benefit_score += 0.3
        else:
            benefit_score += 0.1

        return min(benefit_score, 1.0)

    def suggest_parallelization_strategy(self) -> Dict[str, Any]:
        """
        Suggest an appropriate parallelization strategy for this loop.

        Returns:
            Dictionary containing strategy recommendations
        """
        if not self.is_parallelizable:
            return {
                "strategy": "none",
                "reason": "Loop is not suitable for parallelization",
                "alternatives": [],
            }

        strategy: Dict[str, Any] = {
            "strategy": "parallel_map",
            "chunk_size": "auto",
            "executor_type": "process",
            "alternatives": [],
            "considerations": [],
        }

        # Determine optimal executor type
        if self.range_info:
            iteration_count = self._get_iteration_count()

            if iteration_count < 100:
                strategy["executor_type"] = "thread"
                strategy["considerations"].append(
                    "Small iteration count - threads preferred for lower overhead"
                )
            elif iteration_count > 10000:
                strategy["executor_type"] = "process"
                strategy["chunk_size"] = max(10, iteration_count // 100)
                strategy["considerations"].append(
                    "Large iteration count - chunking recommended"
                )
            else:
                strategy["executor_type"] = "process"

        # Check for NumPy/Pandas opportunities
        if self.iterable and any(
            lib in self.iterable.lower() for lib in ["numpy", "np.", "pandas", "pd."]
        ):
            strategy["alternatives"].append("vectorization")
            strategy["considerations"].append(
                "Consider NumPy/Pandas vectorized operations"
            )

        # Nested loop considerations
        if self.nested_level > 0:
            strategy["considerations"].append(
                f"Nested loop at level {self.nested_level} - consider parallelizing outer loop instead"
            )

        return strategy

    def _get_iteration_count(self) -> int:
        """Get the estimated iteration count for this loop."""
        if self.range_info:
            start = self.range_info["start"]
            stop = self.range_info["stop"]
            step = self.range_info["step"]

            if step > 0 and stop > start:
                return (stop - start + step - 1) // step
            elif step < 0 and stop < start:
                return (start - stop - step - 1) // (-step)

        return 100  # Default estimate

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "loop_type": self.loop_type,
            "variable": self.variable,
            "iterable": self.iterable,
            "range_info": self.range_info,
            "nested_level": self.nested_level,
            "dependencies": list(self.dependencies),
            "is_parallelizable": self.is_parallelizable,
        }

    def enhanced_dependency_analysis(
        self, ast_body: Optional[List[ast.stmt]] = None
    ) -> Dict[str, Any]:
        """
        Perform enhanced dependency analysis with detailed variable tracking.

        Args:
            ast_body: Optional AST body to analyze for dependencies

        Returns:
            Dictionary with detailed dependency information
        """
        analysis: Dict[str, Any] = {
            "read_variables": set(self.dependencies),
            "write_variables": set(),
            "loop_carried_deps": [],
            "reduction_vars": [],
            "array_dependencies": [],
            "function_calls": [],
            "control_flow": {
                "has_break": False,
                "has_continue": False,
                "has_return": False,
                "has_exceptions": False,
            },
            "parallelization_blockers": [],
        }

        if ast_body is None:
            # If no AST body provided, return basic analysis
            return analysis

        # Use DependencyAnalyzer for detailed analysis
        from clustrix.loop_analysis import DependencyAnalyzer

        dep_analyzer = DependencyAnalyzer()
        dep_analyzer.loop_var = self.variable

        for stmt in ast_body:
            dep_analyzer.visit(stmt)

        # Update analysis with detailed findings
        analysis["read_variables"] = dep_analyzer.reads
        analysis["write_variables"] = dep_analyzer.writes
        analysis["function_calls"] = list(dep_analyzer.function_calls)
        analysis["array_dependencies"] = list(dep_analyzer.array_accesses)

        # Control flow analysis
        analysis["control_flow"]["has_break"] = dep_analyzer.has_break
        analysis["control_flow"]["has_continue"] = dep_analyzer.has_continue
        analysis["control_flow"]["has_return"] = dep_analyzer.has_return

        # Identify loop-carried dependencies
        shared_vars = dep_analyzer.reads & dep_analyzer.writes
        if self.variable:
            shared_vars.discard(self.variable)  # Exclude loop variable

        for var in shared_vars:
            analysis["loop_carried_deps"].append(var)
            analysis["parallelization_blockers"].append(
                f"Loop-carried dependency: {var}"
            )

        # Identify reduction patterns
        for reduction_op in dep_analyzer.reduction_ops:
            if reduction_op["is_reduction"]:
                analysis["reduction_vars"].append(
                    {
                        "variable": reduction_op["variable"],
                        "operation": reduction_op["operation"],
                        "parallel_safe": True,
                    }
                )

        # Additional parallelization blockers
        if dep_analyzer.has_break or dep_analyzer.has_continue:
            analysis["parallelization_blockers"].append(
                "Control flow statements (break/continue)"
            )

        if dep_analyzer.has_return:
            analysis["parallelization_blockers"].append("Early return statement")

        # Check for array access conflicts
        array_writes = {
            acc.split("[")[0] for acc in dep_analyzer.array_accesses if "[write]" in acc
        }
        array_reads = {
            acc.split("[")[0] for acc in dep_analyzer.array_accesses if "[read]" in acc
        }
        shared_arrays = array_writes & array_reads

        for array_name in shared_arrays:
            analysis["parallelization_blockers"].append(
                f"Array read-write dependency: {array_name}"
            )

        return analysis

    def reduction_pattern_detection(
        self, ast_body: Optional[List[ast.stmt]] = None
    ) -> Dict[str, Any]:
        """
        Detect reduction patterns like sum(), max(), accumulation patterns.

        Args:
            ast_body: Optional AST body to analyze for reduction patterns

        Returns:
            Dictionary with detected reduction patterns
        """
        patterns: Dict[str, Any] = {
            "detected_reductions": [],
            "potential_reductions": [],
            "reduction_candidates": [],
            "parallelizable_reductions": [],
            "performance_impact": "low",
        }

        if ast_body is None:
            return patterns

        # Use DependencyAnalyzer to find reduction operations
        from clustrix.loop_analysis import DependencyAnalyzer

        dep_analyzer = DependencyAnalyzer()
        dep_analyzer.loop_var = self.variable

        for stmt in ast_body:
            dep_analyzer.visit(stmt)

        # Analyze reduction operations found
        for reduction_op in dep_analyzer.reduction_ops:
            reduction_info = {
                "variable": reduction_op["variable"],
                "operation": reduction_op["operation"],
                "pattern_type": self._classify_reduction_pattern(
                    reduction_op["operation"]
                ),
                "parallel_strategy": dep_analyzer._suggest_reduction_strategy(
                    reduction_op["operation"]
                ),
                "is_parallelizable": reduction_op.get("is_reduction", False),
            }

            if reduction_op.get("is_reduction", False):
                patterns["detected_reductions"].append(reduction_info)
                patterns["parallelizable_reductions"].append(reduction_info)
            else:
                patterns["potential_reductions"].append(reduction_info)

        # Look for common reduction patterns in function calls
        for func_call in dep_analyzer.function_calls:
            if func_call.lower() in ["sum", "max", "min", "any", "all", "reduce"]:
                patterns["reduction_candidates"].append(
                    {
                        "function": func_call,
                        "pattern_type": "builtin_reduction",
                        "parallel_strategy": f"{func_call}_parallel",
                        "is_parallelizable": True,
                    }
                )

        # Estimate performance impact
        total_reductions = len(patterns["detected_reductions"]) + len(
            patterns["reduction_candidates"]
        )
        if total_reductions >= 3:
            patterns["performance_impact"] = "high"
        elif total_reductions >= 1:
            patterns["performance_impact"] = "medium"

        return patterns

    def _classify_reduction_pattern(self, operation: str) -> str:
        """Classify the type of reduction pattern."""
        classification = {
            "Add": "summation",
            "Mult": "product",
            "BitOr": "bitwise_or",
            "BitAnd": "bitwise_and",
            "BitXor": "bitwise_xor",
        }
        return classification.get(operation, "custom")

    def parallelization_suggestions(self) -> Dict[str, Any]:
        """
        Provide improved parallelization strategy suggestions.

        Returns:
            Dictionary with enhanced parallelization suggestions
        """
        suggestions: Dict[str, Any] = {
            "primary_strategy": "none",
            "alternative_strategies": [],
            "optimization_opportunities": [],
            "performance_estimates": {},
            "implementation_complexity": "low",
            "recommended_tools": [],
            "caveats": [],
        }

        if not self.is_parallelizable:
            suggestions["caveats"].append("Loop not suitable for basic parallelization")
            suggestions["alternative_strategies"] = self._suggest_alternatives()
            return suggestions

        # Primary strategy based on loop characteristics
        if self.range_info:
            iteration_count = self._get_iteration_count()

            if iteration_count < 10:
                suggestions["primary_strategy"] = "sequential"
                suggestions["caveats"].append(
                    "Too few iterations for parallelization benefit"
                )
            elif iteration_count < 100:
                suggestions["primary_strategy"] = "thread_pool"
                suggestions["recommended_tools"].append(
                    "concurrent.futures.ThreadPoolExecutor"
                )
                suggestions["implementation_complexity"] = "low"
            elif iteration_count < 10000:
                suggestions["primary_strategy"] = "process_pool"
                suggestions["recommended_tools"].append(
                    "concurrent.futures.ProcessPoolExecutor"
                )
                suggestions["implementation_complexity"] = "medium"
            else:
                suggestions["primary_strategy"] = "distributed_processing"
                suggestions["recommended_tools"].extend(
                    ["multiprocessing.Pool", "joblib.Parallel", "dask.distributed"]
                )
                suggestions["implementation_complexity"] = "high"

        # Alternative strategies
        suggestions["alternative_strategies"] = self._suggest_alternatives()

        # Optimization opportunities
        if self.iterable and any(
            lib in self.iterable.lower() for lib in ["numpy", "np.", "pandas", "pd."]
        ):
            suggestions["optimization_opportunities"].append("vectorization")
            suggestions["recommended_tools"].append(
                "NumPy/Pandas vectorized operations"
            )

        if self.nested_level == 0:
            suggestions["optimization_opportunities"].append("memory_optimization")
            suggestions["recommended_tools"].append("Memory-efficient chunking")

        # Performance estimates
        benefit = self.estimate_parallelization_benefit()
        if benefit > 0.7:
            suggestions["performance_estimates"]["speedup_range"] = "2x-4x"
            suggestions["performance_estimates"]["confidence"] = "high"
        elif benefit > 0.4:
            suggestions["performance_estimates"]["speedup_range"] = "1.5x-3x"
            suggestions["performance_estimates"]["confidence"] = "medium"
        else:
            suggestions["performance_estimates"]["speedup_range"] = "1x-2x"
            suggestions["performance_estimates"]["confidence"] = "low"

        return suggestions

    def _suggest_alternatives(self) -> List[str]:
        """Suggest alternative approaches when direct parallelization isn't optimal."""
        alternatives: List[str] = []

        if self.loop_type == "while":
            alternatives.extend(
                [
                    "Convert to for-loop with known bounds",
                    "Use itertools for functional approach",
                    "Consider async/await for I/O bound operations",
                ]
            )

        if self.nested_level > 0:
            alternatives.append("Parallelize outer loop instead")

        if self.dependencies:
            alternatives.extend(
                [
                    "Refactor to reduce dependencies",
                    "Use reduction patterns where applicable",
                    "Consider pipeline parallelism",
                ]
            )

        if not alternatives:
            alternatives.append("Consider algorithmic improvements")

        return alternatives


class SafeRangeEvaluator(ast.NodeVisitor):
    """Safely evaluate range expressions without using eval()."""

    def __init__(self, local_vars: Optional[Dict[str, Any]] = None):
        self.local_vars = local_vars or {}
        self.result = None
        self.safe = True

    def visit_Call(self, node):
        """Visit function calls."""
        if isinstance(node.func, ast.Name) and node.func.id == "range":
            try:
                args = []
                for arg in node.args:
                    value = self._evaluate_node(arg)
                    if value is None:
                        self.safe = False
                        return
                    args.append(value)

                if len(args) == 1:
                    self.result = {"start": 0, "stop": args[0], "step": 1}
                elif len(args) == 2:
                    self.result = {
                        "start": args[0],
                        "stop": args[1],
                        "step": 1,
                    }
                elif len(args) == 3:
                    self.result = {
                        "start": args[0],
                        "stop": args[1],
                        "step": args[2],
                    }
                else:
                    self.safe = False

            except Exception:
                self.safe = False
        else:
            self.safe = False

    def _evaluate_node(self, node) -> Optional[int]:
        """Safely evaluate an AST node to get integer value.

        Enhanced with robust error handling for malformed nodes.
        """
        try:
            if node is None:
                return None

            if isinstance(node, ast.Constant):
                try:
                    value = getattr(node, "value", None)
                    return value if isinstance(value, int) else None
                except AttributeError:
                    return None
            elif isinstance(node, ast.Num):  # Python < 3.8
                try:
                    n_value = getattr(node, "n", None)
                    return n_value if isinstance(n_value, int) else None
                except AttributeError:
                    return None
            elif isinstance(node, ast.Name):
                try:
                    node_id = getattr(node, "id", None)
                    if node_id and self.local_vars:
                        value = self.local_vars.get(node_id)
                        return value if isinstance(value, int) else None
                    return None
                except (AttributeError, TypeError):
                    return None
            elif isinstance(node, ast.BinOp):
                return self._evaluate_binop(node)
            else:
                return None
        except Exception as e:
            logger.debug(f"Error evaluating AST node: {e}")
            self.safe = False
            return None

    def _evaluate_binop(self, node) -> Optional[int]:
        """Evaluate binary operations."""
        left = self._evaluate_node(node.left)
        right = self._evaluate_node(node.right)

        if left is None or right is None:
            return None

        try:
            if isinstance(node.op, ast.Add):
                result = left + right
            elif isinstance(node.op, ast.Sub):
                result = left - right
            elif isinstance(node.op, ast.Mult):
                result = left * right
            elif isinstance(node.op, ast.FloorDiv):
                if right != 0:
                    result = left // right
                else:
                    return None
            else:
                return None

            # Ensure we return an integer only
            if isinstance(result, int):
                return result
            elif isinstance(result, float) and result.is_integer():
                return int(result)
            else:
                return None

        except Exception:
            return None


class DependencyAnalyzer(ast.NodeVisitor):
    """Analyze variable dependencies in loop bodies."""

    def __init__(self):
        self.reads = set()  # Variables read in the loop
        self.writes = set()  # Variables written in the loop
        self.loop_var = None
        self.array_accesses = set()  # Array/list accesses
        self.function_calls = set()  # Function calls in the loop
        self.reduction_ops = []  # Potential reduction operations (+=, *=, etc.)
        self.has_break = False  # Break statements
        self.has_continue = False  # Continue statements
        self.has_return = False  # Return statements

    def visit_Name(self, node):
        """Visit variable names.

        Enhanced with robust error handling for malformed nodes.
        """
        try:
            if node is None:
                return

            node_id = getattr(node, "id", None)
            if not node_id:
                logger.debug("Name node missing 'id' attribute")
                return

            ctx = getattr(node, "ctx", None)
            if ctx is None:
                logger.debug(f"Name node '{node_id}' missing context")
                # Default to read access for safety
                self.reads.add(node_id)
            elif isinstance(ctx, ast.Load):
                self.reads.add(node_id)
            elif isinstance(ctx, ast.Store):
                self.writes.add(node_id)

        except Exception as e:
            logger.debug(f"Error processing Name node: {e}")

        try:
            self.generic_visit(node)
        except Exception as e:
            logger.debug(f"Error in generic visit for Name node: {e}")

    def visit_Subscript(self, node):
        """Visit array/list subscript operations.

        Enhanced with robust error handling for malformed nodes.
        """
        try:
            if node is None:
                return

            value_node = getattr(node, "value", None)
            if value_node and isinstance(value_node, ast.Name):
                value_id = getattr(value_node, "id", None)
                if value_id:
                    ctx = getattr(node, "ctx", None)
                    if isinstance(ctx, ast.Load):
                        self.array_accesses.add(f"{value_id}[read]")
                    elif isinstance(ctx, ast.Store):
                        self.array_accesses.add(f"{value_id}[write]")
                    else:
                        # Default to read for unknown context
                        self.array_accesses.add(f"{value_id}[read]")

        except Exception as e:
            logger.debug(f"Error processing Subscript node: {e}")

        try:
            self.generic_visit(node)
        except Exception as e:
            logger.debug(f"Error in generic visit for Subscript node: {e}")

    def visit_Call(self, node):
        """Visit function calls.

        Enhanced with robust error handling for malformed nodes.
        """
        try:
            if node is None:
                return

            func_node = getattr(node, "func", None)
            if func_node is None:
                logger.debug("Call node missing 'func' attribute")
                return

            if isinstance(func_node, ast.Name):
                func_id = getattr(func_node, "id", None)
                if func_id:
                    self.function_calls.add(func_id)
            elif isinstance(func_node, ast.Attribute):
                # Method calls like obj.method()
                try:
                    value_node = getattr(func_node, "value", None)
                    attr_name = getattr(func_node, "attr", None)

                    if value_node and isinstance(value_node, ast.Name) and attr_name:
                        value_id = getattr(value_node, "id", None)
                        if value_id:
                            self.function_calls.add(f"{value_id}.{attr_name}")
                except AttributeError:
                    logger.debug("Malformed method call in AST")

        except Exception as e:
            logger.debug(f"Error processing Call node: {e}")

        try:
            self.generic_visit(node)
        except Exception as e:
            logger.debug(f"Error in generic visit for Call node: {e}")

    def visit_AugAssign(self, node):
        """Visit augmented assignment operations (+=, -=, *=, etc.)."""
        if isinstance(node.target, ast.Name):
            op_type = type(node.op).__name__
            self.reduction_ops.append(
                {
                    "variable": node.target.id,
                    "operation": op_type,
                    "is_reduction": op_type
                    in ["Add", "Mult", "BitOr", "BitAnd", "BitXor"],
                }
            )
        self.generic_visit(node)

    def visit_Break(self, node):
        """Visit break statements."""
        self.has_break = True

    def visit_Continue(self, node):
        """Visit continue statements."""
        self.has_continue = True

    def visit_Return(self, node):
        """Visit return statements."""
        self.has_return = True

    def visit_For(self, node):
        """Visit for loops to track loop variables."""
        if isinstance(node.target, ast.Name):
            self.loop_var = node.target.id
            self.writes.add(node.target.id)

        # Visit the body
        for stmt in node.body:
            self.visit(stmt)

    def has_dependencies(self) -> bool:
        """Check if loop iterations have dependencies."""
        # If a variable is both read and written, there might be dependencies
        shared_vars = self.reads & self.writes
        # Exclude the loop variable itself
        if self.loop_var:
            shared_vars.discard(self.loop_var)
        return len(shared_vars) > 0

    def has_loop_carried_dependencies(self) -> bool:
        """Check for loop-carried dependencies that prevent parallelization."""
        # Control flow statements make parallelization complex
        if self.has_break or self.has_continue or self.has_return:
            return True

        # Check for problematic array access patterns
        array_writes = {
            acc.split("[")[0] for acc in self.array_accesses if "[write]" in acc
        }
        array_reads = {
            acc.split("[")[0] for acc in self.array_accesses if "[read]" in acc
        }

        # If the same array is both read and written, check for dependencies
        shared_arrays = array_writes & array_reads
        if shared_arrays:
            return True

        # Non-reduction operations on shared variables are problematic
        for op in self.reduction_ops:
            if not op["is_reduction"] and op["variable"] in self.reads:
                return True

        return False

    def get_reduction_opportunities(self) -> List[Dict[str, Any]]:
        """Identify reduction operations that can be parallelized."""
        reductions = []
        for op in self.reduction_ops:
            if op["is_reduction"]:
                reductions.append(
                    {
                        "variable": op["variable"],
                        "operation": op["operation"],
                        "strategy": self._suggest_reduction_strategy(op["operation"]),
                    }
                )
        return reductions

    def _suggest_reduction_strategy(self, operation: str) -> str:
        """Suggest a reduction strategy for the given operation."""
        strategies = {
            "Add": "sum_reduction",
            "Mult": "product_reduction",
            "BitOr": "bitwise_or_reduction",
            "BitAnd": "bitwise_and_reduction",
            "BitXor": "bitwise_xor_reduction",
        }
        return strategies.get(operation, "custom_reduction")


class LoopDetector(ast.NodeVisitor):
    """Enhanced loop detection with dependency analysis."""

    def __init__(self, local_vars: Optional[Dict[str, Any]] = None):
        self.loops: List[LoopInfo] = []
        self.current_level = 0
        self.local_vars = local_vars or {}

    def visit_For(self, node):
        """Visit for loops."""
        self.current_level += 1

        loop_info = self._analyze_for_loop(node)
        if loop_info:
            self.loops.append(loop_info)

        # Visit nested loops
        for stmt in node.body:
            self.visit(stmt)

        self.current_level -= 1

    def visit_While(self, node):
        """Visit while loops."""
        self.current_level += 1

        loop_info = self._analyze_while_loop(node)
        if loop_info:
            self.loops.append(loop_info)

        # Visit nested loops
        for stmt in node.body:
            self.visit(stmt)

        self.current_level -= 1

    def visit_ListComp(self, node):
        """Visit list comprehensions."""
        self.current_level += 1

        loop_info = self._analyze_comprehension(node, "list_comp")
        if loop_info:
            self.loops.append(loop_info)

        # Visit nested comprehensions and expressions
        self.generic_visit(node)

        self.current_level -= 1

    def visit_SetComp(self, node):
        """Visit set comprehensions."""
        self.current_level += 1

        loop_info = self._analyze_comprehension(node, "set_comp")
        if loop_info:
            self.loops.append(loop_info)

        # Visit nested comprehensions and expressions
        self.generic_visit(node)

        self.current_level -= 1

    def visit_DictComp(self, node):
        """Visit dictionary comprehensions."""
        self.current_level += 1

        loop_info = self._analyze_comprehension(node, "dict_comp")
        if loop_info:
            self.loops.append(loop_info)

        # Visit nested comprehensions and expressions
        self.generic_visit(node)

        self.current_level -= 1

    def visit_GeneratorExp(self, node):
        """Visit generator expressions."""
        self.current_level += 1

        loop_info = self._analyze_comprehension(node, "generator_exp")
        if loop_info:
            self.loops.append(loop_info)

        # Visit nested comprehensions and expressions
        self.generic_visit(node)

        self.current_level -= 1

    def _analyze_comprehension(self, node, comp_type: str) -> Optional[LoopInfo]:
        """Analyze comprehension nodes (ListComp, SetComp, DictComp, GeneratorExp)."""
        try:
            # Comprehensions have generators attribute with comprehension generators
            if not hasattr(node, "generators") or not node.generators:
                return None

            # Analyze the first (outermost) generator
            generator = node.generators[0]

            # Extract loop variable(s)
            variable = self._extract_comprehension_target(generator.target)
            if variable is None:
                return None

            # Try to get string representation of iterable
            try:
                if hasattr(ast, "unparse"):
                    iterable_str = ast.unparse(generator.iter)
                else:
                    iterable_str = _ast_to_string(generator.iter)
            except Exception:
                iterable_str = "unknown"

            # Analyze dependencies in the comprehension
            dep_analyzer = DependencyAnalyzer()
            dep_analyzer.loop_var = variable

            # Visit the comprehension element/key/value expressions
            if comp_type == "dict_comp":
                dep_analyzer.visit(node.key)
                dep_analyzer.visit(node.value)
            else:
                dep_analyzer.visit(node.elt)

            # Visit conditions
            for condition in generator.ifs:
                dep_analyzer.visit(condition)

            # Visit nested generators
            for nested_gen in node.generators[1:]:
                dep_analyzer.visit(nested_gen.iter)
                for condition in nested_gen.ifs:
                    dep_analyzer.visit(condition)

            # Try to extract range information if applicable
            range_info = None
            if isinstance(generator.iter, ast.Call):
                evaluator = SafeRangeEvaluator(self.local_vars)
                evaluator.visit(generator.iter)
                if evaluator.safe and evaluator.result:
                    range_info = evaluator.result

            # Use enhanced dependency analysis
            has_loop_deps = dep_analyzer.has_loop_carried_dependencies()
            final_dependencies = dep_analyzer.reads - {variable}

            # If there are loop-carried dependencies, add them to the dependency set
            if has_loop_deps:
                final_dependencies.update(["loop_carried_dependency"])

            return LoopInfo(
                loop_type=comp_type,
                variable=variable,
                iterable=iterable_str,
                range_info=range_info,
                nested_level=self.current_level - 1,
                dependencies=final_dependencies,
            )

        except Exception as e:
            logger.debug(f"Error analyzing {comp_type}: {e}")
            return None

    def _extract_comprehension_target(self, target) -> Optional[str]:
        """Extract variable name from comprehension target."""
        if isinstance(target, ast.Name):
            return target.id
        elif isinstance(target, ast.Tuple):
            # For tuple unpacking like (a, b), return a representative name
            names = []
            for elt in target.elts:
                if isinstance(elt, ast.Name):
                    names.append(elt.id)
            return f"({', '.join(names)})" if names else None
        else:
            # Complex target, return a generic placeholder
            return "complex_target"

    def _extract_tuple_target_names(self, target) -> str:
        """Extract variable names from tuple unpacking target."""
        names = []
        for elt in target.elts:
            if isinstance(elt, ast.Name):
                names.append(elt.id)
            elif isinstance(elt, ast.Tuple):
                # Handle nested tuples like ((a, b), c)
                nested = self._extract_tuple_target_names(elt)
                names.append(f"({nested})")
            else:
                names.append("_")  # Placeholder for complex elements
        return ", ".join(names)

    def _analyze_for_loop(self, node) -> Optional[LoopInfo]:
        """Analyze a for loop node."""
        try:
            # Handle different target types
            if isinstance(node.target, ast.Name):
                variable = node.target.id
            elif isinstance(node.target, ast.Tuple):
                # Handle tuple unpacking targets like (a, b) in items
                variable = self._extract_tuple_target_names(node.target)
            else:
                # Complex targets like subscripts, attributes, etc.
                variable = "complex_target"
            # Try to get string representation of iterable
            try:
                if hasattr(ast, "unparse"):
                    iterable_str = ast.unparse(node.iter)
                else:
                    # Fallback for older Python versions
                    iterable_str = _ast_to_string(node.iter)
            except Exception:
                iterable_str = "unknown"

            # Analyze dependencies
            dep_analyzer = DependencyAnalyzer()
            dep_analyzer.loop_var = variable
            for stmt in node.body:
                dep_analyzer.visit(stmt)

            # Try to extract range information
            range_info = None
            if isinstance(node.iter, ast.Call):
                evaluator = SafeRangeEvaluator(self.local_vars)
                evaluator.visit(node.iter)
                if evaluator.safe and evaluator.result:
                    range_info = evaluator.result

            # Use enhanced dependency analysis
            has_loop_deps = dep_analyzer.has_loop_carried_dependencies()
            final_dependencies = dep_analyzer.reads - {variable}

            # If there are loop-carried dependencies, add them to the dependency set
            if has_loop_deps:
                final_dependencies.update(["loop_carried_dependency"])

            return LoopInfo(
                loop_type="for",
                variable=variable,
                iterable=iterable_str,
                range_info=range_info,
                nested_level=self.current_level - 1,
                dependencies=final_dependencies,
            )

        except Exception as e:
            logger.debug(f"Error analyzing for loop: {e}")
            return None

    def _analyze_while_loop(self, node) -> Optional[LoopInfo]:
        """Analyze a while loop node."""
        try:
            # Try to get string representation of condition
            try:
                if hasattr(ast, "unparse"):
                    condition_str = ast.unparse(node.test)
                else:
                    condition_str = _ast_to_string(node.test)
            except Exception:
                condition_str = "unknown"

            # Analyze dependencies
            dep_analyzer = DependencyAnalyzer()
            for stmt in node.body:
                dep_analyzer.visit(stmt)

            return LoopInfo(
                loop_type="while",
                iterable=condition_str,  # Store condition as iterable
                nested_level=self.current_level - 1,
                dependencies=dep_analyzer.reads,
            )

        except Exception as e:
            logger.debug(f"Error analyzing while loop: {e}")
            return None


def detect_loops_in_function(
    func: Callable, args: tuple = (), kwargs: Optional[Dict[Any, Any]] = None
) -> List[LoopInfo]:
    """
    Detect and analyze loops in a function.

    Args:
        func: Function to analyze
        args: Function arguments for context
        kwargs: Function keyword arguments for context

    Returns:
        List of LoopInfo objects
    """
    if kwargs is None:
        kwargs = {}

    try:
        source = inspect.getsource(func)
        tree = ast.parse(source)

        # Build local variables context
        local_vars: Dict[str, Any] = {}

        # Add function arguments to context
        try:
            sig = inspect.signature(func)
            bound_args = sig.bind_partial(*args, **kwargs)
            bound_args.apply_defaults()
            local_vars.update(bound_args.arguments)
        except Exception:
            pass

        detector = LoopDetector(local_vars)

        # Use the full visitor pattern to detect all loop constructs including comprehensions
        detector.visit(tree)

        return detector.loops

    except Exception as e:
        logger.debug(f"Loop detection failed for {func.__name__}: {e}")
        return []


def find_parallelizable_loops(
    func: Callable,
    args: tuple = (),
    kwargs: Optional[Dict[Any, Any]] = None,
    max_nesting_level: int = 1,
) -> List[LoopInfo]:
    """
    Find loops that can be parallelized.

    Args:
        func: Function to analyze
        args: Function arguments
        kwargs: Function keyword arguments
        max_nesting_level: Maximum nesting level to consider

    Returns:
        List of parallelizable LoopInfo objects
    """
    all_loops = detect_loops_in_function(func, args, kwargs)

    parallelizable = []
    for loop in all_loops:
        if (
            loop.is_parallelizable
            and loop.nested_level <= max_nesting_level
            and not loop.dependencies
        ):  # No cross-iteration dependencies
            parallelizable.append(loop)

    return parallelizable


def analyze_loop_patterns(
    func: Callable, args: tuple = (), kwargs: Optional[Dict[Any, Any]] = None
) -> Dict[str, Any]:
    """
    Perform comprehensive loop pattern analysis.

    Args:
        func: Function to analyze
        args: Function arguments for context
        kwargs: Function keyword arguments for context

    Returns:
        Dictionary with comprehensive analysis results
    """
    loops = detect_loops_in_function(func, args, kwargs)

    analysis: Dict[str, Any] = {
        "total_loops": len(loops),
        "parallelizable_loops": 0,
        "nested_loops": 0,
        "reduction_opportunities": [],
        "vectorization_candidates": [],
        "parallelization_recommendations": [],
        "performance_estimates": {},
    }

    for loop in loops:
        # Count parallelizable loops
        if loop.is_parallelizable:
            analysis["parallelizable_loops"] += 1

        # Count nested loops
        if loop.nested_level > 0:
            analysis["nested_loops"] += 1

        # Get parallelization strategy
        strategy = loop.suggest_parallelization_strategy()
        benefit = loop.estimate_parallelization_benefit()

        if benefit > 0.3:  # Only recommend if significant benefit
            analysis["parallelization_recommendations"].append(
                {
                    "loop_variable": loop.variable,
                    "iterable": loop.iterable,
                    "strategy": strategy,
                    "benefit_score": benefit,
                    "iteration_count": loop._get_iteration_count(),
                }
            )

        # Check for vectorization opportunities
        if "vectorization" in strategy.get("alternatives", []):
            analysis["vectorization_candidates"].append(
                {
                    "loop_variable": loop.variable,
                    "iterable": loop.iterable,
                    "pattern": "numpy_pandas_operations",
                }
            )

        # Estimate performance improvement
        if loop.is_parallelizable:
            estimated_speedup = min(4.0, benefit * 8.0)  # Cap at 4x speedup
            analysis["performance_estimates"][f"loop_{loop.variable}"] = {
                "estimated_speedup": estimated_speedup,
                "confidence": (
                    "high" if benefit > 0.6 else "medium" if benefit > 0.3 else "low"
                ),
            }

    return analysis


def estimate_work_size(loop_info: LoopInfo) -> int:
    """
    Estimate the amount of work in a loop.

    Args:
        loop_info: Loop information

    Returns:
        Estimated number of iterations
    """
    if loop_info.range_info:
        start = loop_info.range_info["start"]
        stop = loop_info.range_info["stop"]
        step = loop_info.range_info["step"]

        if step > 0 and stop > start:
            return (stop - start + step - 1) // step
        elif step < 0 and stop < start:
            return (start - stop - step - 1) // (-step)
        else:
            return 0

    # For non-range loops, we can't easily estimate
    return 100  # Default estimate


# Backward compatibility
def detect_loops(func: Callable, args: tuple, kwargs: dict) -> Optional[Dict[str, Any]]:
    """
    Legacy function for backward compatibility.

    Args:
        func: Function to analyze
        args: Function arguments
        kwargs: Function keyword arguments

    Returns:
        Dictionary with loop information or None
    """
    loops = detect_loops_in_function(func, args, kwargs)
    if loops:
        # Return the first parallelizable loop as a dictionary
        for loop in loops:
            if loop.is_parallelizable:
                loop_dict = loop.to_dict()
                # Convert range_info to range object for compatibility
                if loop.range_info:
                    range_info = loop.range_info
                    loop_dict["range"] = range(
                        range_info["start"],
                        range_info["stop"],
                        range_info["step"],
                    )
                return loop_dict

    return None


def integrate_with_decorator(
    loops: List[LoopInfo],
    func: Callable,
    decorator_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Integrate loop analysis with @cluster decorator for optimized parallelization.

    Args:
        loops: List of detected LoopInfo objects
        func: Function being analyzed
        decorator_config: Optional decorator configuration

    Returns:
        Dictionary with integration recommendations
    """
    integration: Dict[str, Any] = {
        "recommended_cores": 1,
        "recommended_memory": "1GB",
        "chunk_strategy": "auto",
        "parallelization_approach": "none",
        "estimated_speedup": 1.0,
        "decorator_modifications": [],
        "warnings": [],
    }

    if not loops:
        integration["warnings"].append("No loops detected for parallelization")
        return integration

    # Find the most suitable loop for parallelization
    best_loop = None
    best_benefit = 0.0

    for loop in loops:
        if loop.is_parallelizable:
            benefit = loop.estimate_parallelization_benefit()
            if benefit > best_benefit:
                best_benefit = benefit
                best_loop = loop

    if best_loop is None:
        integration["warnings"].append("No parallelizable loops found")
        return integration

    # Configure based on best loop
    iteration_count = best_loop._get_iteration_count()

    # Recommend cores based on iteration count and loop characteristics
    if iteration_count < 100:
        integration["recommended_cores"] = 2
        integration["parallelization_approach"] = "thread_pool"
    elif iteration_count < 1000:
        integration["recommended_cores"] = 4
        integration["parallelization_approach"] = "process_pool"
    elif iteration_count < 10000:
        integration["recommended_cores"] = 8
        integration["parallelization_approach"] = "process_pool_chunked"
    else:
        integration["recommended_cores"] = 16
        integration["parallelization_approach"] = "distributed"

    # Memory recommendations based on data size
    if best_loop.iterable and any(
        lib in best_loop.iterable.lower() for lib in ["numpy", "pandas"]
    ):
        integration["recommended_memory"] = "4GB"
        integration["decorator_modifications"].append(
            "Consider memory-efficient chunking for large arrays"
        )

    # Chunk strategy
    if iteration_count > 1000:
        chunk_size = max(10, iteration_count // integration["recommended_cores"])
        integration["chunk_strategy"] = f"fixed_size_{chunk_size}"

    # Estimate speedup
    integration["estimated_speedup"] = min(
        integration["recommended_cores"], best_benefit * 10.0
    )

    # Decorator configuration suggestions
    if decorator_config:
        current_cores = decorator_config.get("cores", 1)
        if current_cores < integration["recommended_cores"]:
            integration["decorator_modifications"].append(
                f"Increase cores from {current_cores} to {integration['recommended_cores']}"
            )

    return integration


def validate_analysis_results(
    loops: List[LoopInfo],
    func: Callable,
    validation_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Validate the accuracy of loop analysis results.

    Args:
        loops: List of LoopInfo objects to validate
        func: Function that was analyzed
        validation_config: Optional validation configuration

    Returns:
        Dictionary with validation results
    """
    validation: Dict[str, Any] = {
        "accuracy_score": 0.0,
        "detected_issues": [],
        "false_positives": [],
        "false_negatives": [],
        "recommendations": [],
        "confidence_level": "low",
    }

    try:
        # Re-analyze function for comparison
        source = inspect.getsource(func)
        tree = ast.parse(source)

        # Count actual loops in AST
        actual_for_loops = 0
        actual_while_loops = 0
        actual_comprehensions = 0

        for node in ast.walk(tree):
            if isinstance(node, ast.For):
                actual_for_loops += 1
            elif isinstance(node, ast.While):
                actual_while_loops += 1
            elif isinstance(
                node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
            ):
                actual_comprehensions += 1

        total_actual_loops = actual_for_loops + actual_while_loops
        detected_loops = len(
            [loop for loop in loops if loop.loop_type in ["for", "while"]]
        )

        # Calculate detection accuracy
        if total_actual_loops > 0:
            detection_rate = min(1.0, detected_loops / total_actual_loops)
        else:
            detection_rate = 1.0 if detected_loops == 0 else 0.0

        validation["accuracy_score"] = detection_rate

        # Check for missed comprehensions
        if actual_comprehensions > 0:
            validation["false_negatives"].append(
                f"Detected {actual_comprehensions} comprehensions that weren't analyzed"
            )
            validation["recommendations"].append(
                "Consider extending analysis to include comprehensions"
            )

        # Validate parallelizability assessments
        parallelizable_count = len([loop for loop in loops if loop.is_parallelizable])

        # Heuristic validation based on common patterns
        if parallelizable_count == 0 and total_actual_loops > 0:
            validation["recommendations"].append(
                "Review parallelizability assessment - some loops might be suitable"
            )

        if parallelizable_count > total_actual_loops:
            validation["false_positives"].append(
                "More parallelizable loops detected than actual loops"
            )

        # Check for range loops specifically
        range_loops = [loop for loop in loops if loop.range_info is not None]
        if len(range_loops) == 0 and actual_for_loops > 0:
            validation["recommendations"].append(
                "No range loops detected - verify range detection accuracy"
            )

        # Confidence assessment
        if validation["accuracy_score"] >= 0.9:
            validation["confidence_level"] = "high"
        elif validation["accuracy_score"] >= 0.7:
            validation["confidence_level"] = "medium"
        else:
            validation["confidence_level"] = "low"
            validation["recommendations"].append(
                "Low accuracy detected - consider improving loop detection algorithms"
            )

        # Additional validation checks
        for loop in loops:
            # Validate iteration count estimates
            if loop.range_info:
                try:
                    actual_range = range(
                        loop.range_info["start"],
                        loop.range_info["stop"],
                        loop.range_info["step"],
                    )
                    actual_count = len(actual_range)
                    estimated_count = loop._get_iteration_count()

                    if abs(actual_count - estimated_count) > 1:
                        validation["detected_issues"].append(
                            f"Iteration count mismatch for {loop.variable}: "
                            f"actual={actual_count}, estimated={estimated_count}"
                        )
                except (ValueError, TypeError):
                    validation["detected_issues"].append(
                        f"Invalid range parameters for loop {loop.variable}"
                    )

    except Exception as e:
        validation["detected_issues"].append(f"Validation error: {str(e)}")
        validation["confidence_level"] = "error"

    return validation
