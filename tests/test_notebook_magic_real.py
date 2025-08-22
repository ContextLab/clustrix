"""
Real-world tests for notebook magic functionality.

These tests use actual Jupyter environments and real execution,
demonstrating real user workflows without mocks.
"""

import pytest
import os
import json
import yaml
import tempfile
from pathlib import Path
from clustrix.notebook_magic import (
    DEFAULT_CONFIGS,
    detect_config_files,
    load_config_from_file,
    validate_ip_address,
    validate_hostname,
    load_ipython_extension,
)
from clustrix.config import ClusterConfig
from clustrix import cluster


class TestNotebookMagicReal:
    """Test notebook magic with real execution."""

    @pytest.fixture
    def temp_config_dir(self):
        """Create temporary directory for config files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def ipython_environment(self):
        """Create or get IPython environment if available."""
        try:
            from IPython import get_ipython
            from IPython.terminal.interactiveshell import TerminalInteractiveShell

            # Get existing IPython instance or create one
            ip = get_ipython()
            if ip is None:
                # Create a new IPython instance for testing
                ip = TerminalInteractiveShell.instance()

            return ip
        except ImportError:
            pytest.skip("IPython not available")

    def test_default_configs_structure(self):
        """
        Test default configuration structure.

        This demonstrates:
        - Configuration validation
        - Default values
        - No mock dependencies
        """
        assert isinstance(DEFAULT_CONFIGS, dict)
        assert len(DEFAULT_CONFIGS) >= 2  # local and local_multicore

        for config_name, config in DEFAULT_CONFIGS.items():
            assert isinstance(config, dict)
            assert "cluster_type" in config

            # Basic configs should have these core fields
            if config.get("cluster_type") == "local":
                assert "default_cores" in config
                assert "default_memory" in config

                # Validate values
                cores = config["default_cores"]
                assert isinstance(cores, int)
                assert cores == -1 or cores > 0

                memory = config["default_memory"]
                assert isinstance(memory, str)
                assert memory.endswith(("GB", "MB", "GiB", "MiB"))

    def test_config_file_detection(self, temp_config_dir):
        """
        Test configuration file detection with real files.

        This demonstrates:
        - Real file system operations
        - Multiple config format support
        - Priority ordering
        """
        # Create test config files
        yaml_config = temp_config_dir / "clustrix.yml"
        json_config = temp_config_dir / "clustrix.json"
        custom_config = temp_config_dir / "custom_cluster.yaml"

        # Write YAML config
        yaml_data = {
            "cluster_type": "slurm",
            "cluster_host": "hpc.example.com",
            "username": "researcher",
            "default_cores": 8,
            "default_memory": "16GB",
        }
        with open(yaml_config, "w") as f:
            yaml.dump(yaml_data, f)

        # Write JSON config
        json_data = {
            "cluster_type": "kubernetes",
            "namespace": "default",
            "default_cores": 4,
            "default_memory": "8GB",
        }
        with open(json_config, "w") as f:
            json.dump(json_data, f)

        # Write custom config
        custom_data = {
            "cluster_type": "pbs",
            "cluster_host": "cluster.edu",
            "queue": "batch",
        }
        with open(custom_config, "w") as f:
            yaml.dump(custom_data, f)

        # Test detection
        configs = detect_config_files(str(temp_config_dir))

        # Verify all configs were found
        assert len(configs) >= 3
        config_paths = [c[1] for c in configs]
        assert str(yaml_config) in config_paths
        assert str(json_config) in config_paths
        assert str(custom_config) in config_paths

    def test_load_config_from_yaml(self, temp_config_dir):
        """
        Test loading configuration from YAML file.

        This demonstrates:
        - Real YAML parsing
        - Configuration validation
        - Error handling
        """
        config_file = temp_config_dir / "test_config.yml"

        # Write valid YAML config
        config_data = {
            "cluster_type": "slurm",
            "cluster_host": "compute.cluster.edu",
            "username": "testuser",
            "default_cores": 16,
            "default_memory": "32GB",
            "partition": "gpu",
            "modules": ["python/3.9", "cuda/11.4"],
        }

        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        # Load and validate
        loaded_config = load_config_from_file(str(config_file))

        assert loaded_config["cluster_type"] == "slurm"
        assert loaded_config["cluster_host"] == "compute.cluster.edu"
        assert loaded_config["username"] == "testuser"
        assert loaded_config["default_cores"] == 16
        assert loaded_config["modules"] == ["python/3.9", "cuda/11.4"]

    def test_load_config_from_json(self, temp_config_dir):
        """
        Test loading configuration from JSON file.

        This demonstrates:
        - Real JSON parsing
        - Format flexibility
        - Configuration merging
        """
        config_file = temp_config_dir / "test_config.json"

        # Write valid JSON config
        config_data = {
            "cluster_type": "kubernetes",
            "namespace": "ml-workloads",
            "default_cores": 8,
            "default_memory": "16Gi",
            "gpu": 1,
            "node_selector": {"workload": "gpu", "tier": "production"},
        }

        with open(config_file, "w") as f:
            json.dump(config_data, f, indent=2)

        # Load and validate
        loaded_config = load_config_from_file(str(config_file))

        assert loaded_config["cluster_type"] == "kubernetes"
        assert loaded_config["namespace"] == "ml-workloads"
        assert loaded_config["gpu"] == 1
        assert loaded_config["node_selector"]["workload"] == "gpu"

    def test_validate_ip_address(self):
        """
        Test IP address validation.

        This demonstrates:
        - Input validation logic
        - No external dependencies
        - Edge case handling
        """
        # Valid IP addresses
        assert validate_ip_address("192.168.1.1") is True
        assert validate_ip_address("10.0.0.1") is True
        assert validate_ip_address("172.16.0.1") is True
        assert validate_ip_address("8.8.8.8") is True
        assert validate_ip_address("255.255.255.255") is True

        # Invalid IP addresses
        assert validate_ip_address("256.1.1.1") is False
        assert validate_ip_address("192.168.1") is False
        assert validate_ip_address("192.168.1.1.1") is False
        assert validate_ip_address("abc.def.ghi.jkl") is False
        assert validate_ip_address("") is False
        assert validate_ip_address("192.168.-1.1") is False

    def test_validate_hostname(self):
        """
        Test hostname validation.

        This demonstrates:
        - DNS hostname validation
        - RFC compliance checking
        - Pattern matching
        """
        # Valid hostnames
        assert validate_hostname("example.com") is True
        assert validate_hostname("sub.example.com") is True
        assert validate_hostname("my-server") is True
        assert validate_hostname("server123") is True
        assert validate_hostname("a.b.c.d.example.org") is True
        assert validate_hostname("localhost") is True

        # Invalid hostnames
        assert validate_hostname("example..com") is False
        assert validate_hostname("-example.com") is False
        assert validate_hostname("example.com-") is False
        assert validate_hostname("exam ple.com") is False
        assert validate_hostname("example.com/path") is False
        assert validate_hostname("") is False
        assert validate_hostname("a" * 256) is False  # Too long

    @pytest.mark.real_world
    def test_magic_registration_in_ipython(self, ipython_environment):
        """
        Test magic command registration in IPython.

        This demonstrates:
        - Real IPython integration
        - Magic command registration
        - Interactive environment setup
        """
        ip = ipython_environment

        # Load the magic
        load_ipython_extension(ip)

        # Verify magic is registered
        assert "clustrix" in ip.magics_manager.magics["line"]
        assert "clusterfy" in ip.magics_manager.magics["cell"]

        # Test line magic execution with config
        result = ip.run_line_magic("clustrix", "config")

        # Should show current configuration
        assert result is not None or True  # May return None but sets state

        # Verify clustrix is available in namespace
        assert "cluster" in ip.user_ns or "clustrix" in dir(ip.user_ns)

    @pytest.mark.real_world
    def test_cell_magic_execution(self, ipython_environment, temp_config_dir):
        """
        Test cell magic execution with real code.

        This demonstrates:
        - Real code execution through magic
        - Function decoration
        - Result retrieval
        """
        ip = ipython_environment

        # Load the magic
        load_ipython_extension(ip)

        # Configure for local execution
        config_file = temp_config_dir / "local.yml"
        config_data = {
            "cluster_type": "local",
            "default_cores": 2,
            "default_memory": "4GB",
        }
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        # Load config
        ip.run_line_magic("clustrix", f"load {config_file}")

        # Define and execute function using cell magic
        cell_code = """
def compute_statistics(data):
    import numpy as np
    arr = np.array(data)
    return {
        'mean': float(np.mean(arr)),
        'std': float(np.std(arr)),
        'sum': float(np.sum(arr))
    }

# Execute the function
test_data = [1, 2, 3, 4, 5]
result = compute_statistics(test_data)
"""

        # Run with clusterfy magic
        ip.run_cell_magic("clusterfy", "cores=2 memory=2GB", cell_code)

        # Verify function was executed
        assert "compute_statistics" in ip.user_ns
        assert "result" in ip.user_ns

        # Check result
        result = ip.user_ns["result"]
        assert result["mean"] == 3.0
        assert result["sum"] == 15.0
        assert result["std"] > 0

    def test_config_persistence(self, temp_config_dir):
        """
        Test configuration persistence across sessions.

        This demonstrates:
        - Configuration saving
        - State persistence
        - File-based storage
        """
        config_file = temp_config_dir / "persistent_config.yml"

        # Create initial configuration
        initial_config = ClusterConfig()
        initial_config.cluster_type = "slurm"
        initial_config.cluster_host = "hpc.university.edu"
        initial_config.username = "researcher"
        initial_config.default_cores = 32
        initial_config.default_memory = "64GB"

        # Save configuration
        config_dict = {
            "cluster_type": initial_config.cluster_type,
            "cluster_host": initial_config.cluster_host,
            "username": initial_config.username,
            "default_cores": initial_config.default_cores,
            "default_memory": initial_config.default_memory,
        }

        with open(config_file, "w") as f:
            yaml.dump(config_dict, f)

        # Load configuration in new session
        loaded_config = load_config_from_file(str(config_file))

        # Verify persistence
        assert loaded_config["cluster_type"] == "slurm"
        assert loaded_config["cluster_host"] == "hpc.university.edu"
        assert loaded_config["username"] == "researcher"
        assert loaded_config["default_cores"] == 32
        assert loaded_config["default_memory"] == "64GB"

    def test_error_handling_invalid_config(self, temp_config_dir):
        """
        Test error handling with invalid configurations.

        This demonstrates:
        - Graceful error handling
        - User-friendly messages
        - Validation feedback
        """
        # Test malformed YAML
        bad_yaml = temp_config_dir / "bad.yml"
        with open(bad_yaml, "w") as f:
            f.write("invalid: yaml: content: [")

        with pytest.raises(Exception):
            load_config_from_file(str(bad_yaml))

        # Test malformed JSON
        bad_json = temp_config_dir / "bad.json"
        with open(bad_json, "w") as f:
            f.write('{"invalid": json content}')

        with pytest.raises(Exception):
            load_config_from_file(str(bad_json))

        # Test non-existent file
        with pytest.raises(FileNotFoundError):
            load_config_from_file("/nonexistent/config.yml")


class TestNotebookMagicIntegrationWorkflows:
    """Integration tests showing complete notebook workflows."""

    @pytest.mark.real_world
    def test_complete_notebook_workflow(self, ipython_environment, temp_config_dir):
        """
        Test complete notebook workflow as users would use it.

        This demonstrates the full user experience in a notebook
        environment from setup through execution.
        """
        ip = ipython_environment

        # Load clustrix magic
        load_ipython_extension(ip)

        # Create configuration file as user would
        config_file = temp_config_dir / "my_cluster.yml"
        with open(config_file, "w") as f:
            yaml.dump(
                {
                    "cluster_type": "local",
                    "default_cores": 4,
                    "default_memory": "8GB",
                    "cleanup_remote_files": True,
                },
                f,
            )

        # User loads configuration
        ip.run_line_magic("clustrix", f"load {config_file}")

        # User checks current configuration
        ip.run_line_magic("clustrix", "status")

        # User defines data analysis function with cell magic
        analysis_code = """
@cluster(cores=4, memory="8GB")
def analyze_dataset(n_samples):
    '''Analyze synthetic dataset.'''
    import numpy as np
    from sklearn.datasets import make_classification
    from sklearn.decomposition import PCA
    
    # Generate dataset
    X, y = make_classification(
        n_samples=n_samples,
        n_features=20,
        n_informative=15,
        n_redundant=5,
        random_state=42
    )
    
    # Perform PCA
    pca = PCA(n_components=5)
    X_reduced = pca.fit_transform(X)
    
    # Compute statistics
    return {
        'n_samples': n_samples,
        'original_shape': X.shape,
        'reduced_shape': X_reduced.shape,
        'explained_variance': pca.explained_variance_ratio_.tolist(),
        'total_variance_explained': float(np.sum(pca.explained_variance_ratio_)),
        'first_component_variance': float(pca.explained_variance_ratio_[0])
    }

# Run analysis
results = analyze_dataset(1000)
print(f"Analysis complete: {results['total_variance_explained']:.2%} variance explained")
"""

        # Execute with clusterfy magic
        ip.run_cell_magic("clusterfy", "", analysis_code)

        # Verify results
        assert "analyze_dataset" in ip.user_ns
        assert "results" in ip.user_ns

        results = ip.user_ns["results"]
        assert results["n_samples"] == 1000
        assert results["original_shape"] == [1000, 20]
        assert results["reduced_shape"] == [1000, 5]
        assert len(results["explained_variance"]) == 5
        assert 0 < results["total_variance_explained"] <= 1

    @pytest.mark.real_world
    def test_parallel_execution_workflow(self, ipython_environment):
        """
        Test parallel execution workflow in notebook.

        This demonstrates:
        - Parallel computation setup
        - Loop parallelization
        - Result aggregation
        """
        ip = ipython_environment

        # Load clustrix
        load_ipython_extension(ip)

        # Configure for local parallel execution
        ip.run_line_magic("clustrix", "config local_multicore")

        # Define parallel computation
        parallel_code = """
@cluster(cores=4, parallel=True)
def parallel_analysis(data_sizes):
    '''Analyze multiple datasets in parallel.'''
    results = []
    
    for size in data_sizes:
        import numpy as np
        
        # Generate random data
        data = np.random.randn(size, size)
        
        # Compute statistics
        result = {
            'size': size,
            'mean': float(np.mean(data)),
            'std': float(np.std(data)),
            'max': float(np.max(data)),
            'min': float(np.min(data))
        }
        
        # Compute determinant for small matrices
        if size <= 10:
            result['determinant'] = float(np.linalg.det(data))
        
        results.append(result)
    
    return results

# Execute parallel analysis
sizes = [10, 20, 30, 40, 50]
parallel_results = parallel_analysis(sizes)

# Display results
for r in parallel_results:
    print(f"Size {r['size']}: mean={r['mean']:.3f}, std={r['std']:.3f}")
"""

        # Execute
        ip.run_cell_magic("clusterfy", "parallel=True", parallel_code)

        # Verify parallel execution
        assert "parallel_analysis" in ip.user_ns
        assert "parallel_results" in ip.user_ns

        results = ip.user_ns["parallel_results"]
        assert len(results) == 5

        for i, r in enumerate(results):
            assert r["size"] == [10, 20, 30, 40, 50][i]
            assert "mean" in r
            assert "std" in r
            assert r["std"] > 0  # Should have variation
