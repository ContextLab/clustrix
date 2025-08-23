# Add Custom EKS Policy for Clustrix User

## The Problem
The AWS managed EKS policies (like `AmazonEKSClusterPolicy`) are designed for service roles, not IAM users. They don't grant permissions like `eks:ListClusters` or `eks:CreateCluster` that users need.

## The Solution
Add a custom inline policy or use PowerUserAccess. Here are two options:

## Option 1: Add Custom Inline Policy (Recommended)

### Via AWS Console:
1. Go to: https://console.aws.amazon.com/iam/home#/users/Clustrix?section=permissions
2. Click "Add permissions" → "Create inline policy"
3. Click "JSON" tab
4. Paste this policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "eks:*"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "iam:CreateServiceLinkedRole"
      ],
      "Resource": "arn:aws:iam::*:role/aws-service-role/eks*.amazonaws.com/*",
      "Condition": {
        "StringLike": {
          "iam:AWSServiceName": "eks*.amazonaws.com"
        }
      }
    }
  ]
}
```

5. Click "Review policy"
6. Name it: `ClustrixEKSUserAccess`
7. Click "Create policy"

### Via AWS CLI:
```bash
aws iam put-user-policy \
  --user-name Clustrix \
  --policy-name ClustrixEKSUserAccess \
  --policy-document file://eks_user_policy.json
```

## Option 2: Add PowerUserAccess (Simpler but Broader)

### Via AWS Console:
1. Go to: https://console.aws.amazon.com/iam/home#/users/Clustrix?section=permissions
2. Click "Add permissions" → "Attach policies directly"
3. Search for: `PowerUserAccess`
4. Check the box
5. Click "Next" → "Add permissions"

### Via AWS CLI:
```bash
aws iam attach-user-policy \
  --user-name Clustrix \
  --policy-arn arn:aws:iam::aws:policy/PowerUserAccess
```

## After Adding the Policy

Test that it works:
```bash
python test_aws_preflight.py
```

You should see:
- ✅ Can list EKS clusters
- ✅ Can create EKS clusters
- ✅ Ready to provision

## Why This Is Needed

The AWS managed policies like `AmazonEKSClusterPolicy` are designed for the EKS service itself to assume, not for IAM users. They grant permissions the service needs to manage resources on your behalf, but don't grant users permission to create or list clusters.

For users to manage EKS clusters, they need explicit `eks:*` permissions, which the custom policy provides.