#!/usr/bin/env python3

"""
User analysis module - demonstrates how real users would structure their code.
This is the typical pattern users follow: separate modules for their analysis functions.
"""

from clustrix import cluster

@cluster(
    cores=1,
    memory="512Mi",
    platform="kubernetes",
    auto_provision=True,
    provider="local",
    node_count=2
)
def analyze_data(dataset_size: int, complexity: str = "medium"):
    """
    A realistic data analysis function that a user might write.
    This demonstrates the exact user experience in a properly structured module.
    """
    import platform
    import socket
    import time
    import math
    
    print(f"🔬 Starting analysis of dataset (size: {dataset_size}, complexity: {complexity})")
    
    # Simulate some computation
    start_time = time.time()
    
    if complexity == "simple":
        result = dataset_size * 2
    elif complexity == "medium":
        result = sum(math.sqrt(i) for i in range(min(dataset_size, 1000)))
    else:  # complex
        result = sum(math.sin(i) * math.cos(i) for i in range(min(dataset_size, 5000)))
    
    computation_time = time.time() - start_time
    
    # Return realistic analysis results
    return {
        "dataset_size": dataset_size,
        "complexity": complexity,
        "computation_result": round(result, 2),
        "computation_time_seconds": round(computation_time, 3),
        "execution_info": {
            "platform": platform.platform(),
            "hostname": socket.gethostname(),
            "python_version": platform.python_version(),
            "environment": "kubernetes_cluster"
        },
        "success": True
    }