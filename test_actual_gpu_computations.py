#!/usr/bin/env python3
"""
Test actual GPU computations and verify we get correct mathematical results back.
"""

import sys
import os
import math
sys.path.insert(0, '/Users/jmanning/clustrix')

from clustrix.config import load_config, configure, get_config
from clustrix import cluster
from tests.real_world import TestCredentials

def test_actual_gpu_computation():
    """Test real GPU computation with verifiable mathematical results."""
    print("🧪 Testing actual GPU computation with mathematical verification...")
    
    # Load config and credentials
    load_config('tensor01_config.yml')
    creds = TestCredentials()
    tensor01_creds = creds.get_tensor01_credentials()
    
    # Configure for reliable two-venv setup (no fallback)
    configure(
        cluster_type="ssh",
        cluster_host=tensor01_creds['host'],
        username=tensor01_creds['username'],
        password=tensor01_creds['password'],
        cleanup_on_success=False,  # Keep files for debugging if needed
        use_two_venv=True,         # Must use two-venv (no fallback for bread-and-butter)
        venv_setup_timeout=600,    # 10 minutes - generous timeout but should not fallback
        auto_gpu_parallel=False,   # Test single GPU execution first
        auto_parallel=False,
    )
    
    @cluster(cores=2, memory='8GB', time='00:10:00')
    def matrix_operations_gpu():
        """Perform actual GPU matrix operations with verifiable results."""
        import torch
        import math
        
        # Verify CUDA is available
        if not torch.cuda.is_available():
            return {"error": "CUDA not available", "success": False}
        
        # Get GPU count
        gpu_count = torch.cuda.device_count()
        
        results = []
        
        # Test each GPU with deterministic computations
        for gpu_id in range(min(gpu_count, 4)):  # Test first 4 GPUs
            device = torch.device(f'cuda:{gpu_id}')
            
            # Create deterministic matrix based on GPU ID
            torch.manual_seed(42 + gpu_id)  # Deterministic seed
            
            # Create a specific matrix with known mathematical properties
            size = 100
            A = torch.randn(size, size, device=device)
            
            # Perform operations with verifiable results
            # 1. Matrix multiplication
            B = torch.mm(A, A.t())  # A @ A^T (always positive semi-definite)
            
            # 2. Eigenvalue decomposition (should be real and non-negative)
            eigenvals, eigenvecs = torch.linalg.eigh(B)
            min_eigenval = eigenvals.min().item()
            max_eigenval = eigenvals.max().item()
            
            # 3. Trace (sum of diagonal) - should equal sum of eigenvalues
            trace_direct = torch.trace(B).item()
            trace_from_eigenvals = eigenvals.sum().item()
            
            # 4. Frobenius norm
            frobenius_norm = torch.norm(B, 'fro').item()
            
            # 5. Determinant
            det = torch.det(B).item()
            
            # Verify mathematical properties
            eigenval_trace_diff = abs(trace_direct - trace_from_eigenvals)
            
            results.append({
                "gpu_id": gpu_id,
                "device": str(device),
                "matrix_size": size,
                "seed_used": 42 + gpu_id,
                "min_eigenval": min_eigenval,
                "max_eigenval": max_eigenval,
                "trace_direct": trace_direct,
                "trace_from_eigenvals": trace_from_eigenvals,
                "eigenval_trace_diff": eigenval_trace_diff,
                "frobenius_norm": frobenius_norm,
                "determinant": det,
                "eigenvals_all_nonneg": bool(min_eigenval >= -1e-6),  # Allow small numerical error
                "trace_consistency": bool(eigenval_trace_diff < 1e-6),
            })
        
        return {
            "success": True,
            "gpu_count": gpu_count,
            "results": results,
            "computation_type": "matrix_eigenvalue_analysis"
        }
    
    try:
        print("Executing GPU computation job...")
        result = matrix_operations_gpu()
        
        if not result.get("success", False):
            print(f"❌ GPU computation failed: {result.get('error', 'Unknown error')}")
            return False
        
        print(f"✅ GPU computation successful!")
        print(f"   GPU count: {result['gpu_count']}")
        print(f"   Tested GPUs: {len(result['results'])}")
        
        # Verify mathematical correctness of results
        all_math_correct = True
        for i, res in enumerate(result['results']):
            gpu_id = res['gpu_id']
            print(f"\n   GPU {gpu_id} Results:")
            print(f"     Matrix size: {res['matrix_size']}x{res['matrix_size']}")
            print(f"     Eigenvalue range: [{res['min_eigenval']:.6f}, {res['max_eigenval']:.6f}]")
            print(f"     Trace (direct): {res['trace_direct']:.6f}")
            print(f"     Trace (eigenvals): {res['trace_from_eigenvals']:.6f}")
            print(f"     Trace difference: {res['eigenval_trace_diff']:.2e}")
            print(f"     Frobenius norm: {res['frobenius_norm']:.6f}")
            print(f"     Determinant: {res['determinant']:.6e}")
            
            # Verify mathematical properties
            if not res['eigenvals_all_nonneg']:
                print(f"     ❌ Non-positive eigenvalue detected: {res['min_eigenval']}")
                all_math_correct = False
            else:
                print(f"     ✅ All eigenvalues non-negative (as expected for A@A^T)")
            
            if not res['trace_consistency']:
                print(f"     ❌ Trace inconsistency: {res['eigenval_trace_diff']}")
                all_math_correct = False
            else:
                print(f"     ✅ Trace consistency verified")
            
            # Additional sanity checks
            if math.isnan(res['determinant']) or math.isinf(res['determinant']):
                print(f"     ❌ Invalid determinant: {res['determinant']}")
                all_math_correct = False
            
            if math.isnan(res['frobenius_norm']) or res['frobenius_norm'] <= 0:
                print(f"     ❌ Invalid Frobenius norm: {res['frobenius_norm']}")
                all_math_correct = False
        
        if all_math_correct:
            print(f"\n✅ All mathematical properties verified correctly!")
            print(f"✅ GPU computation test PASSED")
            return True
        else:
            print(f"\n❌ Some mathematical properties failed verification")
            return False
            
    except Exception as e:
        print(f"❌ GPU computation job failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_gpu_parallel_computation():
    """Test automatic GPU parallelization with real computation."""
    print("\n🧪 Testing automatic GPU parallelization with real computation...")
    
    # Load config and credentials
    load_config('tensor01_config.yml')
    creds = TestCredentials()
    tensor01_creds = creds.get_tensor01_credentials()
    
    # Configure for GPU parallelization
    configure(
        cluster_type="ssh",
        cluster_host=tensor01_creds['host'],
        username=tensor01_creds['username'],
        password=tensor01_creds['password'],
        cleanup_on_success=False,
        use_two_venv=True,
        venv_setup_timeout=600,
        auto_gpu_parallel=True,    # Enable automatic GPU parallelization
        auto_parallel=True,
    )
    
    @cluster(cores=4, memory='16GB', time='00:15:00', auto_gpu_parallel=True)
    def parallel_gpu_computation():
        """GPU computation that should be automatically parallelized."""
        import torch
        import math
        
        if not torch.cuda.is_available():
            return {"error": "CUDA not available", "success": False}
        
        results = []
        
        # This loop should be detected and parallelized across GPUs
        for task_id in range(12):  # More tasks than GPUs to test distribution
            # Create task-specific computation
            torch.manual_seed(1000 + task_id)  # Deterministic per task
            
            # Use a different GPU computation pattern
            device = torch.device(f'cuda:{task_id % torch.cuda.device_count()}')
            
            # Create a specific mathematical problem
            n = 50 + (task_id % 5) * 10  # Varying matrix sizes
            A = torch.randn(n, n, device=device)
            
            # Perform SVD decomposition
            U, S, Vh = torch.linalg.svd(A)
            
            # Verify SVD properties: A = U @ diag(S) @ Vh
            A_reconstructed = U @ torch.diag_embed(S) @ Vh
            reconstruction_error = torch.norm(A - A_reconstructed, 'fro').item()
            
            # Compute other properties
            singular_values_sum = S.sum().item()
            condition_number = (S.max() / S.min()).item()
            rank = torch.linalg.matrix_rank(A).item()
            
            results.append({
                "task_id": task_id,
                "matrix_size": n,
                "device_used": str(device),
                "seed": 1000 + task_id,
                "singular_values_sum": singular_values_sum,
                "condition_number": condition_number,
                "rank": rank,
                "reconstruction_error": reconstruction_error,
                "svd_accurate": reconstruction_error < 1e-4,
            })
        
        return {
            "success": True,
            "results": results,
            "parallelized": True,
            "computation_type": "svd_analysis"
        }
    
    try:
        print("Executing parallel GPU computation job...")
        result = parallel_gpu_computation()
        
        if not result.get("success", False):
            print(f"❌ Parallel GPU computation failed: {result.get('error', 'Unknown error')}")
            return False
        
        print(f"✅ Parallel GPU computation successful!")
        print(f"   Tasks completed: {len(result['results'])}")
        
        # Verify all computations were accurate
        all_accurate = True
        device_usage = {}
        
        for res in result['results']:
            task_id = res['task_id']
            device = res['device_used']
            
            # Track device usage
            device_usage[device] = device_usage.get(device, 0) + 1
            
            print(f"   Task {task_id}: {res['matrix_size']}x{res['matrix_size']} on {device}")
            print(f"     SVD reconstruction error: {res['reconstruction_error']:.2e}")
            print(f"     Condition number: {res['condition_number']:.2f}")
            print(f"     Rank: {res['rank']}")
            
            if not res['svd_accurate']:
                print(f"     ❌ SVD reconstruction inaccurate")
                all_accurate = False
            else:
                print(f"     ✅ SVD accurate")
        
        print(f"\n   Device usage distribution:")
        for device, count in device_usage.items():
            print(f"     {device}: {count} tasks")
        
        # Verify parallelization happened (multiple devices used)
        if len(device_usage) > 1:
            print(f"   ✅ Parallelization confirmed: {len(device_usage)} devices used")
        else:
            print(f"   ⚠️  Only one device used: {list(device_usage.keys())}")
        
        if all_accurate:
            print(f"\n✅ All parallel GPU computations accurate!")
            print(f"✅ GPU parallelization test PASSED")
            return True
        else:
            print(f"\n❌ Some parallel computations were inaccurate")
            return False
            
    except Exception as e:
        print(f"❌ Parallel GPU computation job failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("🚀 Testing actual GPU computations...")
    
    # Test 1: Single GPU with mathematical verification
    single_gpu_works = test_actual_gpu_computation()
    
    if single_gpu_works:
        print("\n" + "="*80)
        # Test 2: Parallel GPU computation
        parallel_gpu_works = test_gpu_parallel_computation()
        
        if parallel_gpu_works:
            print("\n🎉 All GPU computation tests PASSED!")
            print("✅ GPU jobs execute correctly with verified mathematical results")
        else:
            print("\n❌ Parallel GPU computation test failed")
    else:
        print("\n❌ Single GPU computation test failed")