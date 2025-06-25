"""Enhanced loop detection and analysis for parallel execution."""

import ast
import inspect
from typing import Any, Dict, List, Optional, Callable, Union, Set
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
        # Basic heuristics for parallelizability
        if self.loop_type == "while":
            return False  # While loops are harder to parallelize

        if self.range_info:
            # Range-based loops are good candidates
            return True

        # TODO: Add more sophisticated analysis
        return False

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

    def __init__(self, local_vars: Dict[str, Any] = None):
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
                    self.result = {"start": args[0], "stop": args[1], "step": 1}
                elif len(args) == 3:
                    self.result = {"start": args[0], "stop": args[1], "step": args[2]}
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

    def visit_Name(self, node):
        """Visit variable names."""
        if isinstance(node.ctx, ast.Load):
            self.reads.add(node.id)
        elif isinstance(node.ctx, ast.Store):
            self.writes.add(node.id)
        self.generic_visit(node)

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


class LoopDetector(ast.NodeVisitor):
    """Enhanced loop detection with dependency analysis."""

    def __init__(self, local_vars: Dict[str, Any] = None):
        self.loops = []
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
            except:
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

            return LoopInfo(
                loop_type="for",
                variable=variable,
                iterable=iterable_str,
                range_info=range_info,
                nested_level=self.current_level - 1,
                dependencies=dep_analyzer.reads - {variable},
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
            except:
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
    func: Callable, args: tuple = (), kwargs: dict = None
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
        local_vars = {}

        # Add function arguments to context
        try:
            sig = inspect.signature(func)
            bound_args = sig.bind_partial(*args, **kwargs)
            bound_args.apply_defaults()
            local_vars.update(bound_args.arguments)
        except:
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
    func: Callable, args: tuple = (), kwargs: dict = None, max_nesting_level: int = 1
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
                        range_info["start"], range_info["stop"], range_info["step"]
                    )
                return loop_dict

    return None
