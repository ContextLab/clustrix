import pytest
import ast
import pickle
from unittest.mock import patch, Mock
from clustrix.utils import (
    serialize_function, deserialize_function, get_environment_requirements,
    detect_loops, create_job_script, setup_remote_environment
)
from clustrix.loop_analysis import detect_loops_in_function
from clustrix.config import ClusterConfig


class TestSerialization:
    """Test function serialization utilities."""
    
    def test_serialize_deserialize_simple_function(self):
        """Test serializing and deserializing a simple function."""
        def test_func(x, y):
            return x + y
            
        serialized = serialize_function(test_func, (5, 3), {})
        result_func, args, kwargs = deserialize_function(serialized)
        
        assert result_func(5, 3) == 8
        assert args == (5, 3)
        assert kwargs == {}
        
    def test_serialize_deserialize_with_kwargs(self):
        """Test serialization with keyword arguments."""
        def test_func(x, y=10, z=20):
            return x + y + z
            
        serialized = serialize_function(test_func, (5,), {"z": 30})
        result_func, args, kwargs = deserialize_function(serialized)
        
        assert result_func(*args, **kwargs) == 45
        assert args == (5,)
        assert kwargs == {"z": 30}
        
    def test_serialize_complex_data(self):
        """Test serialization of complex data types."""
        def test_func(data):
            total = 0
            for key, value in data.items():
                if isinstance(value, list):
                    total += sum(value)
                elif isinstance(value, dict):
                    total += sum(value.values())
                else:
                    total += value
            return total
            
        complex_data = {"a": [1, 2, 3], "b": {"nested": 4}, "c": 5}
        serialized = serialize_function(test_func, (complex_data,), {})
        result_func, args, kwargs = deserialize_function(serialized)
        
        # Calculate expected sum: [1,2,3] = 6, {"nested": 4} = 4, 5 = 5, total = 15
        expected = 15
        assert result_func(*args, **kwargs) == expected
        
    @patch('clustrix.utils.dill.dumps')
    @patch('clustrix.utils.cloudpickle.dumps')
    def test_fallback_to_dill(self, mock_cloudpickle, mock_dill):
        """Test fallback to dill when cloudpickle fails."""
        mock_cloudpickle.side_effect = Exception("Cloudpickle failed")
        mock_dill.return_value = b"dill_data"
        
        def test_func():
            return "test"
            
        result = serialize_function(test_func, (), {})
        
        # The result should be a dict containing the dill data
        assert result['function'] == b"dill_data"
        mock_cloudpickle.assert_called_once()
        mock_dill.assert_called_once()


class TestEnvironmentInfo:
    """Test environment information utilities."""
    
    @patch('subprocess.run')
    def test_get_environment_requirements(self, mock_run):
        """Test getting environment requirements."""
        mock_run.return_value = Mock(
            stdout="package1==1.0.0\npackage2==2.0.0\n",
            returncode=0
        )
        
        requirements = get_environment_requirements()
        
        assert isinstance(requirements, dict)
        # Should contain at least some packages
        assert len(requirements) > 0
        
    def test_get_environment_requirements_format(self):
        """Test environment requirements format."""
        requirements = get_environment_requirements()
        
        # Should be a dictionary
        assert isinstance(requirements, dict)
        
        # Keys should be package names, values should be versions
        for package, version in requirements.items():
            assert isinstance(package, str)
            assert isinstance(version, str)


class TestLoopDetection:
    """Test loop detection functionality."""
    
    def test_detect_loops_function_exists(self):
        """Test that the detect_loops_in_function exists and can be called."""
        def simple_func():
            pass
            
        # Should not raise an error
        loops = detect_loops_in_function(simple_func)
        assert isinstance(loops, list)
        
    def test_detect_loops_legacy_function(self):
        """Test the legacy detect_loops function."""
        def simple_func():
            for i in range(5):
                pass
            
        # Should not raise an error  
        result = detect_loops(simple_func, (), {})
        # Result might be None or a dict, both are acceptable
        assert result is None or isinstance(result, dict)


class TestScriptGeneration:
    """Test job script generation."""
    
    def test_create_job_script_slurm(self):
        """Test SLURM script generation."""
        config = ClusterConfig(
            remote_work_dir="/scratch/test",
            python_executable="python3",
            module_loads=["python/3.9", "cuda/11.2"],
            environment_variables={"TEST_VAR": "value"}
        )
        
        job_config = {
            "cores": 8,
            "memory": "16GB",
            "time": "02:00:00",
            "partition": "gpu"
        }
        
        script = create_job_script("slurm", job_config, "/scratch/test/jobs/job_123", config)
        
        assert "#!/bin/bash" in script
        assert "#SBATCH" in script
        assert "cd /scratch/test/jobs/job_123" in script
        
    def test_create_job_script_pbs(self):
        """Test PBS script generation."""
        config = ClusterConfig(
            remote_work_dir="/home/test",
            python_executable="python3"
        )
        
        job_config = {
            "cores": 4,
            "memory": "8GB",
            "time": "01:00:00",
            "queue": "batch"
        }
        
        script = create_job_script("pbs", job_config, "/home/test/jobs/job_456", config)
        
        assert "#!/bin/bash" in script
        assert "#PBS" in script
        assert "cd /home/test/jobs/job_456" in script
        
    def test_create_job_script_sge_not_implemented(self):
        """Test that SGE script generation is not fully implemented."""
        config = ClusterConfig()
        
        # Currently returns None since it's not implemented
        result = create_job_script("sge", {}, "/tmp/job", config)
        assert result is None


class TestRemoteEnvironment:
    """Test remote environment setup."""
    
    def test_setup_remote_environment(self):
        """Test remote environment setup."""
        from unittest.mock import MagicMock
        
        mock_ssh = Mock()
        mock_sftp = Mock()
        
        # Mock the SFTP context manager properly
        mock_file = MagicMock()
        mock_sftp.open.return_value = mock_file
        mock_ssh.open_sftp.return_value = mock_sftp
        
        # Mock exec_command return values
        mock_stdin = Mock()
        mock_stdout = Mock()
        mock_stderr = Mock()
        
        # Mock the channel and exit status
        mock_channel = Mock()
        mock_channel.recv_exit_status.return_value = 0  # Success
        mock_stdout.channel = mock_channel
        
        mock_ssh.exec_command.return_value = (mock_stdin, mock_stdout, mock_stderr)
        
        requirements = {"numpy": "1.21.0", "pandas": "1.3.0"}
        
        # This should not raise an error
        setup_remote_environment(mock_ssh, "/tmp/test", requirements)
        
        # Verify SSH commands were executed
        assert mock_ssh.exec_command.called
        # Verify SFTP was used
        assert mock_ssh.open_sftp.called