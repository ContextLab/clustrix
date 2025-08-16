#!/usr/bin/env python3
"""
Test functions for Kubernetes execution.
"""

def simple_computation(x: int, y: int):
    """Simple computation function for local testing."""
    import platform
    import socket
    
    result = x * y + 42
    
    return {
        "result": result,
        "platform": platform.platform(),
        "hostname": socket.gethostname(),
        "environment": "kubernetes"
    }