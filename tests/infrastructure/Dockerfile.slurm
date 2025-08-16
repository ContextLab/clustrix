# Dockerfile for mock SLURM environment
FROM ubuntu:22.04

# Install SLURM and dependencies
RUN apt-get update && apt-get install -y \
    slurm-wlm \
    munge \
    python3 \
    python3-pip \
    python3-venv \
    sudo \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create munge key
RUN mkdir -p /etc/munge && \
    dd if=/dev/urandom of=/etc/munge/munge.key bs=1 count=1024 && \
    chown munge:munge /etc/munge/munge.key && \
    chmod 400 /etc/munge/munge.key

# Create SLURM user and directories
RUN useradd -m -s /bin/bash slurm && \
    mkdir -p /var/spool/slurm/ctld /var/spool/slurm/d /var/log/slurm && \
    chown -R slurm:slurm /var/spool/slurm /var/log/slurm

# Copy SLURM configuration
COPY slurm.conf /etc/slurm/slurm.conf
COPY slurmdbd.conf /etc/slurm/slurmdbd.conf 2>/dev/null || true

# Create test user
RUN useradd -m -s /bin/bash testuser && \
    echo "testuser:testpass" | chpasswd

# Install Python packages
RUN python3 -m pip install \
    numpy \
    pandas \
    cloudpickle \
    dill

# Start script
COPY start-slurm.sh /usr/local/bin/start-slurm.sh
RUN chmod +x /usr/local/bin/start-slurm.sh

EXPOSE 6817 6818

CMD ["/usr/local/bin/start-slurm.sh"]