#!/bin/bash
# Start script for mock SLURM services

# Start munge
service munge start

# Wait for munge to be ready
sleep 2

# Start SLURM controller daemon
/usr/sbin/slurmctld -D &

# Start SLURM daemon (simulating compute nodes)
/usr/sbin/slurmd -D &

# Keep container running
tail -f /var/log/slurm/*.log