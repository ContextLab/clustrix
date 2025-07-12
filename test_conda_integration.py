#!/usr/bin/env python3
"""
Quick test to verify conda integration is working.
"""

import os
from clustrix import cluster
from clustrix.config import load_config, configure

def main():
    # Set environment password if available
    if 'CLUSTRIX_PASSWORD' not in os.environ:
        print("Setting test password...")
        os.environ['CLUSTRIX_PASSWORD'] = 'test123'
    
    # Load configuration
    load_config("tensor01_config.yml")
    
    # Configure with environment password
    configure(
        password=os.environ.get('CLUSTRIX_PASSWORD'),
        cleanup_on_success=False,
        job_poll_interval=5,
    )
    
    @cluster(cores=1, memory="2GB")
    def test_conda_venv():
        """Test that conda VENV2 is working."""
        import subprocess
        import sys
        import os
        
        result = {
            "python_version": sys.version,
            "python_executable": sys.executable,
            "working_directory": os.getcwd(),
            "conda_info": {},
        }
        
        # Check if we're in a conda environment
        try:
            conda_env = os.environ.get('CONDA_DEFAULT_ENV', 'Not set')
            result["conda_info"]["conda_default_env"] = conda_env
        except Exception as e:
            result["conda_info"]["error"] = str(e)
        
        # Try to check PyTorch
        try:
            import torch
            result["pytorch"] = {
                "version": torch.__version__,
                "cuda_available": torch.cuda.is_available(),
                "device_count": torch.cuda.device_count() if torch.cuda.is_available() else 0,
            }
        except ImportError:
            result["pytorch"] = {"error": "PyTorch not installed"}
        except Exception as e:
            result["pytorch"] = {"error": str(e)}
            
        return result
    
    print("Testing conda integration on tensor01...")
    try:
        result = test_conda_venv()
        print("Success! Result:")
        for key, value in result.items():
            print(f"  {key}: {value}")
        return True
    except Exception as e:
        print(f"Test failed: {e}")
        return False

if __name__ == "__main__":
    main()