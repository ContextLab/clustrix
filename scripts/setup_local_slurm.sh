#!/usr/bin/env bash
# Stand up a real single-node SLURM cluster, reachable over a real sshd, on
# one Ubuntu 24.04 machine or container. Run as root.
#
# This is what tests/real_world/test_local_sshd_slurm.py runs against. Every
# command below is one that worked; nothing here is aspirational.
#
#   sudo scripts/setup_local_slurm.sh           # install, configure, start
#   sudo scripts/setup_local_slurm.sh stop      # stop the daemons
#
# What it leaves behind:
#   - user "clx" with home /home/clx and an ed25519 key pair for logging in;
#     the private key is copied to $CLUSTRIX_TEST_KEY (default
#     /tmp/clustrix-local-cluster/id_ed25519)
#   - sshd on 127.0.0.1:$CLUSTRIX_TEST_SSH_PORT (default 2222), key auth only
#   - munged, mariadbd, slurmdbd, slurmctld, slurmd; one node, one partition
#     named "debug"; accounting through slurmdbd so that sacct works
#
# Then:
#   export CLUSTRIX_TEST_SSH_HOST=127.0.0.1 CLUSTRIX_TEST_SSH_PORT=2222 \
#          CLUSTRIX_TEST_SSH_USER=clx \
#          CLUSTRIX_TEST_SSH_KEY=/tmp/clustrix-local-cluster/id_ed25519
#   pytest tests/real_world/test_local_sshd_slurm.py -m real_world
set -euo pipefail

PORT="${CLUSTRIX_TEST_SSH_PORT:-2222}"
KEY="${CLUSTRIX_TEST_KEY:-/tmp/clustrix-local-cluster/id_ed25519}"
RUN_USER=clx
NODE="$(hostname -s)"
CPUS="$(nproc)"
MEM_MB="$(awk '/MemTotal/ {print int($2/1024) - 512}' /proc/meminfo)"

stop_all() {
    pkill -x slurmd || true
    pkill -x slurmctld || true
    pkill -x slurmdbd || true
    pkill -x munged || true
    pkill -f "sshd -f /etc/clustrix-test-sshd/sshd_config" || true
    pkill -x mariadbd || true
}

if [ "${1:-}" = "stop" ]; then
    stop_all
    exit 0
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq openssh-server slurm-wlm slurmdbd munge mariadb-server \
    python3-venv python3-pip

stop_all
sleep 1

# --- the account jobs run as -------------------------------------------------
id "$RUN_USER" >/dev/null 2>&1 || useradd -m -s /bin/bash "$RUN_USER"
# A locked password makes sshd refuse the account even for key auth.
usermod -p '*' "$RUN_USER"
install -d -m 700 -o "$RUN_USER" -g "$RUN_USER" "/home/$RUN_USER/.ssh"
mkdir -p "$(dirname "$KEY")"
if [ ! -f "$KEY" ]; then
    ssh-keygen -q -t ed25519 -N "" -f "$KEY"
fi
install -m 600 -o "$RUN_USER" -g "$RUN_USER" "$KEY.pub" \
    "/home/$RUN_USER/.ssh/authorized_keys"

# --- sshd --------------------------------------------------------------------
mkdir -p /etc/clustrix-test-sshd /run/sshd
[ -f /etc/clustrix-test-sshd/host_ed25519 ] || \
    ssh-keygen -q -t ed25519 -N "" -f /etc/clustrix-test-sshd/host_ed25519
cat > /etc/clustrix-test-sshd/sshd_config <<EOF
Port $PORT
ListenAddress 127.0.0.1
HostKey /etc/clustrix-test-sshd/host_ed25519
PidFile /run/clustrix-test-sshd.pid
PasswordAuthentication no
PubkeyAuthentication yes
AllowUsers $RUN_USER
UsePAM no
Subsystem sftp internal-sftp
EOF
/usr/sbin/sshd -f /etc/clustrix-test-sshd/sshd_config

# --- munge -------------------------------------------------------------------
[ -f /etc/munge/munge.key ] || dd if=/dev/urandom of=/etc/munge/munge.key bs=1024 count=1 status=none
chown munge:munge /etc/munge/munge.key
chmod 400 /etc/munge/munge.key
install -d -o munge -g munge -m 755 /run/munge
install -d -o munge -g munge -m 700 /var/lib/munge /var/log/munge
runuser -u munge -- /usr/sbin/munged --force

# --- accounting database -----------------------------------------------------
install -d -o mysql -g mysql /run/mysqld
[ -d /var/lib/mysql/mysql ] || mariadb-install-db --user=mysql >/dev/null
(runuser -u mysql -- /usr/sbin/mariadbd >/var/log/mariadbd-clustrix.log 2>&1 &)
for _ in $(seq 30); do
    mariadb -e "select 1" >/dev/null 2>&1 && break
    sleep 1
done
mariadb -e "CREATE DATABASE IF NOT EXISTS slurm_acct_db;
CREATE USER IF NOT EXISTS 'slurm'@'localhost' IDENTIFIED BY 'slurm-local-test';
GRANT ALL ON slurm_acct_db.* TO 'slurm'@'localhost'; FLUSH PRIVILEGES;"

install -d -o slurm -g slurm /var/spool/slurmctld /var/log/slurm /run/slurm
install -d /var/spool/slurmd

cat > /etc/slurm/slurmdbd.conf <<EOF
AuthType=auth/munge
DbdHost=localhost
SlurmUser=slurm
StorageType=accounting_storage/mysql
StorageHost=localhost
StorageUser=slurm
StoragePass=slurm-local-test
StorageLoc=slurm_acct_db
LogFile=/var/log/slurm/slurmdbd.log
PidFile=/run/slurm/slurmdbd.pid
EOF
chown slurm:slurm /etc/slurm/slurmdbd.conf
chmod 600 /etc/slurm/slurmdbd.conf

# --- slurm -------------------------------------------------------------------
# proctrack/linuxproc and task/none: no cgroup hierarchy is needed, which is
# what lets this run inside a container. select/cons_tres with CR_Core_Memory
# so --mem and --cpus-per-task are really enforced as allocations.
cat > /etc/slurm/slurm.conf <<EOF
ClusterName=clustrixlocal
SlurmctldHost=$NODE
AuthType=auth/munge
SlurmUser=slurm
SlurmctldPort=6817
SlurmdPort=6818
StateSaveLocation=/var/spool/slurmctld
SlurmdSpoolDir=/var/spool/slurmd
SlurmctldPidFile=/run/slurm/slurmctld.pid
SlurmdPidFile=/run/slurm/slurmd.pid
SlurmctldLogFile=/var/log/slurm/slurmctld.log
SlurmdLogFile=/var/log/slurm/slurmd.log
ProctrackType=proctrack/linuxproc
TaskPlugin=task/none
MpiDefault=none
ReturnToService=2
SchedulerType=sched/backfill
SelectType=select/cons_tres
SelectTypeParameters=CR_Core_Memory
AccountingStorageType=accounting_storage/slurmdbd
AccountingStorageHost=localhost
JobAcctGatherType=jobacct_gather/linux
JobAcctGatherFrequency=5
JobCompType=jobcomp/none
# Short kill grace so a --time overrun is visible in seconds, not minutes.
KillWait=5
OverTimeLimit=0
MinJobAge=30
NodeName=$NODE CPUs=$CPUS RealMemory=$MEM_MB State=UNKNOWN
PartitionName=debug Nodes=$NODE Default=YES MaxTime=INFINITE State=UP
EOF

runuser -u slurm -- /usr/sbin/slurmdbd
for _ in $(seq 30); do
    sacctmgr -n show cluster >/dev/null 2>&1 && break
    sleep 1
done
sacctmgr -i add cluster clustrixlocal >/dev/null 2>&1 || true
runuser -u slurm -- /usr/sbin/slurmctld
/usr/sbin/slurmd
for _ in $(seq 30); do
    sinfo -h -o '%T' 2>/dev/null | grep -q idle && break
    sleep 1
done
scontrol update nodename="$NODE" state=resume >/dev/null 2>&1 || true

# --- prove it ----------------------------------------------------------------
sinfo
SSH="ssh -i $KEY -p $PORT -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR $RUN_USER@127.0.0.1"
JOB="$($SSH 'cd ~ && sbatch --parsable --wrap hostname')"
for _ in $(seq 30); do
    STATE="$($SSH "sacct -j $JOB -X -n -o State" | tr -d ' ')"
    [ "$STATE" = "COMPLETED" ] && break
    sleep 1
done
echo "job $JOB: $STATE; output: $($SSH "cat ~/slurm-$JOB.out")"
[ "$STATE" = "COMPLETED" ]
echo "OK: sshd on 127.0.0.1:$PORT as $RUN_USER (key $KEY), SLURM partition 'debug' on $NODE"
