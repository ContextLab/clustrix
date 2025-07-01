#!/usr/bin/env python3
"""
Tutorial: Using Clustrix Filesystem Utilities

This tutorial demonstrates how to use Clustrix's unified filesystem utilities
for working with files both locally and on remote clusters.
"""

import sys
from pathlib import Path

# Add clustrix to path for examples
sys.path.insert(0, str(Path(__file__).parent.parent))

from clustrix import (
    cluster_ls,
    cluster_find,
    cluster_stat,
    cluster_exists,
    cluster_isdir,
    cluster_isfile,
    cluster_glob,
    cluster_du,
    cluster_count_files
)
from clustrix.config import ClusterConfig


def tutorial_local_operations():
    """Demonstrate filesystem operations on local files."""
    print("=" * 60)
    print("LOCAL FILESYSTEM OPERATIONS")
    print("=" * 60)
    
    # Configure for local operations
    config = ClusterConfig(
        cluster_type="local",
        local_work_dir="."  # Current directory
    )
    
    print("1. Listing directory contents:")
    files = cluster_ls(".", config)
    print(f"   Found {len(files)} items:")
    for file in files[:5]:  # Show first 5
        print(f"   - {file}")
    if len(files) > 5:
        print(f"   ... and {len(files) - 5} more")
    
    print("\n2. Finding Python files:")
    py_files = cluster_find("*.py", ".", config)
    print(f"   Found {len(py_files)} Python files:")
    for file in py_files[:3]:
        print(f"   - {file}")
    
    print("\n3. Checking file existence:")
    test_files = ["README.md", "setup.py", "nonexistent.txt"]
    for file in test_files:
        exists = cluster_exists(file, config)
        print(f"   {file}: {'EXISTS' if exists else 'NOT FOUND'}")
    
    print("\n4. Getting file information:")
    if py_files:
        file_info = cluster_stat(py_files[0], config)
        print(f"   File: {file_info.name}")
        print(f"   Size: {file_info.size:,} bytes")
        print(f"   Type: {'Directory' if file_info.is_dir else 'File'}")
        print(f"   Permissions: {file_info.permissions}")
        print(f"   Modified: {file_info.modified_datetime}")
    
    print("\n5. Using glob patterns:")
    patterns = ["*.py", "*.md", "*.txt", "test_*"]
    for pattern in patterns:
        matches = cluster_glob(pattern, ".", config)
        print(f"   Pattern '{pattern}': {len(matches)} matches")
    
    print("\n6. Counting files by type:")
    total_files = cluster_count_files(".", "*", config)
    py_count = cluster_count_files(".", "*.py", config)
    print(f"   Total files: {total_files}")
    print(f"   Python files: {py_count}")
    
    print("\n7. Directory usage:")
    usage = cluster_du(".", config)
    print(f"   Total size: {usage.total_mb:.1f} MB")
    print(f"   File count: {usage.file_count}")


def tutorial_remote_operations():
    """Demonstrate filesystem operations on remote cluster."""
    print("\n" + "=" * 60)
    print("REMOTE FILESYSTEM OPERATIONS")
    print("=" * 60)
    
    # Example remote configuration (adjust for your cluster)
    config = ClusterConfig(
        cluster_type="slurm",
        cluster_host="your-cluster.edu",
        username="your-username",
        # For demo, we'll use key-based auth
        # password="your-password",  # or use SSH keys
        remote_work_dir="/home/your-username"
    )
    
    print("NOTE: This section requires actual cluster credentials.")
    print("Update the config above with your cluster details to test.\n")
    
    print("Example remote operations (same API as local):")
    
    print("1. List remote home directory:")
    print("   files = cluster_ls('.', config)")
    
    print("\n2. Find data files on cluster:")
    print("   data_files = cluster_find('*.csv', 'data/', config)")
    
    print("\n3. Check if dataset exists:")
    print("   if cluster_exists('large_dataset.h5', config):")
    print("       print('Dataset found!')")
    
    print("\n4. Get remote file info:")
    print("   file_info = cluster_stat('results/output.txt', config)")
    print("   print(f'Output size: {file_info.size} bytes')")
    
    print("\n5. Count processed files:")
    print("   processed = cluster_count_files('results/', '*.json', config)")
    print("   print(f'Processed {processed} files')")


def tutorial_data_workflow():
    """Demonstrate a typical data processing workflow."""
    print("\n" + "=" * 60)
    print("DATA PROCESSING WORKFLOW")
    print("=" * 60)
    
    print("Example: Processing datasets with @cluster decorator")
    print()
    
    # Show example code (not executed)
    workflow_code = '''
from clustrix import cluster

@cluster(cores=8, memory="16GB")
def process_dataset(config):
    """Process dataset files on remote cluster."""
    import pandas as pd
    from clustrix import cluster_glob, cluster_du, cluster_stat
    
    # 1. Find all CSV files in data directory
    data_files = cluster_glob("*.csv", "data/", config)
    print(f"Found {len(data_files)} CSV files to process")
    
    # 2. Check available space
    usage = cluster_du("data/", config)
    print(f"Dataset size: {usage.total_gb:.2f} GB")
    
    results = []
    for filename in data_files:  # This loop gets parallelized automatically!
        # 3. Check file size before processing
        file_info = cluster_stat(filename, config)
        
        if file_info.size > 100_000_000:  # > 100MB
            print(f"Processing large file: {filename}")
            # Use chunked processing for large files
            df = pd.read_csv(filename, chunksize=10000)
            result = process_large_file(df)
        else:
            # Process smaller files normally
            df = pd.read_csv(filename)
            result = process_small_file(df)
        
        results.append(result)
    
    return results

# Usage
config = ClusterConfig(
    cluster_type="slurm",
    cluster_host="cluster.edu",
    username="researcher",
    remote_work_dir="/scratch/datasets"
)

# This will run on the cluster with automatic loop parallelization
results = process_dataset(config)
'''
    
    print(workflow_code)
    
    print("\nKey benefits of filesystem utilities:")
    print("• Same API works locally and remotely")
    print("• Automatic SSH connection management")
    print("• Works with @cluster decorator for seamless workflows")
    print("• Enables data-driven cluster computing")


def tutorial_advanced_patterns():
    """Show advanced usage patterns."""
    print("\n" + "=" * 60)
    print("ADVANCED PATTERNS")
    print("=" * 60)
    
    advanced_code = '''
# Pattern 1: Conditional processing based on file existence
@cluster
def smart_processing(config):
    # Check if results already exist
    if cluster_exists("results/final_output.json", config):
        print("Results already computed, loading...")
        return load_existing_results(config)
    else:
        print("Computing new results...")
        return compute_new_results(config)

# Pattern 2: Monitoring disk usage during processing
@cluster
def monitored_processing(config):
    initial_usage = cluster_du(".", config)
    print(f"Starting with {initial_usage.total_gb:.1f} GB")
    
    # Process files
    results = heavy_computation(config)
    
    final_usage = cluster_du(".", config)
    added_data = final_usage.total_gb - initial_usage.total_gb
    print(f"Generated {added_data:.1f} GB of new data")
    
    return results

# Pattern 3: Adaptive processing based on file counts
@cluster
def adaptive_processing(config):
    # Count input files
    total_files = cluster_count_files("input/", "*.dat", config)
    
    if total_files > 1000:
        # Use batch processing for large datasets
        return batch_process_files(config)
    else:
        # Use sequential processing for smaller datasets
        return sequential_process_files(config)

# Pattern 4: Data validation workflow
@cluster  
def validate_and_process(config):
    # 1. Find all input files
    input_files = cluster_find("*.raw", "input/", config)
    
    # 2. Validate each file
    valid_files = []
    for filename in input_files:
        file_info = cluster_stat(filename, config)
        
        # Check file size and age
        if file_info.size > 1000 and file_info.modified_timestamp > cutoff_time:
            valid_files.append(filename)
    
    print(f"Validated {len(valid_files)} out of {len(input_files)} files")
    
    # 3. Process only valid files
    return process_files(valid_files, config)
'''
    
    print(advanced_code)


def main():
    """Run the complete tutorial."""
    print("CLUSTRIX FILESYSTEM UTILITIES TUTORIAL")
    print("This tutorial shows how to use unified filesystem operations")
    print("that work seamlessly with both local and remote clusters.\n")
    
    # Run local examples (these will actually work)
    tutorial_local_operations()
    
    # Show remote examples (documentation)
    tutorial_remote_operations()
    
    # Show workflow examples
    tutorial_data_workflow()
    
    # Show advanced patterns
    tutorial_advanced_patterns()
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print("The filesystem utilities provide:")
    print("• Unified API for local and remote operations")
    print("• Seamless integration with @cluster decorator")
    print("• Automatic SSH connection management")
    print("• Data-driven cluster computing capabilities")
    print("• Support for complex data processing workflows")
    print("\nNext steps:")
    print("• Configure your cluster credentials")
    print("• Try the remote operations examples")
    print("• Integrate with your existing @cluster functions")
    print("• Explore the technical design document for Phase 2 features")


if __name__ == "__main__":
    main()