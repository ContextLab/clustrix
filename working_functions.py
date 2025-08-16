#!/usr/bin/env python3

"""
Working functions module for Kubernetes auto-provisioning.
Functions defined here can be properly serialized and executed remotely.
"""

def analyze_dataset_simple(size, complexity="medium"):
    """
    Simple, importable data analysis function.
    This demonstrates the pattern that actually works for users.
    """
    # All imports inside function for remote execution
    import math
    import platform
    import socket
    import time
    
    print(f"🔬 Analyzing dataset (size: {size}, complexity: {complexity})")
    
    # Simulate computation
    start_time = time.time()
    
    if complexity == "simple":
        result = size * 2
    elif complexity == "medium":
        result = sum(math.sqrt(i) for i in range(min(size, 1000)))
    else:
        result = sum(math.sin(i) * math.cos(i) for i in range(min(size, 5000)))
    
    computation_time = time.time() - start_time
    
    # Return comprehensive results  
    return {
        "input": {"size": size, "complexity": complexity},
        "computation": {
            "result": round(result, 2),
            "time_seconds": round(computation_time, 3)
        },
        "environment": {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "execution_context": "auto_provisioned_kubernetes"
        },
        "success": True,
        "message": "✅ Successfully executed on auto-provisioned Kubernetes cluster!"
    }