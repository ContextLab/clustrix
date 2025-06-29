from .decorator import cluster
from .config import configure, get_config
from .executor import ClusterExecutor
from .local_executor import LocalExecutor, create_local_executor
from .loop_analysis import detect_loops_in_function, find_parallelizable_loops
from .utils import setup_environment
from .ssh_utils import setup_ssh_keys, find_ssh_keys, list_ssh_keys
from .cost_monitoring import (
    cost_tracking_decorator,
    get_cost_monitor,
    start_cost_monitoring,
    generate_cost_report,
    get_pricing_info,
    ResourceUsage,
    CostEstimate,
    CostReport,
)

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
    "setup_ssh_keys",
    "find_ssh_keys",
    "list_ssh_keys",
    "cost_tracking_decorator",
    "get_cost_monitor",
    "start_cost_monitoring",
    "generate_cost_report",
    "get_pricing_info",
    "ResourceUsage",
    "CostEstimate",
    "CostReport",
]

# Auto-register IPython magic command and display widget if in notebook environment
try:
    from IPython import get_ipython

    ipython = get_ipython()
    if ipython is not None:
        from .notebook_magic import load_ipython_extension, auto_display_on_import

        # Load the magic command
        load_ipython_extension(ipython)

        # Auto-display widget if in notebook
        auto_display_on_import()
except (ImportError, AttributeError):
    # Not in IPython/Jupyter environment
    pass
