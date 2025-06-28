"""Enhanced loop detection and analysis for parallel execution."""

import ast
import inspect
from typing import Any, Dict, List, Optional, Callable, Set
import logging

logger = logging.getLogger(__name__)


def _ast_to_string(node) -> str:
    """Convert AST node to string for Python < 3.9."""
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            args = [_ast_to_string(arg) for arg in node.args]
            return f"{node.func.id}({', '.join(args)})"
        return "call"
    elif isinstance(node, ast.Constant):
        return str(node.value)
    elif isinstance(node, ast.Num):  # Python < 3.8
        return str(node.n)
    else:
        return str(type(node).__name__)


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
        """Safely evaluate an AST node to get integer value."""
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, int) else None
        elif isinstance(node, ast.Num):  # Python < 3.8
            return node.n if isinstance(node.n, int) else None
        elif isinstance(node, ast.Name):
            return (
                self.local_vars.get(node.id)
                if isinstance(self.local_vars.get(node.id), int)
                else None
            )
        elif isinstance(node, ast.BinOp):
            return self._evaluate_binop(node)
        else:
            return None

    def _evaluate_binop(self, node) -> Optional[int]:
        """Evaluate binary operations."""
        left = self._evaluate_node(node.left)
        right = self._evaluate_node(node.right)

        if left is None or right is None:
            return None

        try:
            if isinstance(node.op, ast.Add):
                return left + right
            elif isinstance(node.op, ast.Sub):
                return left - right
            elif isinstance(node.op, ast.Mult):
                return left * right
            elif isinstance(node.op, ast.FloorDiv):
                return left // right if right != 0 else None
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
        """Visit variable names."""
        if isinstance(node.ctx, ast.Load):
            self.reads.add(node.id)
        elif isinstance(node.ctx, ast.Store):
            self.writes.add(node.id)
        self.generic_visit(node)

    def visit_Subscript(self, node):
        """Visit array/list subscript operations."""
        if isinstance(node.value, ast.Name):
            # Track array accesses
            if isinstance(node.ctx, ast.Load):
                self.array_accesses.add(f"{node.value.id}[read]")
            elif isinstance(node.ctx, ast.Store):
                self.array_accesses.add(f"{node.value.id}[write]")
        self.generic_visit(node)

    def visit_Call(self, node):
        """Visit function calls."""
        if isinstance(node.func, ast.Name):
            self.function_calls.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            # Method calls like obj.method()
            if isinstance(node.func.value, ast.Name):
                self.function_calls.add(f"{node.func.value.id}.{node.func.attr}")
        self.generic_visit(node)

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

    def _analyze_for_loop(self, node) -> Optional[LoopInfo]:
        """Analyze a for loop node."""
        try:
            if not isinstance(node.target, ast.Name):
                return None  # Complex targets not supported yet

            variable = node.target.id
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

        # Visit all nodes, not just the root
        for node in ast.walk(tree):
            if isinstance(node, (ast.For, ast.While)):
                if isinstance(node, ast.For):
                    loop_info = detector._analyze_for_loop(node)
                else:
                    loop_info = detector._analyze_while_loop(node)
                if loop_info:
                    detector.loops.append(loop_info)

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
