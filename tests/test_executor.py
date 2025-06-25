import pytest
import pickle
import json
from unittest.mock import Mock, patch, MagicMock, call
from pathlib import Path
import paramiko
from clustrix.executor import ClusterExecutor
from clustrix.config import ClusterConfig


class TestClusterExecutor:
    """Test ClusterExecutor class."""
    
    @pytest.fixture
    def executor(self, mock_config):
        """Create a ClusterExecutor instance with mock config."""
        return ClusterExecutor(mock_config)
        
    def test_initialization(self, executor, mock_config):
        """Test executor initialization."""
        assert executor.config == mock_config
        assert executor.ssh_client is None
        assert executor.sftp_client is None
        
    @patch('paramiko.SSHClient')
    def test_connect(self, mock_ssh_class, executor):
        """Test SSH connection establishment."""
        mock_ssh = Mock()
        mock_ssh_class.return_value = mock_ssh
        mock_sftp = Mock()
        mock_ssh.open_sftp.return_value = mock_sftp
        
        executor.connect()
        
        mock_ssh.set_missing_host_key_policy.assert_called_once()
        mock_ssh.connect.assert_called_once_with(
            hostname="test.cluster.com",
            port=22,
            username="testuser",
            key_filename="~/.ssh/test_key",
            password=None
        )
        assert executor.ssh_client == mock_ssh
        assert executor.sftp_client == mock_sftp
        
    @patch('paramiko.SSHClient')
    def test_connect_with_password(self, mock_ssh_class):
        """Test SSH connection with password."""
        config = ClusterConfig(
            cluster_host="test.cluster.com",
            username="testuser",
            password="testpass"
        )
        executor = ClusterExecutor(config)
        
        mock_ssh = Mock()
        mock_ssh_class.return_value = mock_ssh
        
        executor.connect()
        
        mock_ssh.connect.assert_called_once_with(
            hostname="test.cluster.com",
            port=22,
            username="testuser",
            key_filename=None,
            password="testpass"
        )
        
    def test_disconnect(self, executor):
        """Test SSH disconnection."""
        executor.ssh_client = Mock()
        executor.sftp_client = Mock()
        
        executor.disconnect()
        
        executor.sftp_client.close.assert_called_once()
        executor.ssh_client.close.assert_called_once()
        assert executor.ssh_client is None
        assert executor.sftp_client is None
        
    @patch('paramiko.SSHClient')
    def test_execute_command(self, mock_ssh_class, executor):
        """Test command execution."""
        mock_ssh = Mock()
        mock_ssh_class.return_value = mock_ssh
        executor.ssh_client = mock_ssh
        
        # Setup mock response
        mock_stdout = Mock()
        mock_stdout.read.return_value = b"command output"
        mock_stdout.channel.recv_exit_status.return_value = 0
        
        mock_stderr = Mock()
        mock_stderr.read.return_value = b""
        
        mock_ssh.exec_command.return_value = (None, mock_stdout, mock_stderr)
        
        stdout, stderr, exit_code = executor._execute_command("echo test")
        
        assert stdout == "command output"
        assert stderr == ""
        assert exit_code == 0
        mock_ssh.exec_command.assert_called_once_with("echo test")
        
    def test_execute_command_not_connected(self, executor):
        """Test command execution without connection."""
        with pytest.raises(RuntimeError, match="Not connected"):
            executor._execute_command("echo test")
            
    @patch('cloudpickle.dumps')
    def test_prepare_function_data(self, mock_pickle, executor):
        """Test function data preparation."""
        def test_func(x):
            return x * 2
            
        mock_pickle.return_value = b"pickled_data"
        
        result = executor._prepare_function_data(
            test_func, 
            (5,), 
            {}, 
            {"cores": 4}
        )
        
        assert result == b"pickled_data"
        mock_pickle.assert_called_once()
        
        # Check the structure of pickled data
        call_args = mock_pickle.call_args[0][0]
        assert call_args['func'].__name__ == 'test_func'
        assert call_args['args'] == (5,)
        assert call_args['kwargs'] == {}
        assert call_args['config'] == {"cores": 4}
        
    def test_submit_slurm_job(self, executor):
        """Test SLURM job submission."""
        executor.ssh_client = Mock()
        executor.sftp_client = Mock()
        
        # Mock command execution
        mock_stdout = Mock()
        mock_stdout.read.return_value = b"12345"
        mock_stdout.channel.recv_exit_status.return_value = 0
        
        executor.ssh_client.exec_command.return_value = (None, mock_stdout, Mock())
        
        job_id = executor._submit_slurm_job("job_12345", {"cores": 4})
        
        assert job_id == "12345"
        
        # Verify sbatch command was called
        call_args = executor.ssh_client.exec_command.call_args[0][0]
        assert "sbatch" in call_args
        assert "submit.sh" in call_args
        
    def test_submit_pbs_job(self, executor):
        """Test PBS job submission."""
        executor.ssh_client = Mock()
        executor.sftp_client = Mock()
        
        # Mock command execution
        mock_stdout = Mock()
        mock_stdout.read.return_value = b"67890.pbs"
        mock_stdout.channel.recv_exit_status.return_value = 0
        
        executor.ssh_client.exec_command.return_value = (None, mock_stdout, Mock())
        
        job_id = executor._submit_pbs_job("job_67890", {"cores": 4})
        
        assert job_id == "67890"
        
        # Verify qsub command was called
        call_args = executor.ssh_client.exec_command.call_args[0][0]
        assert "qsub" in call_args
        assert "submit.sh" in call_args
        
    def test_submit_sge_job_not_implemented(self, executor):
        """Test that SGE job submission raises NotImplementedError."""
        executor.ssh_client = Mock()
        
        with pytest.raises(NotImplementedError):
            executor._submit_sge_job("job_id", {})
            
    def test_submit_k8s_job_not_implemented(self, executor):
        """Test that K8s job submission raises NotImplementedError."""
        with pytest.raises(NotImplementedError):
            executor._submit_k8s_job("job_id", {})
            
    def test_check_slurm_status(self, executor):
        """Test SLURM job status checking."""
        executor.ssh_client = Mock()
        
        # Mock squeue output
        mock_stdout = Mock()
        mock_stdout.read.return_value = b"RUNNING"
        mock_stdout.channel.recv_exit_status.return_value = 0
        
        executor.ssh_client.exec_command.return_value = (None, mock_stdout, Mock())
        
        status = executor._check_slurm_status("12345")
        
        assert status == "running"
        
        # Verify squeue command
        call_args = executor.ssh_client.exec_command.call_args[0][0]
        assert "squeue" in call_args
        assert "12345" in call_args
        
    def test_check_pbs_status(self, executor):
        """Test PBS job status checking."""
        executor.ssh_client = Mock()
        
        # Mock qstat output
        mock_stdout = Mock()
        mock_stdout.read.return_value = b"12345.pbs  user  R  queue"
        mock_stdout.channel.recv_exit_status.return_value = 0
        
        executor.ssh_client.exec_command.return_value = (None, mock_stdout, Mock())
        
        status = executor._check_pbs_status("12345")
        
        assert status == "running"
        
    def test_get_job_status_completed(self, executor):
        """Test job status when result file exists."""
        executor.ssh_client = Mock()
        executor.sftp_client = Mock()
        
        # Mock file existence check
        executor.sftp_client.stat.return_value = Mock()  # File exists
        
        status = executor.get_job_status("job_12345")
        
        assert status == "completed"
        
    def test_get_job_status_failed(self, executor):
        """Test job status when error file exists."""
        executor.ssh_client = Mock()
        executor.sftp_client = Mock()
        
        # Mock file existence check
        def stat_side_effect(path):
            if "error.pkl" in path:
                return Mock()  # Error file exists
            else:
                raise IOError()  # Result file doesn't exist
                
        executor.sftp_client.stat.side_effect = stat_side_effect
        
        status = executor.get_job_status("job_12345")
        
        assert status == "failed"
        
    @patch('tempfile.NamedTemporaryFile')
    def test_get_result_success(self, mock_temp_file, executor):
        """Test retrieving successful result."""
        executor.sftp_client = Mock()
        
        # Mock temp file
        mock_file = Mock()
        mock_file.name = "/tmp/test_result"
        mock_temp_file.return_value.__enter__.return_value = mock_file
        
        # Mock result data
        test_result = {"value": 42}
        with open("/tmp/test_pickle", "wb") as f:
            pickle.dump(test_result, f)
            
        with patch('builtins.open', create=True) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = pickle.dumps(test_result)
            
            result = executor.get_result("job_12345")
            
        assert result == test_result
        executor.sftp_client.get.assert_called_once()
        
    def test_cancel_job_slurm(self, executor):
        """Test canceling SLURM job."""
        executor.ssh_client = Mock()
        executor.config.cluster_type = "slurm"
        
        mock_stdout = Mock()
        mock_stdout.read.return_value = b""
        mock_stdout.channel.recv_exit_status.return_value = 0
        
        executor.ssh_client.exec_command.return_value = (None, mock_stdout, Mock())
        
        executor.cancel_job("12345")
        
        call_args = executor.ssh_client.exec_command.call_args[0][0]
        assert "scancel 12345" in call_args
        
    def test_cancel_job_pbs(self, executor):
        """Test canceling PBS job."""
        executor.ssh_client = Mock()
        executor.config.cluster_type = "pbs"
        
        mock_stdout = Mock()
        mock_stdout.read.return_value = b""
        mock_stdout.channel.recv_exit_status.return_value = 0
        
        executor.ssh_client.exec_command.return_value = (None, mock_stdout, Mock())
        
        executor.cancel_job("67890")
        
        call_args = executor.ssh_client.exec_command.call_args[0][0]
        assert "qdel 67890" in call_args