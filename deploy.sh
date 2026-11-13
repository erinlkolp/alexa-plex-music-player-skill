#!/usr/bin/env bash
#
# Build lambda-deployment.zip and upload it to an existing AWS Lambda function.
#
# Usage:
#   ./deploy.sh
#
# Options (environment variables):
#   FUNCTION_NAME   Lambda function to update (default: PlexAlexaMusicSkill)
#   PYTHON_VERSION  Passed through to build.sh (default: 3.12)
#   ARCH            Passed through to build.sh (default: x86_64)
#   AWS_PROFILE     AWS CLI profile to use (default: your default profile)
#   AWS_REGION      Region the function lives in (default: your CLI's configured region)
#
# Requires the AWS CLI, configured with credentials allowed to update the function:
#   brew install awscli && aws configure

set -euo pipefail

cd "$(dirname "$0")"

FUNCTION_NAME="${FUNCTION_NAME:-PlexAlexaMusicSkill}"
ZIP_FILE="lambda-deployment.zip"

if ! command -v aws >/dev/null 2>&1; then
    echo "Error: the AWS CLI isn't installed. Install it with 'brew install awscli', then run 'aws configure'." >&2
    exit 1
fi

./build.sh

echo "==> Uploading $ZIP_FILE to Lambda function '$FUNCTION_NAME'"
aws lambda update-function-code \
    --function-name "$FUNCTION_NAME" \
    --zip-file "fileb://$ZIP_FILE" \
    --query 'LastModified' \
    --output text \
    --no-cli-pager

echo "==> Waiting for the update to finish"
aws lambda wait function-updated --function-name "$FUNCTION_NAME"

echo "==> Deployed to '$FUNCTION_NAME'"
