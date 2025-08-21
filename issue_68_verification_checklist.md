# Issue #68: Kubernetes Auto-Provisioning - Verification Checklist

## ✅ COMPLETED & VERIFIED
1. **Credential Manager Priority Fix** (Commit: d6b4b7b)
   - [x] .env file loads first
   - [x] No 1Password popups
   - [x] AWS credentials load successfully

2. **Local Kubernetes (Kind)**
   - [x] LocalDockerKubernetesProvisioner class implemented
   - [x] Creates cluster successfully (~30 seconds)
   - [x] Returns proper cluster info

## 🔄 IMPLEMENTED BUT NOT VERIFIED

### AWS EKS Provisioning
**File**: `clustrix/kubernetes/aws_provisioner.py`
- [ ] VPC creation with CIDR 10.0.0.0/16
- [ ] 2 public subnets in different AZs
- [ ] 2 private subnets in different AZs
- [ ] Internet Gateway creation and attachment
- [ ] NAT Gateway creation (one per AZ)
- [ ] Route table configuration
- [ ] Security group with proper ingress/egress rules
- [ ] EKS cluster IAM role with correct policies
- [ ] Node group IAM role with correct policies
- [ ] EKS cluster creation
- [ ] Node group creation with specified instance types
- [ ] Kubeconfig retrieval
- [ ] Cluster readiness check
- [ ] Cost estimation accuracy ($0.10/hour control plane + nodes)
- [ ] Cluster destruction (all resources cleaned up)
- [ ] Error handling for partial failures

### GCP GKE Provisioning
**File**: `clustrix/kubernetes/gcp_provisioner.py`
- [ ] Project validation
- [ ] Enable required APIs (container, compute)
- [ ] Create GKE cluster
- [ ] Configure node pools
- [ ] Set up networking
- [ ] Retrieve credentials
- [ ] Cost estimation
- [ ] Cleanup

### Azure AKS Provisioning
**File**: `clustrix/kubernetes/azure_provisioner.py`
- [ ] Resource group creation
- [ ] Virtual network creation
- [ ] AKS cluster creation
- [ ] Node pool configuration
- [ ] Service principal setup
- [ ] Credentials retrieval
- [ ] Cost estimation
- [ ] Cleanup

### HuggingFace Endpoints
**File**: `clustrix/kubernetes/huggingface_provisioner.py`
- [ ] Inference endpoint creation
- [ ] Kubernetes integration
- [ ] Model deployment
- [ ] Scaling configuration
- [ ] Cost estimation
- [ ] Cleanup

### Lambda Cloud
**File**: `clustrix/kubernetes/lambda_provisioner.py`
- [ ] Instance provisioning
- [ ] Kubernetes installation
- [ ] GPU configuration (if applicable)
- [ ] Network setup
- [ ] Cost estimation
- [ ] Cleanup

## 📝 TEST PLAN

### Phase 1: AWS EKS (TODAY)
```bash
# 1. Test credentials without provisioning
python test_aws_without_1password.py

# 2. Test actual provisioning (COSTS MONEY)
python test_aws_eks_real.py

# 3. Verify cluster is running
kubectl get nodes

# 4. Test job execution
python -c "
from clustrix import cluster
@cluster(platform='kubernetes', auto_provision=True, provider='aws')
def test_job():
    return 'Hello from EKS!'
result = test_job()
print(result)
"

# 5. Destroy cluster
# Will be prompted in test script
```

### Phase 2: Fix Issues
- Document any errors
- Fix provisioning code
- Update all providers with fixes
- Re-test AWS

### Phase 3: Test Other Providers
- GCP GKE (need GOOGLE_APPLICATION_CREDENTIALS)
- Azure AKS (need Azure credentials)
- Local Kind (already verified ✅)

## 🚨 CRITICAL ITEMS TO VERIFY

1. **Resource Cleanup**
   - ALL resources must be destroyed
   - No orphaned VPCs, subnets, or IAM roles
   - Verify in AWS Console after destruction

2. **Cost Accuracy**
   - EKS control plane: $0.10/hour
   - t3.medium nodes: ~$0.0416/hour each
   - Total for 2 nodes: ~$0.18/hour
   - Verify estimation matches reality

3. **Error Recovery**
   - What happens if provisioning fails midway?
   - Are partial resources cleaned up?
   - Can we retry safely?

4. **IAM Permissions Required**
   Document minimum IAM permissions needed:
   - ec2:*
   - eks:*
   - iam:*
   - And others?

## 📊 VERIFICATION MATRIX

| Provider | Provisioning | Execution | Cleanup | Cost Est | 
|----------|-------------|-----------|---------|----------|
| AWS EKS  | ⏳ TODO     | ⏳ TODO   | ⏳ TODO | ⏳ TODO  |
| GCP GKE  | ⏳ TODO     | ⏳ TODO   | ⏳ TODO | ⏳ TODO  |
| Azure AKS| ⏳ TODO     | ⏳ TODO   | ⏳ TODO | ⏳ TODO  |
| HuggingFace| ⏳ TODO   | ⏳ TODO   | ⏳ TODO | ⏳ TODO  |
| Lambda   | ⏳ TODO     | ⏳ TODO   | ⏳ TODO | ⏳ TODO  |
| Local/Kind| ✅ DONE    | ✅ DONE   | ✅ DONE | ✅ FREE  |

## 🎯 SUCCESS CRITERIA

Issue #68 can be marked complete when:
1. [ ] At least 3 cloud providers tested and working
2. [ ] Cost estimation within 20% of actual
3. [ ] Clean destruction verified (no orphaned resources)
4. [ ] Error handling tested (partial failures)
5. [ ] Documentation updated with examples
6. [ ] CI/CD tests added (using Kind for free testing)

## 📝 NOTES
- Current AWS credentials are loaded successfully from ~/.clustrix/.env
- No 1Password popups (fixed in d6b4b7b)
- Ready to begin AWS EKS testing
- REMEMBER: Only mark as complete after MANUAL verification!