# AWS Console Quick Steps - Add Permissions to Clustrix User

## 🚀 Quick Steps (5 minutes)

### 1. Open this link in your browser:
**https://console.aws.amazon.com/iam/home#/users/Clustrix?section=permissions**

(This goes directly to the Clustrix user's permissions page)

### 2. Click the blue "Add permissions" button

### 3. Select "Attach policies directly"

### 4. In the search box, type each policy name and check it:

Copy-paste these one at a time into the search box:

```
AmazonEKSClusterPolicy
```
✓ Check the box next to it

```
AmazonEKSWorkerNodePolicy
```
✓ Check the box next to it

```
AmazonEKS_CNI_Policy
```
✓ Check the box next to it

```
AmazonEKSServicePolicy
```
✓ Check the box next to it

```
AmazonEC2FullAccess
```
✓ Check the box next to it

```
IAMFullAccess
```
✓ Check the box next to it

```
AWSCloudFormationFullAccess
```
✓ Check the box next to it

### 5. Click "Next" button at the bottom

### 6. Click "Add permissions" button

## ✅ Done!

Now test that it worked:
```bash
python tests/integration/test_aws_preflight.py
```

You should see:
- ✅ Can read EC2 resources
- ✅ Can read EKS resources  
- ✅ Can read IAM resources

## 📝 What We Just Did

We gave the Clustrix user permission to:
- Create and manage EKS clusters
- Create VPCs, subnets, and networking
- Create IAM roles for the cluster
- Use CloudFormation (EKS uses it internally)

## ⚠️ Important
These are broad permissions for testing. For production, create a custom policy with only what's needed.

## 🧹 Cleanup After Testing
Remember to destroy any test clusters to avoid charges:
- EKS clusters cost $0.10/hour just for existing
- EC2 instances and NAT gateways add more costs