#!/usr/bin/env bash
#
# Build lambda-deployment.zip for upload to AWS Lambda.
#
# Usage:
#   ./build.sh
#
# Options (environment variables):
#   PYTHON_VERSION  Lambda runtime Python version (default: 3.12)
#   ARCH            Lambda architecture: x86_64 or aarch64 (default: x86_64)
#
# Example for a Python 3.13 / arm64 function:
#   PYTHON_VERSION=3.13 ARCH=aarch64 ./build.sh
#
# Dependencies are downloaded as Linux packages matching the Lambda runtime,
# so the zip works even when built on macOS.

set -euo pipefail

cd "$(dirname "$0")"

PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
ARCH="${ARCH:-x86_64}"
BUILD_DIR="build/package"
ZIP_FILE="lambda-deployment.zip"

echo "==> Cleaning previous build"
rm -rf build "$ZIP_FILE"
mkdir -p "$BUILD_DIR"

echo "==> Installing dependencies for Python $PYTHON_VERSION on Linux $ARCH"
python3 -m pip install \
    --requirement lambda/requirements.txt \
    --target "$BUILD_DIR" \
    --platform "manylinux2014_$ARCH" \
    --implementation cp \
    --python-version "$PYTHON_VERSION" \
    --only-binary=:all: \
    --disable-pip-version-check \
    --quiet

echo "==> Copying skill code"
cp lambda/lambda_function.py "$BUILD_DIR/"

# Compiled bytecode caches aren't needed and just bloat the zip
find "$BUILD_DIR" -type d -name __pycache__ -prune -exec rm -rf {} +

echo "==> Creating $ZIP_FILE"
(cd "$BUILD_DIR" && zip -qr9 "../../$ZIP_FILE" .)

echo "==> Done: $ZIP_FILE ($(du -h "$ZIP_FILE" | cut -f1))"
echo "    Upload it in the AWS Lambda console: Code > Upload from > .zip file"
