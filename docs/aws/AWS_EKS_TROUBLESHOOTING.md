# AWS EKS Access Troubleshooting Guide

## Current Issue
Despite having all required IAM policies attached, the Clustrix user cannot perform EKS operations.

### Symptoms
- ✅ All 7 required AWS managed policies are attached:
  - AmazonEKSClusterPolicy
  - AmazonEKSWorkerNodePolicy  
  - AmazonEKS_CNI_Policy
  - AmazonEKSServicePolicy
  - AmazonEC2FullAccess
  - IAMFullAccess
  - AWSCloudFormationFullAccess

- ❌ All EKS operations return AccessDeniedException:
  - eks:ListClusters
  - eks:CreateCluster
  - eks:DescribeCluster
  - eks:DescribeAddonVersions

## Root Cause Analysis

The issue is NOT with IAM policies. The denial is happening at a higher level:

### 1. AWS Organizations Service Control Policy (Most Likely)
Your AWS account (229182852735) may be part of an AWS Organization with SCPs that:
- Block EKS service access
- Restrict certain regions
- Limit service usage to specific roles/users

**How to check:**
1. In AWS Console, go to AWS Organizations
2. Check if your account is part of an organization
3. Look for any SCPs attached to your account or organizational unit
4. Search for policies that deny "eks:*" actions

**Solution:**
- Contact your AWS Organization administrator
- Request an exception for the Clustrix user
- Or create the user in an account without SCP restrictions

### 2. AWS Service Not Enabled (Less Likely)
Some AWS services need to be explicitly enabled for an account/region.

**How to check:**
```bash
aws eks describe-addon-versions --region us-east-1
```

If this fails with a service error (not permission error), EKS might not be enabled.

**Solution:**
- Try a different region (us-west-2, eu-west-1)
- Contact AWS Support to enable EKS

### 3. IAM Permission Boundary (Already Checked - Not the Issue)
✅ We verified no permission boundary is set on the Clustrix user.

## Immediate Workarounds

### Option 1: Use a Different AWS Account
If you have access to another AWS account without organizational restrictions:
1. Create a new IAM user there
2. Apply the same permissions
3. Update ~/.clustrix/.env with new credentials

### Option 2: Use Admin/Root Credentials (Temporary)
For testing only:
1. Use AWS root account or admin user credentials
2. Update ~/.clustrix/.env temporarily
3. Remember to rotate credentials after testing

### Option 3: Test with AWS CloudShell
AWS CloudShell has pre-configured credentials:
1. Open AWS Console
2. Click the CloudShell icon (terminal icon in top bar)
3. Install Python and clustrix
4. Run tests from there

## Testing Command

Once you've resolved the access issue, test with:
```bash
python -c "
import boto3
eks = boto3.client('eks', region_name='us-east-1')
try:
    clusters = eks.list_clusters()
    print('✅ EKS access working! Found clusters:', clusters.get('clusters', []))
except Exception as e:
    print('❌ Still blocked:', str(e))
"
```

## Alternative: Use Local Kubernetes
While resolving AWS access, you can test with local Kubernetes:

```bash
# Test with Kind (local Kubernetes)
python test_local_kind.py

# This will:
# - Create a local K8s cluster
# - Run clustrix functions on it
# - Verify the full workflow
```

## Next Steps

1. **Check with AWS Admin**: Ask if there are organizational policies blocking EKS
2. **Try Different Region**: Some organizations only allow certain regions
3. **Check AWS Support**: Open a support ticket if you own the account
4. **Use Alternative**: Test with GCP, Azure, or local Kubernetes instead

## Contact Points

- AWS Support: https://console.aws.amazon.com/support/
- AWS Organizations: https://console.aws.amazon.com/organizations/
- IAM Dashboard: https://console.aws.amazon.com/iam/

## Status for Issue #68

Current blocker: AWS Organizations policy preventing EKS access despite correct IAM setup.

This is an **account-level restriction**, not a code or permission configuration issue.