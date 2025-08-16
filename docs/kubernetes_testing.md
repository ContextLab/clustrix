# Kubernetes Real-World Testing Guide

This guide explains how to set up and run real-world Kubernetes validation tests for Clustrix (Issue #63 Phase 2).

## Overview

The Kubernetes test suite (`tests/real_world/test_kubernetes_comprehensive.py`) validates:

- ✅ Job submission and execution
- ✅ Container-based Python function execution  
- ✅ Resource specification and limits
- ✅ Error handling and recovery
- ✅ Concurrent job execution
- ✅ Status tracking and monitoring
- ✅ Job cleanup and TTL management
- ✅ Dependency handling within containers

## Prerequisites

### Required Dependencies
```bash
pip install kubernetes  # Kubernetes Python client
pip install clustrix[kubernetes]  # If using optional dependencies
```

### Kubernetes Cluster Options

#### Option 1: Local Development Clusters

**minikube** (Recommended for local testing):
```bash
# Install minikube
curl -LO https://storage.googleapis.com/minikube/releases/latest/minikube-darwin-amd64
sudo install minikube-darwin-amd64 /usr/local/bin/minikube

# Start cluster
minikube start --driver=docker
minikube kubectl -- config view  # Verify kubeconfig
```

**Docker Desktop Kubernetes**:
```bash
# Enable in Docker Desktop settings
# Kubernetes > Enable Kubernetes > Apply & Restart
kubectl config use-context docker-desktop
```

**kind** (Kubernetes in Docker):
```bash
# Install kind
go install sigs.k8s.io/kind@latest

# Create cluster
kind create cluster --name clustrix-test
kubectl config use-context kind-clustrix-test
```

#### Option 2: Cloud Kubernetes Services

**AWS EKS**:
```bash
# Install eksctl
brew install eksctl

# Create cluster
eksctl create cluster --name clustrix-test --region us-west-2 --nodegroup-name standard-nodes --node-type t3.medium --nodes 2

# Update kubeconfig
aws eks update-kubeconfig --region us-west-2 --name clustrix-test
```

**Google GKE**:
```bash
# Install gcloud CLI
# https://cloud.google.com/sdk/docs/install

# Create cluster
gcloud container clusters create clustrix-test --zone us-central1-a --num-nodes 2

# Update kubeconfig
gcloud container clusters get-credentials clustrix-test --zone us-central1-a
```

**Azure AKS**:
```bash
# Install Azure CLI
# https://docs.microsoft.com/en-us/cli/azure/install-azure-cli

# Create resource group
az group create --name clustrix-test --location eastus

# Create AKS cluster
az aks create --resource-group clustrix-test --name clustrix-test --node-count 2 --enable-addons monitoring --generate-ssh-keys

# Update kubeconfig
az aks get-credentials --resource-group clustrix-test --name clustrix-test
```

## Configuration

### Method 1: Local kubeconfig (Recommended)

Ensure your `~/.kube/config` points to a working cluster:

```bash
kubectl cluster-info  # Verify cluster access
kubectl get nodes     # Check node status
```

Set optional environment variables:
```bash
export K8S_NAMESPACE=default           # Kubernetes namespace (default: default)
export K8S_IMAGE=python:3.11-slim     # Container image (default: python:3.11-slim)
export K8S_CONTEXT=my-cluster         # Specific context (optional)
```

### Method 2: 1Password Integration

Add Kubernetes credentials to 1Password:

**Item Name**: `clustrix-kubernetes-validation`

**Fields**:
- `kubeconfig`: Complete kubeconfig file content
- `namespace`: Kubernetes namespace (optional, defaults to 'default')  
- `context`: Specific context to use (optional)

### Method 3: GitHub Actions / CI

Set repository secrets:
- `KUBECONFIG_CONTENT`: Base64-encoded kubeconfig file
- `K8S_NAMESPACE`: Kubernetes namespace
- `K8S_CONTEXT`: Specific context (optional)

```yaml
# GitHub Actions example
env:
  KUBECONFIG_CONTENT: ${{ secrets.KUBECONFIG_CONTENT }}
  K8S_NAMESPACE: clustrix-test
```

### Method 4: In-Cluster (Pod-based testing)

When running inside a Kubernetes cluster, the tests automatically detect the in-cluster service account token at `/var/run/secrets/kubernetes.io/serviceaccount/token`.

## Running Tests

### Individual Test Cases
```bash
# Basic functionality
python -m pytest tests/real_world/test_kubernetes_comprehensive.py::TestKubernetesComprehensive::test_kubernetes_simple_function_execution -v

# Error handling
python -m pytest tests/real_world/test_kubernetes_comprehensive.py::TestKubernetesComprehensive::test_kubernetes_error_handling_and_recovery -v

# Resource limits
python -m pytest tests/real_world/test_kubernetes_comprehensive.py::TestKubernetesComprehensive::test_kubernetes_resource_specification -v

# Concurrent jobs
python -m pytest tests/real_world/test_kubernetes_comprehensive.py::TestKubernetesComprehensive::test_kubernetes_concurrent_jobs -v
```

### Full Test Suite
```bash
# All Kubernetes tests
python -m pytest tests/real_world/test_kubernetes_comprehensive.py -v -m real_world

# With detailed logging
python -m pytest tests/real_world/test_kubernetes_comprehensive.py -v -m real_world -s --log-cli-level=INFO
```

### Test Behavior

**With Kubernetes Access**: Tests run against real cluster
**Without Kubernetes Access**: Tests are automatically skipped

## Troubleshooting

### Common Issues

#### "Kubernetes cluster not available"
```bash
# Check kubeconfig
kubectl config current-context
kubectl cluster-info

# Verify permissions
kubectl auth can-i create jobs
kubectl auth can-i create pods
kubectl auth can-i get pods
```

#### "kubernetes package required"
```bash
pip install kubernetes
```

#### "Permission denied" errors
```bash
# Check RBAC permissions
kubectl get clusterrolebinding
kubectl describe clusterrolebinding cluster-admin

# Create service account with job permissions if needed
kubectl create serviceaccount clustrix-test
kubectl create clusterrolebinding clustrix-test --clusterrole=cluster-admin --serviceaccount=default:clustrix-test
```

#### Pod fails with "ImagePullBackOff"
```bash
# Check if python:3.11-slim is accessible
kubectl run test-pod --image=python:3.11-slim --rm -it --restart=Never -- python --version

# Use alternative image if needed
export K8S_IMAGE=python:3.9-slim
```

#### Jobs not cleaning up
```bash
# Manual cleanup
kubectl delete jobs -l app=clustrix
kubectl delete pods -l job-name --field-selector=status.phase=Succeeded
```

### Debugging Failed Tests

#### Check job status
```bash
kubectl get jobs
kubectl describe job <job-name>
```

#### Check pod logs
```bash
kubectl get pods -l job-name=<job-name>
kubectl logs <pod-name>
```

#### Check pod events
```bash
kubectl describe pod <pod-name>
```

## Test Structure

Each test follows this pattern:

1. **Setup**: Create `ClusterConfig` with Kubernetes parameters
2. **Submit**: Submit Python function as Kubernetes Job
3. **Monitor**: Track job status through Kubernetes API
4. **Retrieve**: Parse results from pod logs (`CLUSTRIX_RESULT:` markers)
5. **Cleanup**: Delete job and associated resources
6. **Verify**: Assert expected outcomes

## Integration with Issue #63

The Kubernetes test suite addresses **Phase 2** of the external service validation tracker:

- ✅ **Real Cluster Integration**: No mock tests, only actual Kubernetes clusters
- ✅ **Container Execution**: Validates containerized Python function execution
- ✅ **Resource Management**: Tests CPU/memory limits and requests
- ✅ **Error Handling**: Comprehensive failure scenario testing
- ✅ **Status Monitoring**: Real-time job status tracking via K8s API
- ✅ **Cleanup Validation**: TTL and manual cleanup verification

This provides complete validation of Clustrix's Kubernetes integration without relying on mock objects or simulations.