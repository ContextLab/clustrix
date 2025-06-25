from .decorator import cluster
from .config import configure, get_config
from .executor import ClusterExecutor
from .local_executor import LocalExecutor, create_local_executor
from .loop_analysis import detect_loops_in_function, find_parallelizable_loops
from .utils import setup_environment

__version__ = "0.1.0"
__all__ = [
    "cluster",
    "configure",
    "get_config",
    "ClusterExecutor",
    "LocalExecutor",
    "create_local_executor",
    "detect_loops_in_function",
    "find_parallelizable_loops",
    "setup_environment",
]
