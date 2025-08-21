#!/bin/bash

# Script to add custom EKS user policy to Clustrix IAM user

USER_NAME="Clustrix"
POLICY_NAME="ClustrixEKSUserAccess"

echo "========================================"
echo "Adding Custom EKS Policy for User Access"
echo "========================================"
echo ""
echo "This will add a custom inline policy to grant EKS permissions"
echo "to the $USER_NAME IAM user."
echo ""

# Check if AWS CLI is available
if ! command -v aws &> /dev/null; then
    echo "❌ AWS CLI not found. Please install it first:"
    echo "   brew install awscli"
    exit 1
fi

# Check current user
echo "Checking AWS credentials..."
CURRENT_USER=$(aws sts get-caller-identity --query 'Arn' --output text 2>/dev/null)

if [ $? -ne 0 ]; then
    echo "❌ AWS CLI not configured. Please run: aws configure"
    exit 1
fi

echo "Using: $CURRENT_USER"

# Check if we're using the Clustrix user
if [[ $CURRENT_USER == *"user/Clustrix"* ]]; then
    echo "⚠️  You're using the Clustrix user which can't modify its own permissions."
    echo "Please use admin credentials: aws configure --profile admin"
    echo "Then run: AWS_PROFILE=admin ./add_eks_user_policy.sh"
    exit 1
fi

echo ""
echo "Adding inline policy to grant EKS permissions..."

# Add the inline policy
aws iam put-user-policy \
    --user-name $USER_NAME \
    --policy-name $POLICY_NAME \
    --policy-document file://eks_user_policy.json

if [ $? -eq 0 ]; then
    echo "✅ Policy added successfully!"
else
    echo "❌ Failed to add policy. Check error above."
    exit 1
fi

echo ""
echo "Verifying the policy was added..."

# List inline policies
policies=$(aws iam list-user-policies --user-name $USER_NAME --query 'PolicyNames' --output text)

if [[ $policies == *"$POLICY_NAME"* ]]; then
    echo "✅ Confirmed: $POLICY_NAME is attached to $USER_NAME"
else
    echo "⚠️  Policy may not have been attached correctly"
fi

echo ""
echo "========================================"
echo "Next steps:"
echo "1. Test with: python test_aws_preflight.py"
echo "2. If successful, provision with: python test_aws_eks_real.py"
echo "========================================"