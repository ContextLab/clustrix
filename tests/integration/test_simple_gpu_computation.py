#!/usr/bin/env python3
"""
Test simple GPU computation to isolate the issue.
"""


from clustrix.config import load_config, configure, get_config
from clustrix import cluster
from tests.real_world import TestCredentials

@cluster(cores=1, memory='4GB', time='00:05:00')
def simple_gpu_matrix_mult():
    """Simple GPU matrix multiplication."""
    import torch
    
    # Create tensors on GPU 0
    device = torch.device('cuda:0')
    a = torch.randn(100, 100, device=device)
    b = torch.randn(100, 100, device=device)
    
    # Simple matrix multiplication
    c = torch.mm(a, b)
    
    # Return basic statistics
    return {
        "success": True,
        "device": str(device),
        "result_shape": list(c.shape),
        "result_mean": c.mean().item(),
        "result_std": c.std().item(),
        "cuda_available": torch.cuda.is_available(),
        "gpu_count": torch.cuda.device_count()
    }

def test_simple_gpu_computation():
    """Test simple GPU computation without complex operations."""
    print("🧪 Testing simple GPU computation...")
    
    # Load config and credentials
    load_config('tensor01_config.yml')
    creds = TestCredentials()
    tensor01_creds = creds.get_tensor01_credentials()
    
    configure(
        cluster_type="ssh",
        cluster_host=tensor01_creds['host'],
        username=tensor01_creds['username'],
        password=tensor01_creds['password'],
        cleanup_on_success=False,
        use_two_venv=True,
        venv_setup_timeout=600,
        auto_gpu_parallel=False,
        auto_parallel=False,
    )
    
    try:
        print("Executing simple GPU matrix multiplication...")
        result = simple_gpu_matrix_mult()
        
        print(f"✅ Simple GPU computation successful!")
        print(f"   Device: {result['device']}")
        print(f"   Result shape: {result['result_shape']}")
        print(f"   Result mean: {result['result_mean']:.6f}")
        print(f"   Result std: {result['result_std']:.6f}")
        print(f"   CUDA available: {result['cuda_available']}")
        print(f"   GPU count: {result['gpu_count']}")
        
        return True
        
    except Exception as e:
        print(f"❌ Simple GPU computation failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_medium_complexity_gpu():
    """Test GPU computation with medium complexity."""
    print("\n🧪 Testing medium complexity GPU computation...")
    
    load_config('tensor01_config.yml')
    creds = TestCredentials()
    tensor01_creds = creds.get_tensor01_credentials()
    
    configure(
        cluster_type="ssh",
        cluster_host=tensor01_creds['host'],
        username=tensor01_creds['username'],
        password=tensor01_creds['password'],
        cleanup_on_success=False,
        use_two_venv=True,
        venv_setup_timeout=600,
        auto_gpu_parallel=False,
        auto_parallel=False,
    )
    
    @cluster(cores=2, memory='8GB', time='00:10:00')
    def medium_gpu_computation():
        """Medium complexity GPU computation."""
        import torch
        
        results = []
        
        for i in range(3):  # Test with a small loop
            device = torch.device(f'cuda:{i % torch.cuda.device_count()}')
            
            # Create matrices
            a = torch.randn(50, 50, device=device)
            b = torch.randn(50, 50, device=device)
            
            # Matrix operations
            c = torch.mm(a, b)
            trace = torch.trace(c)
            det = torch.det(c)
            
            results.append({
                "iteration": i,
                "device": str(device),
                "trace": trace.item(),
                "determinant": det.item(),
                "mean": c.mean().item()
            })
        
        return {
            "success": True,
            "results": results,
            "total_iterations": len(results)
        }
    
    try:
        print("Executing medium complexity GPU computation...")
        result = medium_gpu_computation()
        
        print(f"✅ Medium GPU computation successful!")
        print(f"   Total iterations: {result['total_iterations']}")
        
        for res in result['results']:
            print(f"   Iteration {res['iteration']}: device={res['device']}, "
                  f"trace={res['trace']:.4f}, det={res['determinant']:.2e}")
        
        return True
        
    except Exception as e:
        print(f"❌ Medium GPU computation failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("🚀 Testing GPU computations with increasing complexity...")
    
    # Test 1: Simple computation
    simple_works = test_simple_gpu_computation()
    
    if simple_works:
        # Test 2: Medium complexity
        medium_works = test_medium_complexity_gpu()
        
        if medium_works:
            print(f"\n🎉 Both simple and medium GPU computations work!")
            print(f"✅ Basic GPU execution confirmed working")
        else:
            print(f"\n❌ Medium complexity GPU computation failed")
    else:
        print(f"\n❌ Simple GPU computation failed")