from .decorator import cluster
from .config import configure, get_config
from .executor import ClusterExecutor
from .local_executor import LocalExecutor, create_local_executor
from .loop_analysis import detect_loops_in_function, find_parallelizable_loops
from .utils import setup_environment
from .ssh_utils import (
    setup_ssh_keys,
    setup_ssh_keys_with_fallback,
    find_ssh_keys,
    list_ssh_keys,
    add_host_key,
)
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
from .filesystem import (
    ClusterFilesystem,
    FileInfo,
    DiskUsage,
    cluster_ls,
    cluster_find,
    cluster_stat,
    cluster_exists,
    cluster_isdir,
    cluster_isfile,
    cluster_glob,
    cluster_du,
    cluster_count_files,
)
from .dependency_analysis import (
    DependencyAnalyzer,
    DependencyGraph,
    LoopAnalyzer,
    FilesystemCall,
    ImportInfo,
    LocalFunctionCall,
    FileReference,
    analyze_function_dependencies,
    analyze_function_loops,
)
from .file_packaging import (
    FilePackager,
    PackageInfo,
    ExecutionContext,
    create_execution_context,
    package_function_for_execution,
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
    "add_host_key",
    "cost_tracking_decorator",
    "get_cost_monitor",
    "start_cost_monitoring",
    "generate_cost_report",
    "get_pricing_info",
    "ResourceUsage",
    "CostEstimate",
    "CostReport",
    "ClusterFilesystem",
    "FileInfo",
    "DiskUsage",
    "cluster_ls",
    "cluster_find",
    "cluster_stat",
    "cluster_exists",
    "cluster_isdir",
    "cluster_isfile",
    "cluster_glob",
    "cluster_du",
    "cluster_count_files",
    "DependencyAnalyzer",
    "DependencyGraph",
    "LoopAnalyzer",
    "FilesystemCall",
    "ImportInfo",
    "LocalFunctionCall",
    "FileReference",
    "analyze_function_dependencies",
    "analyze_function_loops",
    "FilePackager",
    "PackageInfo",
    "ExecutionContext",
    "create_execution_context",
    "package_function_for_execution",
]

# Auto-register IPython magic command and display widget if in notebook environment
try:
    from IPython import get_ipython

    ipython = get_ipython()
    if ipython is not None:
        from .notebook_magic import load_ipython_extension, auto_display_on_import

        # Load the magic command (only register once)
        if not hasattr(ipython, "_clustrix_magic_loaded"):
            load_ipython_extension(ipython)
            ipython._clustrix_magic_loaded = True

        # Always auto-display widget if in notebook (even on re-imports)
        auto_display_on_import()
except (ImportError, AttributeError):
    # Not in IPython/Jupyter environment
    pass
