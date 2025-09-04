"""
Test GPU detection and job script generation functionality in clustrix/utils.py.

This module tests the job script generation and GPU-enhanced features
from lines 1078-1789 in clustrix/utils.py as part of Issue #100 Stream 3.
"""

import pytest
from unittest.mock import patch, Mock, MagicMock, call
import tempfile
import os
from typing import Dict, Any

from clustrix.utils import (
    create_job_script,
    _create_slurm_script,
    _create_pbs_script,
    _create_sge_script,
    _create_ssh_script,
    detect_gpu_capabilities,
    setup_gpu_enabled_venv2,
    enhanced_setup_two_venv_environment,
)
from clustrix.config import ClusterConfig


class TestJobScriptGeneration:
    """Test job script generation for different cluster schedulers."""

    def test_create_job_script_slurm(self):
        """Test job script creation for SLURM scheduler."""
        config = ClusterConfig(
            cluster_type="slurm", cluster_host="test.cluster.edu", username="testuser"
        )
        job_config = {
            "cores": 4,
            "memory": "8GB",
            "time": "02:00:00",
            "partition": "gpu",
        }
        remote_job_dir = "/scratch/testuser/job_123"

        script = create_job_script("slurm", job_config, remote_job_dir, config)

        assert "#!/bin/bash" in script
        assert "#SBATCH --job-name=clustrix" in script
        assert "#SBATCH --cpus-per-task=4" in script
        assert "#SBATCH --mem=8GB" in script
        assert "#SBATCH --time=02:00:00" in script
        assert "#SBATCH --partition=gpu" in script
        assert f"cd {remote_job_dir}" in script
        assert "source venv/bin/activate" in script

    def test_create_job_script_pbs(self):
        """Test job script creation for PBS scheduler."""
        config = ClusterConfig(
            cluster_type="pbs", cluster_host="pbs.cluster.edu", username="testuser"
        )
        job_config = {
            "cores": 8,
            "memory": "16GB",
            "time": "04:00:00",
            "queue": "batch",
        }
        remote_job_dir = "/home/testuser/job_456"

        script = create_job_script("pbs", job_config, remote_job_dir, config)

        assert "#!/bin/bash" in script
        assert "#PBS -N clustrix" in script
        assert "#PBS -l nodes=1:ppn=8" in script
        assert "#PBS -l mem=16GB" in script
        assert "#PBS -l walltime=04:00:00" in script
        assert "#PBS -q batch" in script
        assert f"cd {remote_job_dir}" in script

    def test_create_job_script_sge(self):
        """Test job script creation for SGE scheduler."""
        config = ClusterConfig(
            cluster_type="sge", cluster_host="sge.cluster.edu", username="testuser"
        )
        job_config = {"cores": 2, "memory": "4GB", "time": "01:00:00"}
        remote_job_dir = "/tmp/testuser/job_789"

        script = create_job_script("sge", job_config, remote_job_dir, config)

        assert "#!/bin/bash" in script
        assert "#$ -N clustrix" in script
        assert "#$ -pe smp 2" in script
        assert "#$ -l h_vmem=4GB" in script
        assert "#$ -l h_rt=01:00:00" in script
        assert "#$ -cwd" in script
        assert f"cd {remote_job_dir}" in script

    def test_create_job_script_ssh(self):
        """Test job script creation for SSH execution."""
        config = ClusterConfig(
            cluster_type="ssh", cluster_host="ssh.cluster.edu", username="testuser"
        )
        job_config = {"cores": 1, "memory": "2GB", "time": "00:30:00"}
        remote_job_dir = "/var/tmp/job_101"

        script = create_job_script("ssh", job_config, remote_job_dir, config)

        assert "#!/bin/bash" in script
        assert f"cd {remote_job_dir}" in script
        assert "import pickle" in script
        assert "import sys" in script
        assert "import traceback" in script

    def test_create_job_script_invalid_type(self):
        """Test job script creation with invalid scheduler type."""
        config = ClusterConfig()
        job_config = {"cores": 1, "memory": "1GB", "time": "00:15:00"}
        remote_job_dir = "/tmp/job"

        with pytest.raises(ValueError, match="Unsupported cluster type: invalid"):
            create_job_script("invalid", job_config, remote_job_dir, config)


class TestSlurmScriptGeneration:
    """Test SLURM-specific job script generation."""

    def test_slurm_script_basic_configuration(self):
        """Test basic SLURM script generation."""
        config = ClusterConfig()
        job_config = {"cores": 4, "memory": "8GB", "time": "02:00:00"}
        remote_job_dir = "/scratch/job"

        script = _create_slurm_script(job_config, remote_job_dir, config)

        assert "#SBATCH --job-name=clustrix" in script
        assert "#SBATCH --cpus-per-task=4" in script
        assert "#SBATCH --mem=8GB" in script
        assert "#SBATCH --time=02:00:00" in script
        assert f"#SBATCH --output={remote_job_dir}/slurm-%j.out" in script
        assert f"#SBATCH --error={remote_job_dir}/slurm-%j.err" in script

    def test_slurm_script_with_modules(self):
        """Test SLURM script with module loading."""
        config = ClusterConfig()
        config.module_loads = ["python/3.9", "cuda/11.8", "gcc/9.3"]
        job_config = {"cores": 2, "memory": "4GB", "time": "01:00:00"}
        remote_job_dir = "/scratch/job"

        script = _create_slurm_script(job_config, remote_job_dir, config)

        assert "module load python/3.9" in script
        assert "module load cuda/11.8" in script
        assert "module load gcc/9.3" in script

    def test_slurm_script_with_environment_variables(self):
        """Test SLURM script with environment variables."""
        config = ClusterConfig()
        config.environment_variables = {
            "CUDA_VISIBLE_DEVICES": "0,1",
            "OMP_NUM_THREADS": "4",
            "PYTHONPATH": "/opt/software/lib",
        }
        job_config = {"cores": 4, "memory": "8GB", "time": "02:00:00"}
        remote_job_dir = "/scratch/job"

        script = _create_slurm_script(job_config, remote_job_dir, config)

        assert "export CUDA_VISIBLE_DEVICES=0,1" in script
        assert "export OMP_NUM_THREADS=4" in script
        assert "export PYTHONPATH=/opt/software/lib" in script

    def test_slurm_script_with_pre_execution_commands(self):
        """Test SLURM script with pre-execution commands."""
        config = ClusterConfig()
        config.pre_execution_commands = [
            "echo 'Starting job'",
            "nvidia-smi",
            "df -h /scratch",
        ]
        job_config = {"cores": 2, "memory": "4GB", "time": "01:00:00"}
        remote_job_dir = "/scratch/job"

        script = _create_slurm_script(job_config, remote_job_dir, config)

        assert "echo 'Starting job'" in script
        assert "nvidia-smi" in script
        assert "df -h /scratch" in script

    def test_slurm_script_with_custom_python(self):
        """Test SLURM script with custom Python executable."""
        config = ClusterConfig()
        config.python_executable = "/opt/python3.10/bin/python"
        job_config = {"cores": 1, "memory": "2GB", "time": "00:30:00"}
        remote_job_dir = "/scratch/job"

        script = _create_slurm_script(job_config, remote_job_dir, config)

        # The custom python executable should be used in the execution
        assert "/opt/python3.10/bin/python" in script or "python" in script

    def test_slurm_script_with_two_venv_setup(self):
        """Test SLURM script with two-venv configuration."""
        config = ClusterConfig()
        config.venv_info = {
            "conda_env1_name": "clustrix_venv1_job123",
            "conda_env2_name": "clustrix_venv2_job123",
        }
        job_config = {"cores": 2, "memory": "4GB", "time": "01:00:00"}
        remote_job_dir = "/scratch/job123"

        with patch("clustrix.utils.generate_two_venv_execution_commands") as mock_gen:
            mock_gen.return_value = [
                "conda activate clustrix_venv1_job123",
                "python venv1_setup.py",
                "conda activate clustrix_venv2_job123",
                "python execute_function.py",
            ]

            script = _create_slurm_script(job_config, remote_job_dir, config)

            mock_gen.assert_called_once_with(
                remote_job_dir, "clustrix_venv1_job123", "clustrix_venv2_job123"
            )
            assert f"cd {remote_job_dir}" in script


class TestPbsScriptGeneration:
    """Test PBS-specific job script generation."""

    def test_pbs_script_basic_configuration(self):
        """Test basic PBS script generation."""
        config = ClusterConfig()
        job_config = {"cores": 8, "memory": "16GB", "time": "04:00:00", "queue": "gpu"}
        remote_job_dir = "/home/user/job"

        script = _create_pbs_script(job_config, remote_job_dir, config)

        assert "#PBS -N clustrix" in script
        assert "#PBS -l nodes=1:ppn=8" in script
        assert "#PBS -l mem=16GB" in script
        assert "#PBS -l walltime=04:00:00" in script
        assert "#PBS -q gpu" in script
        assert f"#PBS -o {remote_job_dir}/job.out" in script
        assert f"#PBS -e {remote_job_dir}/job.err" in script

    def test_pbs_script_without_queue(self):
        """Test PBS script generation without queue specification."""
        config = ClusterConfig()
        job_config = {"cores": 4, "memory": "8GB", "time": "02:00:00"}
        remote_job_dir = "/home/user/job"

        script = _create_pbs_script(job_config, remote_job_dir, config)

        assert "#PBS -q" not in script
        assert "#PBS -N clustrix" in script

    def test_pbs_script_with_modules_and_environment(self):
        """Test PBS script with modules and environment variables."""
        config = ClusterConfig()
        config.module_loads = ["intel/19.0", "mpi/3.1"]
        config.environment_variables = {"MPI_HOME": "/opt/mpi"}
        config.pre_execution_commands = ["ulimit -s unlimited"]
        job_config = {"cores": 16, "memory": "32GB", "time": "08:00:00"}
        remote_job_dir = "/scratch/mpi_job"

        script = _create_pbs_script(job_config, remote_job_dir, config)

        assert "module load intel/19.0" in script
        assert "module load mpi/3.1" in script
        assert "export MPI_HOME=/opt/mpi" in script
        assert "ulimit -s unlimited" in script
        assert "python execute_function.py" in script


class TestSgeScriptGeneration:
    """Test SGE-specific job script generation."""

    def test_sge_script_basic_configuration(self):
        """Test basic SGE script generation."""
        config = ClusterConfig()
        job_config = {"cores": 4, "memory": "8GB", "time": "02:00:00"}
        remote_job_dir = "/tmp/sge_job"

        script = _create_sge_script(job_config, remote_job_dir, config)

        assert "#$ -N clustrix" in script
        assert "#$ -pe smp 4" in script
        assert "#$ -l h_vmem=8GB" in script
        assert "#$ -l h_rt=02:00:00" in script
        assert "#$ -cwd" in script
        assert f"#$ -o {remote_job_dir}/job.out" in script
        assert f"#$ -e {remote_job_dir}/job.err" in script

    def test_sge_script_with_fallback_serialization(self):
        """Test SGE script includes comprehensive fallback serialization logic."""
        config = ClusterConfig()
        config.python_executable = "/usr/bin/python3"
        job_config = {"cores": 2, "memory": "4GB", "time": "01:00:00"}
        remote_job_dir = "/tmp/job"

        script = _create_sge_script(job_config, remote_job_dir, config)

        # Check for comprehensive deserialization attempts
        assert "import dill" in script
        assert "import cloudpickle" in script
        assert "dill.loads(data['function'])" in script
        assert "cloudpickle.loads(data['function'])" in script
        assert "function_source" in script
        assert "exec(source, namespace)" in script
        assert "Could not deserialize function" in script

    def test_sge_script_with_environment_setup(self):
        """Test SGE script with full environment setup."""
        config = ClusterConfig()
        config.module_loads = ["gcc/8.3", "python/3.8"]
        config.environment_variables = {"TMPDIR": "/local/tmp"}
        config.pre_execution_commands = ["echo 'SGE job starting'"]
        job_config = {"cores": 1, "memory": "2GB", "time": "00:30:00"}
        remote_job_dir = "/gridware/job"

        script = _create_sge_script(job_config, remote_job_dir, config)

        assert "module load gcc/8.3" in script
        assert "module load python/3.8" in script
        assert "export TMPDIR=/local/tmp" in script
        assert "echo 'SGE job starting'" in script


class TestSshScriptGeneration:
    """Test SSH-specific script generation."""

    def test_ssh_script_basic_configuration(self):
        """Test basic SSH script generation."""
        config = ClusterConfig()
        job_config = {"cores": 1, "memory": "2GB", "time": "00:30:00"}
        remote_job_dir = "/home/user/ssh_job"

        script = _create_ssh_script(job_config, remote_job_dir, config)

        assert "#!/bin/bash" in script
        assert f"cd {remote_job_dir}" in script
        assert "import pickle" in script
        assert "import sys" in script
        assert "import traceback" in script
        assert "Python version:" in script
        assert "Data keys:" in script

    def test_ssh_script_with_custom_python(self):
        """Test SSH script with custom Python executable."""
        config = ClusterConfig()
        config.python_executable = "/opt/anaconda3/bin/python"
        job_config = {"cores": 2, "memory": "4GB", "time": "01:00:00"}
        remote_job_dir = "/scratch/job"

        script = _create_ssh_script(job_config, remote_job_dir, config)

        assert "/opt/anaconda3/bin/python" in script

    def test_ssh_script_with_modules_and_commands(self):
        """Test SSH script with module loads and pre-execution commands."""
        config = ClusterConfig()
        config.module_loads = ["python/3.9", "cuda/11.2"]
        config.environment_variables = {"CUDA_DEVICE_ORDER": "PCI_BUS_ID"}
        config.pre_execution_commands = ["nvidia-smi", "df -h"]
        job_config = {"cores": 4, "memory": "8GB", "time": "02:00:00"}
        remote_job_dir = "/work/user/job"

        script = _create_ssh_script(job_config, remote_job_dir, config)

        assert "# Load required modules" in script
        assert "module load python/3.9" in script
        assert "module load cuda/11.2" in script
        assert "# Set environment variables" in script
        assert "export CUDA_DEVICE_ORDER=PCI_BUS_ID" in script
        assert "# Execute pre-execution commands" in script
        assert "nvidia-smi" in script
        assert "df -h" in script

    def test_ssh_script_comprehensive_deserialization(self):
        """Test SSH script includes comprehensive deserialization fallback."""
        config = ClusterConfig()
        job_config = {"cores": 1, "memory": "1GB", "time": "00:15:00"}
        remote_job_dir = "/tmp/test_job"

        script = _create_ssh_script(job_config, remote_job_dir, config)

        # Verify all fallback mechanisms are present
        assert "dill.loads(data['function'])" in script
        assert "cloudpickle.loads(data['function'])" in script
        assert "pickle.loads(data['function'])" in script
        assert "function_source" in script
        assert "exec(source, namespace)" in script
        assert "Dill deserialization failed:" in script
        assert "Cloudpickle deserialization failed:" in script
        assert "Pickle deserialization failed:" in script

    def test_ssh_script_with_two_venv_setup(self):
        """Test SSH script with two-venv configuration."""
        config = ClusterConfig()
        config.venv_info = {
            "conda_env1_name": "env1_test",
            "conda_env2_name": "env2_test",
        }
        job_config = {"cores": 1, "memory": "2GB", "time": "00:30:00"}
        remote_job_dir = "/tmp/two_venv_job"

        with patch("clustrix.utils.generate_two_venv_execution_commands") as mock_gen:
            mock_gen.return_value = [
                "conda activate env1_test",
                "python setup_venv1.py",
                "conda activate env2_test",
                "python execute_remote.py",
            ]

            script = _create_ssh_script(job_config, remote_job_dir, config)

            mock_gen.assert_called_once_with(remote_job_dir, "env1_test", "env2_test")


class TestGpuDetection:
    """Test GPU detection capabilities."""

    def test_detect_gpu_nvidia_smi_success(self):
        """Test successful GPU detection via nvidia-smi."""
        mock_ssh = Mock()

        # Mock successful nvidia-smi output
        mock_stdout = Mock()
        mock_stdout.read.return_value = (
            b"0, Tesla V100, 32768, 32000, 7.0\n1, Tesla V100, 32768, 31500, 7.0"
        )
        mock_stdout.channel.recv_exit_status.return_value = 0

        mock_stderr = Mock()
        mock_ssh.exec_command.return_value = (Mock(), mock_stdout, mock_stderr)

        gpu_info = detect_gpu_capabilities(mock_ssh)

        assert gpu_info["gpu_available"] is True
        assert gpu_info["gpu_count"] == 2
        assert gpu_info["detection_method"] == "nvidia-smi"
        assert len(gpu_info["gpu_devices"]) == 2

        # Check first device
        device0 = gpu_info["gpu_devices"][0]
        assert device0["index"] == 0
        assert device0["name"] == "Tesla V100"
        assert device0["memory_total_mb"] == 32768
        assert device0["memory_free_mb"] == 32000
        assert device0["compute_capability"] == "7.0"

    def test_detect_gpu_nvidia_smi_fails_fallback_nvcc(self):
        """Test GPU detection falls back to CUDA version check when nvidia-smi fails."""
        mock_ssh = Mock()

        # Mock nvidia-smi failure, nvcc success
        calls = [
            # nvidia-smi call (fails)
            (
                Mock(),
                Mock(
                    channel=Mock(recv_exit_status=Mock(return_value=1)),
                    read=Mock(return_value=b""),
                ),
                Mock(),
            ),
            # nvcc version call (succeeds)
            (
                Mock(),
                Mock(
                    channel=Mock(recv_exit_status=Mock(return_value=0)),
                    read=Mock(return_value=b"11.8"),
                ),
                Mock(),
            ),
            # /proc check call (will be made since gpu_available is still False)
            (
                Mock(),
                Mock(
                    channel=Mock(recv_exit_status=Mock(return_value=1)),
                    read=Mock(return_value=b""),
                ),
                Mock(),
            ),
            # lspci check call (final fallback)
            (
                Mock(),
                Mock(
                    channel=Mock(recv_exit_status=Mock(return_value=1)),
                    read=Mock(return_value=b""),
                ),
                Mock(),
            ),
        ]
        mock_ssh.exec_command.side_effect = calls

        gpu_info = detect_gpu_capabilities(mock_ssh)

        assert gpu_info["cuda_available"] is True
        assert gpu_info["cuda_version"] == "11.8"

        # Should have called all detection methods
        assert mock_ssh.exec_command.call_count == 4

    def test_detect_gpu_proc_driver_nvidia(self):
        """Test GPU detection via /proc/driver/nvidia."""
        mock_ssh = Mock()

        # Mock nvidia-smi and nvcc failure, /proc success
        calls = [
            # nvidia-smi fails
            (
                Mock(),
                Mock(
                    channel=Mock(recv_exit_status=Mock(return_value=1)),
                    read=Mock(return_value=b""),
                ),
                Mock(),
            ),
            # nvcc fails
            (
                Mock(),
                Mock(
                    channel=Mock(recv_exit_status=Mock(return_value=1)),
                    read=Mock(return_value=b""),
                ),
                Mock(),
            ),
            # /proc/driver/nvidia succeeds (4 entries: ., .., gpu0, gpu1)
            (
                Mock(),
                Mock(
                    channel=Mock(recv_exit_status=Mock(return_value=0)),
                    read=Mock(return_value=b"4"),
                ),
                Mock(),
            ),
        ]
        mock_ssh.exec_command.side_effect = calls

        gpu_info = detect_gpu_capabilities(mock_ssh)

        assert gpu_info["gpu_available"] is True
        assert gpu_info["gpu_count"] == 2  # 4 - 2 (for . and ..)
        assert gpu_info["detection_method"] == "/proc/driver/nvidia"

    def test_detect_gpu_lspci_fallback(self):
        """Test GPU detection via lspci as final fallback."""
        mock_ssh = Mock()

        # Mock all previous methods fail, lspci succeeds
        calls = [
            # nvidia-smi fails
            (
                Mock(),
                Mock(
                    channel=Mock(recv_exit_status=Mock(return_value=1)),
                    read=Mock(return_value=b""),
                ),
                Mock(),
            ),
            # nvcc fails
            (
                Mock(),
                Mock(
                    channel=Mock(recv_exit_status=Mock(return_value=1)),
                    read=Mock(return_value=b""),
                ),
                Mock(),
            ),
            # /proc fails
            (
                Mock(),
                Mock(
                    channel=Mock(recv_exit_status=Mock(return_value=1)),
                    read=Mock(return_value=b""),
                ),
                Mock(),
            ),
            # lspci succeeds (3 NVIDIA devices)
            (
                Mock(),
                Mock(
                    channel=Mock(recv_exit_status=Mock(return_value=0)),
                    read=Mock(return_value=b"3"),
                ),
                Mock(),
            ),
        ]
        mock_ssh.exec_command.side_effect = calls

        gpu_info = detect_gpu_capabilities(mock_ssh)

        assert gpu_info["gpu_available"] is True
        assert gpu_info["gpu_count"] == 3
        assert gpu_info["detection_method"] == "lspci"

    def test_detect_gpu_no_gpus_found(self):
        """Test when no GPUs are detected by any method."""
        mock_ssh = Mock()

        # Mock all detection methods fail but generate actual exceptions for error tracking
        def failing_exec_command(cmd):
            if "nvidia-smi" in cmd:
                raise Exception("nvidia-smi not found")
            elif "nvcc" in cmd:
                raise Exception("nvcc not available")
            elif "/proc/driver/nvidia" in cmd:
                raise Exception("proc check failed")
            elif "lspci" in cmd:
                raise Exception("lspci failed")
            else:
                mock_fail_stdout = Mock(
                    channel=Mock(recv_exit_status=Mock(return_value=1)),
                    read=Mock(return_value=b""),
                )
                return (Mock(), mock_fail_stdout, Mock())

        mock_ssh.exec_command.side_effect = failing_exec_command

        gpu_info = detect_gpu_capabilities(mock_ssh)

        assert gpu_info["gpu_available"] is False
        assert gpu_info["gpu_count"] == 0
        assert gpu_info["detection_method"] == "unknown"
        assert len(gpu_info["detection_errors"]) > 0

    def test_detect_gpu_with_config(self):
        """Test GPU detection with ClusterConfig parameter."""
        mock_ssh = Mock()
        config = ClusterConfig()

        # Mock successful detection
        mock_stdout = Mock()
        mock_stdout.read.return_value = b"0, RTX 3090, 24576, 24000, 8.6"
        mock_stdout.channel.recv_exit_status.return_value = 0
        mock_ssh.exec_command.return_value = (Mock(), mock_stdout, Mock())

        gpu_info = detect_gpu_capabilities(mock_ssh, config)

        assert gpu_info["gpu_available"] is True
        assert gpu_info["gpu_devices"][0]["name"] == "RTX 3090"


class TestGpuEnabledVenv2:
    """Test GPU-enabled VENV2 setup."""

    def test_setup_gpu_enabled_venv2_no_gpu(self):
        """Test VENV2 setup when no GPU is available."""
        mock_ssh = Mock()
        work_dir = "/scratch/test_job"
        requirements = {"numpy": "1.21.0", "scipy": "1.7.0"}
        gpu_info = {"gpu_available": False}

        result = setup_gpu_enabled_venv2(mock_ssh, work_dir, requirements, gpu_info)

        assert result["gpu_packages_installed"] is False
        assert result["cuda_support_added"] is False
        assert result["pytorch_gpu_installed"] is False
        assert result["tensorflow_gpu_installed"] is False

    def test_setup_gpu_enabled_venv2_with_pytorch(self):
        """Test VENV2 setup with PyTorch GPU support."""
        mock_ssh = Mock()
        work_dir = "/scratch/pytorch_job"
        requirements = {"torch": "1.12.0", "torchvision": "0.13.0"}
        gpu_info = {"gpu_available": True, "cuda_available": True}

        # Mock conda availability check
        mock_stdout = Mock()
        mock_stdout.read.return_value = b"conda 4.12.0"
        mock_ssh.exec_command.return_value = (Mock(), mock_stdout, Mock())

        result = setup_gpu_enabled_venv2(mock_ssh, work_dir, requirements, gpu_info)

        assert result["gpu_packages_installed"] is True
        assert result["pytorch_gpu_installed"] is True

    def test_setup_gpu_enabled_venv2_with_tensorflow(self):
        """Test VENV2 setup with TensorFlow GPU support."""
        mock_ssh = Mock()
        work_dir = "/scratch/tf_job"
        requirements = {"tensorflow": "2.9.0", "keras": "2.9.0"}
        gpu_info = {"gpu_available": True, "cuda_available": True}

        # Mock no conda available (use pip)
        mock_stdout = Mock()
        mock_stdout.read.return_value = b""
        mock_ssh.exec_command.return_value = (Mock(), mock_stdout, Mock())

        result = setup_gpu_enabled_venv2(mock_ssh, work_dir, requirements, gpu_info)

        assert result["gpu_packages_installed"] is True
        assert result["tensorflow_gpu_installed"] is True

    def test_setup_gpu_enabled_venv2_conda_installation(self):
        """Test conda-based GPU package installation."""
        mock_ssh = Mock()
        work_dir = "/scratch/conda_job"
        requirements = {"torch": "1.13.0", "cupy": "11.0.0"}
        gpu_info = {"gpu_available": True, "cuda_available": True}

        # Mock conda available
        mock_conda_stdout = Mock()
        mock_conda_stdout.read.return_value = b"conda 4.14.0"

        # Mock installation success
        mock_install_stdout = Mock()
        mock_install_stdout.channel.recv_exit_status.return_value = 0

        mock_ssh.exec_command.side_effect = [
            (Mock(), mock_conda_stdout, Mock()),  # conda version check
            (Mock(), mock_install_stdout, Mock()),  # installation
        ]

        result = setup_gpu_enabled_venv2(mock_ssh, work_dir, requirements, gpu_info)

        assert result["gpu_packages_installed"] is True
        assert mock_ssh.exec_command.call_count == 2

    def test_setup_gpu_enabled_venv2_pip_installation(self):
        """Test pip-based GPU package installation."""
        mock_ssh = Mock()
        work_dir = "/scratch/pip_job"
        requirements = {"jax": "0.3.15", "tensorflow": "2.10.0"}
        gpu_info = {"gpu_available": True, "cuda_available": True}

        # Mock no conda available
        mock_no_conda = Mock()
        mock_no_conda.read.return_value = b""

        # Mock installation success
        mock_install_stdout = Mock()
        mock_install_stdout.channel.recv_exit_status.return_value = 0

        mock_ssh.exec_command.side_effect = [
            (Mock(), mock_no_conda, Mock()),  # conda check fails
            (Mock(), mock_install_stdout, Mock()),  # pip installation
        ]

        result = setup_gpu_enabled_venv2(mock_ssh, work_dir, requirements, gpu_info)

        assert result["gpu_packages_installed"] is True

    def test_setup_gpu_enabled_venv2_with_scientific_packages(self):
        """Test CUDA support package installation with scientific libraries."""
        mock_ssh = Mock()
        work_dir = "/scratch/science_job"
        requirements = {
            "numpy": "1.21.0",
            "pandas": "1.3.0",
            "scikit-learn": "1.0.0",
            "scipy": "1.7.0",
        }
        gpu_info = {"gpu_available": True, "cuda_available": True}

        # Mock conda available
        mock_conda_stdout = Mock()
        mock_conda_stdout.read.return_value = b"conda 4.12.0"

        mock_install_stdout = Mock()
        mock_install_stdout.channel.recv_exit_status.return_value = 0

        mock_ssh.exec_command.side_effect = [
            (Mock(), mock_conda_stdout, Mock()),  # conda check
            (Mock(), mock_install_stdout, Mock()),  # installations
        ]

        result = setup_gpu_enabled_venv2(mock_ssh, work_dir, requirements, gpu_info)

        assert result["cuda_support_added"] is True

    def test_setup_gpu_enabled_venv2_installation_failure(self):
        """Test handling of package installation failures."""
        mock_ssh = Mock()
        work_dir = "/scratch/fail_job"
        requirements = {"torch": "1.12.0"}
        gpu_info = {"gpu_available": True}

        # Mock installation failure
        mock_conda_stdout = Mock()
        mock_conda_stdout.read.return_value = b"conda 4.12.0"

        mock_fail_stdout = Mock()
        mock_fail_stdout.channel.recv_exit_status.return_value = 1
        mock_fail_stderr = Mock()
        mock_fail_stderr.read.return_value = b"Installation failed: package not found"

        mock_ssh.exec_command.side_effect = [
            (Mock(), mock_conda_stdout, Mock()),  # conda check
            (Mock(), mock_fail_stdout, mock_fail_stderr),  # failed installation
        ]

        result = setup_gpu_enabled_venv2(mock_ssh, work_dir, requirements, gpu_info)

        assert len(result["installation_errors"]) > 0
        assert "Installation failed" in result["installation_errors"][0]


class TestEnhancedTwoVenvEnvironment:
    """Test enhanced two-venv environment setup with GPU integration."""

    @patch("clustrix.utils.detect_gpu_capabilities")
    @patch("clustrix.utils.setup_two_venv_environment")
    @patch("clustrix.utils.setup_gpu_enabled_venv2")
    def test_enhanced_setup_with_gpu_detection(
        self, mock_gpu_venv2, mock_two_venv, mock_gpu_detect
    ):
        """Test enhanced setup successfully detects GPUs and sets up GPU-enabled VENV2."""
        mock_ssh = Mock()
        work_dir = "/scratch/enhanced_job"
        requirements = {"torch": "1.13.0", "numpy": "1.21.0"}

        # Mock GPU detection success
        gpu_info = {
            "gpu_available": True,
            "gpu_count": 2,
            "detection_method": "nvidia-smi",
        }
        mock_gpu_detect.return_value = gpu_info

        # Mock basic two-venv setup
        venv_info = {
            "venv1_path": f"{work_dir}/venv1",
            "venv2_path": f"{work_dir}/venv2",
        }
        mock_two_venv.return_value = venv_info

        # Mock GPU-enabled VENV2 setup
        gpu_venv2_info = {"gpu_packages_installed": True, "pytorch_gpu_installed": True}
        mock_gpu_venv2.return_value = gpu_venv2_info

        # Test the function
        with patch("builtins.print") as mock_print:
            result = enhanced_setup_two_venv_environment(
                mock_ssh, work_dir, requirements
            )

        # Verify all components were called
        mock_gpu_detect.assert_called_once()
        mock_two_venv.assert_called_once()
        mock_gpu_venv2.assert_called_once()

        # Verify output includes GPU info and enhanced setup
        assert result["gpu_info"] == gpu_info
        assert result["gpu_packages_installed"] is True
        assert result["pytorch_gpu_installed"] is True

        # Check print statements
        mock_print.assert_any_call("Detecting GPU capabilities on remote cluster...")
        mock_print.assert_any_call("Setting up two-venv environment...")
        mock_print.assert_any_call(
            "GPU detected (2 devices), setting up GPU-enabled VENV2..."
        )

    @patch("clustrix.utils.detect_gpu_capabilities")
    @patch("clustrix.utils.setup_two_venv_environment")
    def test_enhanced_setup_no_gpu_detected(self, mock_two_venv, mock_gpu_detect):
        """Test enhanced setup when no GPUs are detected."""
        mock_ssh = Mock()
        work_dir = "/scratch/no_gpu_job"
        requirements = {"numpy": "1.21.0", "pandas": "1.3.0"}

        # Mock no GPU detection
        gpu_info = {
            "gpu_available": False,
            "gpu_count": 0,
            "detection_method": "unknown",
        }
        mock_gpu_detect.return_value = gpu_info

        # Mock basic two-venv setup
        venv_info = {
            "venv1_path": f"{work_dir}/venv1",
            "venv2_path": f"{work_dir}/venv2",
        }
        mock_two_venv.return_value = venv_info

        # Test the function
        with patch("builtins.print") as mock_print:
            result = enhanced_setup_two_venv_environment(
                mock_ssh, work_dir, requirements
            )

        # Verify components were called appropriately
        mock_gpu_detect.assert_called_once()
        mock_two_venv.assert_called_once()

        # Verify result structure
        assert result["gpu_info"] == gpu_info
        assert "gpu_packages_installed" not in result

        # Check print statements
        mock_print.assert_any_call("No GPUs detected, using standard VENV2 setup...")

    @patch("clustrix.utils.detect_gpu_capabilities")
    @patch("clustrix.utils.setup_two_venv_environment")
    def test_enhanced_setup_with_config(self, mock_two_venv, mock_gpu_detect):
        """Test enhanced setup with provided configuration."""
        mock_ssh = Mock()
        work_dir = "/scratch/config_job"
        requirements = {"tensorflow": "2.9.0"}
        config = ClusterConfig()

        mock_gpu_detect.return_value = {"gpu_available": False}
        mock_two_venv.return_value = {"venv1_path": f"{work_dir}/venv1"}

        result = enhanced_setup_two_venv_environment(
            mock_ssh, work_dir, requirements, config
        )

        # Should detect GPUs and setup venvs with provided config
        mock_gpu_detect.assert_called_once_with(mock_ssh, config)
        mock_two_venv.assert_called_once_with(mock_ssh, work_dir, requirements, config)

    @patch("clustrix.utils.detect_gpu_capabilities")
    @patch("clustrix.utils.setup_two_venv_environment")
    def test_enhanced_setup_no_config_provided(self, mock_two_venv, mock_gpu_detect):
        """Test enhanced setup when no config is provided (uses internal get_config)."""
        mock_ssh = Mock()
        work_dir = "/scratch/no_config_job"
        requirements = {"jax": "0.3.15"}

        mock_gpu_detect.return_value = {"gpu_available": True, "gpu_count": 1}
        mock_two_venv.return_value = {"venv1_path": f"{work_dir}/venv1"}

        with patch("clustrix.utils.setup_gpu_enabled_venv2") as mock_gpu_venv2:
            mock_gpu_venv2.return_value = {"gpu_packages_installed": True}

            result = enhanced_setup_two_venv_environment(
                mock_ssh, work_dir, requirements
            )

        # Should call detect_gpu_capabilities and setup_two_venv_environment
        # The get_config import is handled internally within the function
        mock_gpu_detect.assert_called_once()
        mock_two_venv.assert_called_once()
