#!/bin/bash

# Script to set up AWS IAM permissions for Clustrix EKS provisioning
# Run this with AWS CLI configured with admin credentials

USER_NAME="Clustrix"
ACCOUNT_ID="229182852735"

echo "======================================================"
echo "AWS IAM Permission Setup for Clustrix EKS Provisioning"
echo "======================================================"
echo ""
echo "This script will add the necessary permissions to user: $USER_NAME"
echo "Account: $ACCOUNT_ID"
echo ""

# Check if AWS CLI is available
if ! command -v aws &> /dev/null; then
    echo "❌ AWS CLI not found. Please install it first:"
    echo "   brew install awscli"
    echo ""
    echo "Or add permissions manually in AWS Console:"
    echo "1. Go to: https://console.aws.amazon.com/iam/home#/users/Clustrix"
    echo "2. Click 'Add permissions' → 'Attach policies directly'"
    echo "3. Search and add these policies:"
    echo "   - AmazonEKSClusterPolicy"
    echo "   - AmazonEKSWorkerNodePolicy" 
    echo "   - AmazonEKS_CNI_Policy"
    echo "   - AmazonEC2FullAccess"
    echo "   - IAMFullAccess"
    echo ""
    exit 1
fi

echo "🔍 Checking current AWS credentials..."
CURRENT_USER=$(aws sts get-caller-identity --query 'Arn' --output text 2>/dev/null)

if [ $? -ne 0 ]; then
    echo "❌ AWS CLI not configured. Please run: aws configure"
    exit 1
fi

echo "Current AWS identity: $CURRENT_USER"
echo ""

# Check if we're using the Clustrix user (which won't have permissions) or an admin
if [[ $CURRENT_USER == *"user/Clustrix"* ]]; then
    echo "⚠️  You're currently using the Clustrix user which doesn't have permission to modify IAM."
    echo "Please configure AWS CLI with admin credentials:"
    echo "  aws configure --profile admin"
    echo "Then re-run this script with:"
    echo "  AWS_PROFILE=admin ./setup_aws_permissions.sh"
    exit 1
fi

echo "✅ Using credentials with IAM access"
echo ""

# Function to attach a policy
attach_policy() {
    local policy_arn=$1
    local policy_name=$2
    
    echo -n "  Adding $policy_name... "
    
    # Check if already attached
    aws iam list-attached-user-policies --user-name $USER_NAME \
        --query "AttachedPolicies[?PolicyArn=='$policy_arn'].PolicyArn" \
        --output text 2>/dev/null | grep -q "$policy_arn"
    
    if [ $? -eq 0 ]; then
        echo "✓ (already attached)"
    else
        aws iam attach-user-policy \
            --user-name $USER_NAME \
            --policy-arn $policy_arn 2>/dev/null
        
        if [ $? -eq 0 ]; then
            echo "✅"
        else
            echo "❌ Failed"
            FAILED=true
        fi
    fi
}

echo "📝 Attaching required AWS managed policies..."
echo ""

FAILED=false

# Core EKS policies
attach_policy "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy" "AmazonEKSClusterPolicy"
attach_policy "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy" "AmazonEKSWorkerNodePolicy"
attach_policy "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy" "AmazonEKS_CNI_Policy"
attach_policy "arn:aws:iam::aws:policy/AmazonEKSServicePolicy" "AmazonEKSServicePolicy"

# EC2 permissions for VPC/networking
attach_policy "arn:aws:iam::aws:policy/AmazonEC2FullAccess" "AmazonEC2FullAccess"

# IAM permissions for creating service roles
attach_policy "arn:aws:iam::aws:policy/IAMFullAccess" "IAMFullAccess"

# CloudFormation (EKS uses it internally)
attach_policy "arn:aws:iam::aws:policy/AWSCloudFormationFullAccess" "CloudFormation"

echo ""

if [ "$FAILED" = true ]; then
    echo "⚠️  Some policies failed to attach. Check error messages above."
else
    echo "✅ All policies attached successfully!"
fi

echo ""
echo "🔍 Verifying permissions..."
echo ""

# List attached policies
echo "Attached policies for user $USER_NAME:"
aws iam list-attached-user-policies --user-name $USER_NAME \
    --query 'AttachedPolicies[].PolicyName' --output text | tr '\t' '\n' | sed 's/^/  - /'

echo ""
echo "======================================================"
echo "Next steps:"
echo "1. Test permissions: python test_aws_preflight.py"
echo "2. If successful, provision cluster: python test_aws_eks_real.py"
echo "======================================================"