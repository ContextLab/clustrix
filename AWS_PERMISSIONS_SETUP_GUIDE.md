# AWS IAM Permissions Setup Guide for Clustrix

## Quick Setup via AWS Console (Recommended)

### Step 1: Open IAM Console
1. Go to: https://console.aws.amazon.com/iam/
2. Sign in with your AWS admin account (not the Clustrix user)

### Step 2: Find the Clustrix User
1. Click "Users" in the left sidebar
2. Search for "Clustrix"
3. Click on the "Clustrix" user

### Step 3: Add Permissions
1. Click the blue "Add permissions" button
2. Select "Attach policies directly"
3. Search for and check each of these policies:
   - [ ] `AmazonEKSClusterPolicy`
   - [ ] `AmazonEKSWorkerNodePolicy`
   - [ ] `AmazonEKS_CNI_Policy`
   - [ ] `AmazonEKSServicePolicy`
   - [ ] `AmazonEC2FullAccess`
   - [ ] `IAMFullAccess`
   - [ ] `AWSCloudFormationFullAccess`

4. Click "Next"
5. Review the policies
6. Click "Add permissions"

### Step 4: Verify Permissions
After adding, you should see these policies listed under "Permissions policies" for the Clustrix user.

## Alternative: Command Line Setup

If you have AWS CLI configured with admin credentials:

```bash
# Run the setup script
./setup_aws_permissions.sh
```

Or manually run these commands:
```bash
# Set the username
USER_NAME="Clustrix"

# Attach each policy
aws iam attach-user-policy --user-name $USER_NAME --policy-arn arn:aws:iam::aws:policy/AmazonEKSClusterPolicy
aws iam attach-user-policy --user-name $USER_NAME --policy-arn arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy
aws iam attach-user-policy --user-name $USER_NAME --policy-arn arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy
aws iam attach-user-policy --user-name $USER_NAME --policy-arn arn:aws:iam::aws:policy/AmazonEKSServicePolicy
aws iam attach-user-policy --user-name $USER_NAME --policy-arn arn:aws:iam::aws:policy/AmazonEC2FullAccess
aws iam attach-user-policy --user-name $USER_NAME --policy-arn arn:aws:iam::aws:policy/IAMFullAccess
aws iam attach-user-policy --user-name $USER_NAME --policy-arn arn:aws:iam::aws:policy/AWSCloudFormationFullAccess

# Verify
aws iam list-attached-user-policies --user-name $USER_NAME
```

## Testing Permissions

After adding permissions, test with:
```bash
python test_aws_preflight.py
```

You should see:
- ✅ Can read EC2 resources
- ✅ Can read EKS resources
- ✅ Can read IAM resources

## Security Notes

⚠️ **Important**: These permissions grant broad access to AWS resources. For production use:
1. Create a custom policy with only the minimum required permissions
2. Use resource-specific ARNs instead of "*"
3. Consider using temporary credentials via AWS STS
4. Enable MFA for the user

## Troubleshooting

### "Access Denied" Errors
- Make sure you're adding policies as an admin user, not as the Clustrix user itself
- Check that all 7 policies are attached
- Wait a few minutes for permissions to propagate

### "Invalid credentials" Errors
- The Clustrix user credentials in ~/.clustrix/.env are still valid
- You don't need to regenerate the access keys after adding permissions

### Testing Individual Services
Test each service separately:
```python
import boto3

# Use Clustrix credentials
creds = {
    'aws_access_key_id': 'YOUR_KEY',
    'aws_secret_access_key': 'YOUR_SECRET',
    'region_name': 'us-east-1'
}

# Test EC2
ec2 = boto3.client('ec2', **creds)
print("VPCs:", ec2.describe_vpcs()['Vpcs'][:1])

# Test EKS
eks = boto3.client('eks', **creds)
print("Clusters:", eks.list_clusters()['clusters'])

# Test IAM
iam = boto3.client('iam', **creds)
print("Roles:", len(iam.list_roles()['Roles']))
```

## Next Steps

Once permissions are set up:
1. Run pre-flight check: `python test_aws_preflight.py`
2. Test EKS provisioning: `python test_aws_eks_real.py`
3. Remember to destroy the cluster after testing to avoid charges!