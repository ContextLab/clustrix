"""
GPU parallelization utilities for ClustriX.

This module provides automatic GPU detection and parallelization capabilities
for seamless multi-GPU usage without requiring manual GPU configuration.
"""

import ast
import inspect
import subprocess
from typing import Any, Dict, List, Optional, Callable
import logging

logger = logging.getLogger(__name__)


def detect_gpu_availability() -> Dict[str, Any]:
    """
    Detect available GPUs in the current environment.

    Returns:
        Dictionary with GPU information including count, names, and memory
    """
    gpu_info: Dict[str, Any] = {
        "available": False,
        "count": 0,
        "device_names": [],
        "memory_per_device": [],
        "total_memory": 0,
        "cuda_version": None,
        "driver_version": None,
    }

    try:
        # Check PyTorch CUDA availability
        pytorch_check = subprocess.run(
            [
                "python",
                "-c",
                """
import torch
print(f'CUDA_AVAILABLE:{torch.cuda.is_available()}')
print(f'DEVICE_COUNT:{torch.cuda.device_count()}')
if torch.cuda.is_available():
    for i in range(torch.cuda.device_count()):
        props = torch.cuda.get_device_properties(i)
        print(f'DEVICE_{i}_NAME:{props.name}')
        print(f'DEVICE_{i}_MEMORY:{props.total_memory}')
    print(f'CUDA_VERSION:{torch.version.cuda}')
""",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=30,
        )

        if pytorch_check.returncode == 0:
            lines = pytorch_check.stdout.strip().split("\n")
            for line in lines:
                if line.startswith("CUDA_AVAILABLE:"):
                    gpu_info["available"] = line.split(":", 1)[1] == "True"
                elif line.startswith("DEVICE_COUNT:"):
                    gpu_info["count"] = int(line.split(":", 1)[1])
                elif line.startswith("DEVICE_") and "_NAME:" in line:
                    device_name = line.split(":", 1)[1]
                    gpu_info["device_names"].append(device_name)
                elif line.startswith("DEVICE_") and "_MEMORY:" in line:
                    memory = int(line.split(":", 1)[1])
                    gpu_info["memory_per_device"].append(memory)
                    gpu_info["total_memory"] += memory
                elif line.startswith("CUDA_VERSION:"):
                    gpu_info["cuda_version"] = line.split(":", 1)[1]

        # Try to get driver version from nvidia-smi
        nvidia_smi = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=driver_version",
                "--format=csv,noheader,nounits",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=10,
        )

        if nvidia_smi.returncode == 0:
            driver_versions = nvidia_smi.stdout.strip().split("\n")
            if driver_versions and driver_versions[0]:
                gpu_info["driver_version"] = driver_versions[0]

    except Exception as e:
        logger.warning(f"GPU detection failed: {e}")

    return gpu_info


def detect_gpu_parallelizable_operations(
    func: Callable, args: tuple, kwargs: dict
) -> List[Dict[str, Any]]:
    """
    Detect operations in a function that can be parallelized across GPUs.

    Args:
        func: Function to analyze
        args: Function arguments
        kwargs: Function keyword arguments

    Returns:
        List of parallelizable operations with metadata
    """
    parallelizable_ops = []

    try:
        source = inspect.getsource(func)
        # Remove leading whitespace to avoid indentation issues
        import textwrap

        source = textwrap.dedent(source)
        tree = ast.parse(source)

        class GPUOpVisitor(ast.NodeVisitor):
            def __init__(self):
                self.operations = []
                self.current_loop = None

            def visit_For(self, node):
                """Detect for loops that might benefit from GPU parallelization."""
                if isinstance(node.target, ast.Name):
                    loop_var = node.target.id

                    # Analyze loop body for GPU operations
                    gpu_ops_in_loop = []
                    for stmt in ast.walk(node):
                        if isinstance(stmt, ast.Call):
                            call_info = self._analyze_call(stmt)
                            if call_info and call_info.get("gpu_compatible"):
                                gpu_ops_in_loop.append(call_info)

                    if gpu_ops_in_loop:
                        loop_info = {
                            "type": "for_loop",
                            "variable": loop_var,
                            "iterable": self._get_iterable_info(node.iter),
                            "gpu_operations": gpu_ops_in_loop,
                            "parallelizable": True,
                            "estimated_benefit": self._estimate_gpu_benefit(
                                gpu_ops_in_loop
                            ),
                        }
                        self.operations.append(loop_info)

                self.generic_visit(node)

            def visit_ListComp(self, node):
                """Detect list comprehensions that might benefit from GPU parallelization."""
                # Analyze comprehension for GPU operations
                gpu_ops = []
                for stmt in ast.walk(node.elt):
                    if isinstance(stmt, ast.Call):
                        call_info = self._analyze_call(stmt)
                        if call_info and call_info.get("gpu_compatible"):
                            gpu_ops.append(call_info)

                if gpu_ops and node.generators:
                    gen = node.generators[0]  # Focus on first generator
                    if isinstance(gen.target, ast.Name):
                        comp_info = {
                            "type": "list_comprehension",
                            "variable": gen.target.id,
                            "iterable": self._get_iterable_info(gen.iter),
                            "gpu_operations": gpu_ops,
                            "parallelizable": True,
                            "estimated_benefit": self._estimate_gpu_benefit(gpu_ops),
                        }
                        self.operations.append(comp_info)

                self.generic_visit(node)

            def _analyze_call(self, call_node: ast.Call) -> Optional[Dict[str, Any]]:
                """Analyze a function call to determine if it's GPU-compatible."""
                if isinstance(call_node.func, ast.Attribute):
                    # Method calls like tensor.cuda(), torch.mm(), etc.
                    if hasattr(call_node.func, "attr"):
                        method_name = call_node.func.attr
                        if method_name in [
                            "cuda",
                            "to",
                            "mm",
                            "matmul",
                            "add",
                            "mul",
                            "conv2d",
                        ]:
                            return {
                                "type": "method_call",
                                "method": method_name,
                                "gpu_compatible": True,
                                "operation_type": "tensor_operation",
                            }
                elif isinstance(call_node.func, ast.Name):
                    # Function calls
                    func_name = call_node.func.id
                    if func_name in ["torch", "F"]:  # Common PyTorch functions
                        return {
                            "type": "function_call",
                            "function": func_name,
                            "gpu_compatible": True,
                            "operation_type": "torch_function",
                        }

                return None

            def _get_iterable_info(self, iter_node: ast.AST) -> Dict[str, Any]:
                """Extract information about loop iterable."""
                if isinstance(iter_node, ast.Call) and isinstance(
                    iter_node.func, ast.Name
                ):
                    if iter_node.func.id == "range":
                        # Extract range parameters
                        args = iter_node.args
                        if len(args) == 1:
                            return {
                                "type": "range",
                                "start": 0,
                                "stop": "dynamic",
                                "step": 1,
                            }
                        elif len(args) == 2:
                            return {
                                "type": "range",
                                "start": "dynamic",
                                "stop": "dynamic",
                                "step": 1,
                            }
                        elif len(args) == 3:
                            return {
                                "type": "range",
                                "start": "dynamic",
                                "stop": "dynamic",
                                "step": "dynamic",
                            }

                return {"type": "unknown", "analyzable": False}

            def _estimate_gpu_benefit(self, gpu_ops: List[Dict[str, Any]]) -> str:
                """Estimate potential benefit from GPU parallelization."""
                if len(gpu_ops) >= 3:
                    return "high"
                elif len(gpu_ops) >= 1:
                    return "medium"
                else:
                    return "low"

        visitor = GPUOpVisitor()
        visitor.visit(tree)
        parallelizable_ops = visitor.operations

    except Exception as e:
        logger.warning(f"GPU operation analysis failed: {e}")

    return parallelizable_ops


def create_gpu_parallel_execution_plan(
    func: Callable,
    args: tuple,
    kwargs: dict,
    gpu_info: Dict[str, Any],
    parallelizable_ops: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Create an execution plan for GPU parallelization.

    Args:
        func: Function to parallelize
        args: Function arguments
        kwargs: Function keyword arguments
        gpu_info: Available GPU information
        parallelizable_ops: Detected parallelizable operations

    Returns:
        Execution plan or None if parallelization not beneficial
    """
    if not gpu_info["available"] or gpu_info["count"] < 2:
        return None

    if not parallelizable_ops:
        return None

    # Select the best operation to parallelize
    best_op = max(
        parallelizable_ops,
        key=lambda op: {"high": 3, "medium": 2, "low": 1}.get(
            op["estimated_benefit"], 0
        ),
    )

    if best_op["estimated_benefit"] == "low":
        return None

    # Create execution plan
    plan = {
        "strategy": "data_parallel",
        "target_operation": best_op,
        "gpu_count": gpu_info["count"],
        "chunk_strategy": "even_split",
        "memory_per_gpu": (
            gpu_info["memory_per_device"][0] if gpu_info["memory_per_device"] else None
        ),
        "device_assignments": list(range(gpu_info["count"])),
        "synchronization_points": ["before_combine"],
        "result_combination": (
            "concatenate" if best_op["type"] == "list_comprehension" else "sum"
        ),
    }

    return plan


def generate_gpu_parallel_code(
    original_func: Callable, execution_plan: Dict[str, Any]
) -> str:
    """
    Generate GPU-parallelized code based on execution plan.

    Args:
        original_func: Original function to parallelize
        execution_plan: GPU parallelization plan

    Returns:
        Python code string for GPU-parallelized execution
    """
    target_op = execution_plan["target_operation"]
    gpu_count = execution_plan["gpu_count"]

    if target_op["type"] == "for_loop":
        return _generate_for_loop_gpu_code(original_func, target_op, gpu_count)
    elif target_op["type"] == "list_comprehension":
        return _generate_list_comp_gpu_code(original_func, target_op, gpu_count)
    else:
        raise ValueError(f"Unsupported operation type: {target_op['type']}")


def _generate_for_loop_gpu_code(
    func: Callable, loop_info: Dict[str, Any], gpu_count: int
) -> str:
    """Generate GPU-parallelized code for for loops."""
    loop_var = loop_info["variable"]

    return f"""
import torch
import torch.multiprocessing as mp
from concurrent.futures import ThreadPoolExecutor
import os

def gpu_parallel_execution():
    # Detect available GPUs
    available_gpus = torch.cuda.device_count()
    gpu_count = min({gpu_count}, available_gpus)

    if gpu_count <= 1:
        # Fallback to original execution
        return original_function()

    # Set up GPU devices
    devices = [f'cuda:{{i}}' for i in range(gpu_count)]

    # Split work across GPUs
    total_iterations = len(range_data)  # This needs to be dynamically determined
    chunk_size = max(1, total_iterations // gpu_count)

    def process_chunk(device_id, start_idx, end_idx):
        torch.cuda.set_device(device_id)
        device = torch.device(f'cuda:{{device_id}}')

        results = []
        for {loop_var} in range(start_idx, end_idx):
            # Original loop body here, with tensors moved to device
            result = original_loop_body({loop_var}, device)
            results.append(result)

        return results

    # Execute on multiple GPUs
    with ThreadPoolExecutor(max_workers=gpu_count) as executor:
        futures = []
        for i in range(gpu_count):
            start_idx = i * chunk_size
            end_idx = min((i + 1) * chunk_size, total_iterations)
            if start_idx < end_idx:
                future = executor.submit(process_chunk, i, start_idx, end_idx)
                futures.append(future)

        # Collect results
        all_results = []
        for future in futures:
            chunk_results = future.result()
            all_results.extend(chunk_results)

    return all_results

# Execute GPU parallel version
result = gpu_parallel_execution()
"""


def _generate_list_comp_gpu_code(
    func: Callable, comp_info: Dict[str, Any], gpu_count: int
) -> str:
    """Generate GPU-parallelized code for list comprehensions."""
    comp_var = comp_info["variable"]

    return f"""
import torch
import torch.multiprocessing as mp
from concurrent.futures import ThreadPoolExecutor

def gpu_parallel_list_comp():
    # Detect available GPUs
    available_gpus = torch.cuda.device_count()
    gpu_count = min({gpu_count}, available_gpus)

    if gpu_count <= 1:
        # Fallback to original execution
        return original_function()

    # Set up for parallel execution
    input_data = list(iterable_data)  # Convert to list for splitting
    chunk_size = max(1, len(input_data) // gpu_count)

    def process_chunk(device_id, data_chunk):
        torch.cuda.set_device(device_id)
        device = torch.device(f'cuda:{{device_id}}')

        results = []
        for {comp_var} in data_chunk:
            # Original comprehension expression here
            result = original_expression({comp_var}, device)
            results.append(result)

        return results

    # Split data and execute
    with ThreadPoolExecutor(max_workers=gpu_count) as executor:
        futures = []
        for i in range(gpu_count):
            start_idx = i * chunk_size
            end_idx = min((i + 1) * chunk_size, len(input_data))
            if start_idx < end_idx:
                chunk = input_data[start_idx:end_idx]
                future = executor.submit(process_chunk, i, chunk)
                futures.append(future)

        # Collect and combine results
        all_results = []
        for future in futures:
            chunk_results = future.result()
            all_results.extend(chunk_results)

    return all_results

# Execute GPU parallel version
result = gpu_parallel_list_comp()
"""


def validate_gpu_parallel_result(
    original_result: Any, parallel_result: Any, tolerance: float = 1e-6
) -> Dict[str, Any]:
    """
    Validate that GPU parallel execution produces correct results.

    Args:
        original_result: Result from sequential execution
        parallel_result: Result from GPU parallel execution
        tolerance: Numerical tolerance for floating point comparisons

    Returns:
        Validation report with correctness information
    """
    validation: Dict[str, Any] = {
        "correct": False,
        "type_match": False,
        "shape_match": False,
        "value_match": False,
        "max_difference": None,
        "mean_difference": None,
        "details": [],
    }

    try:
        # Check type compatibility
        if type(original_result) is type(parallel_result):
            validation["type_match"] = True
        else:
            validation["details"].append(
                f"Type mismatch: {type(original_result)} vs {type(parallel_result)}"
            )

        # Handle different result types
        if isinstance(original_result, (list, tuple)):
            validation.update(
                _validate_sequence_results(original_result, parallel_result, tolerance)
            )
        elif hasattr(original_result, "shape"):  # NumPy/PyTorch tensors
            validation.update(
                _validate_tensor_results(original_result, parallel_result, tolerance)
            )
        elif isinstance(original_result, (int, float, complex)):
            validation.update(
                _validate_numeric_results(original_result, parallel_result, tolerance)
            )
        else:
            # Generic equality check
            validation["value_match"] = original_result == parallel_result
            if not validation["value_match"]:
                validation["details"].append("Generic equality check failed")

        # Overall correctness
        validation["correct"] = (
            validation["type_match"]
            and validation.get("shape_match", True)
            and validation["value_match"]
        )

    except Exception as e:
        validation["details"].append(f"Validation error: {str(e)}")

    return validation


def _validate_sequence_results(
    orig: Any, parallel: Any, tolerance: float
) -> Dict[str, Any]:
    """Validate sequence (list/tuple) results."""
    result: Dict[str, Any] = {"shape_match": False, "value_match": False, "details": []}

    if len(orig) != len(parallel):
        result["details"].append(f"Length mismatch: {len(orig)} vs {len(parallel)}")
        return result

    result["shape_match"] = True

    # Check element-wise equality
    mismatches = 0
    max_diff = 0.0
    total_diff = 0.0

    for i, (o_item, p_item) in enumerate(zip(orig, parallel)):
        if hasattr(o_item, "__sub__") and hasattr(p_item, "__sub__"):
            try:
                diff = abs(o_item - p_item)
                if isinstance(diff, (int, float)):
                    max_diff = max(max_diff, float(diff))
                    total_diff += float(diff)
                    if diff > tolerance:
                        mismatches += 1
            except Exception:
                if o_item != p_item:
                    mismatches += 1
        else:
            if o_item != p_item:
                mismatches += 1

    result["max_difference"] = max_diff
    result["mean_difference"] = total_diff / len(orig) if orig else 0
    result["value_match"] = mismatches == 0

    if mismatches > 0:
        result["details"].append(f"{mismatches} element mismatches out of {len(orig)}")

    return result


def _validate_tensor_results(
    orig: Any, parallel: Any, tolerance: float
) -> Dict[str, Any]:
    """Validate tensor results."""
    result: Dict[str, Any] = {"shape_match": False, "value_match": False, "details": []}

    try:
        if hasattr(orig, "shape") and hasattr(parallel, "shape"):
            if orig.shape != parallel.shape:
                result["details"].append(
                    f"Shape mismatch: {orig.shape} vs {parallel.shape}"
                )
                return result

            result["shape_match"] = True

            # Compute differences
            if hasattr(orig, "cpu"):  # PyTorch tensor
                orig_cpu = orig.cpu()
                parallel_cpu = parallel.cpu()
            else:
                orig_cpu = orig
                parallel_cpu = parallel

            diff = abs(orig_cpu - parallel_cpu)
            if hasattr(diff, "max"):
                max_diff = float(diff.max())
                mean_diff = float(diff.mean())
            else:
                max_diff = float(diff)
                mean_diff = float(diff)

            result["max_difference"] = max_diff
            result["mean_difference"] = mean_diff
            result["value_match"] = max_diff <= tolerance

            if max_diff > tolerance:
                result["details"].append(
                    f"Max difference {max_diff} exceeds tolerance {tolerance}"
                )

    except Exception as e:
        result["details"].append(f"Tensor validation error: {str(e)}")

    return result


def _validate_numeric_results(
    orig: Any, parallel: Any, tolerance: float
) -> Dict[str, Any]:
    """Validate numeric results."""
    result: Dict[str, Any] = {"value_match": False, "details": []}

    try:
        diff = abs(orig - parallel)
        result["max_difference"] = diff
        result["mean_difference"] = diff
        result["value_match"] = diff <= tolerance

        if diff > tolerance:
            result["details"].append(f"Difference {diff} exceeds tolerance {tolerance}")

    except Exception as e:
        result["details"].append(f"Numeric validation error: {str(e)}")

    return result
