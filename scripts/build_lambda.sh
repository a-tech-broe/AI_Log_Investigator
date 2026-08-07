#!/usr/bin/env bash
# Build a reproducible Lambda deployment package.
#
# Usage: scripts/build_lambda.sh [output.zip]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="${1:-$ROOT/build/lambda.zip}"
BUILD_DIR="$ROOT/build/package"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
PLATFORM="${LAMBDA_PLATFORM:-manylinux2014_x86_64}"

echo "==> Cleaning build directory"
rm -rf "$BUILD_DIR" "$OUTPUT"
mkdir -p "$BUILD_DIR" "$(dirname "$OUTPUT")"

echo "==> Installing runtime dependencies for $PLATFORM (py$PYTHON_VERSION)"
# --only-binary=:all: with an explicit platform makes the build work from macOS
# or any CI runner, not just Amazon Linux.
pip install \
  --requirement "$ROOT/requirements.txt" \
  --target "$BUILD_DIR" \
  --platform "$PLATFORM" \
  --python-version "$PYTHON_VERSION" \
  --implementation cp \
  --only-binary=:all: \
  --upgrade \
  --quiet

echo "==> Copying application source"
cp -R "$ROOT/lambda/." "$BUILD_DIR/"

echo "==> Pruning build artifacts"
find "$BUILD_DIR" -type d -name "__pycache__" -prune -exec rm -rf {} +
find "$BUILD_DIR" -type d -name "*.dist-info" -prune -exec rm -rf {} +
find "$BUILD_DIR" -type d -name "tests" -prune -exec rm -rf {} +
find "$BUILD_DIR" -type f -name "*.pyc" -delete

echo "==> Creating $OUTPUT"
# Fixed timestamps + sorted entries keep the zip hash stable across identical
# builds, so Terraform only redeploys when the code actually changed.
( cd "$BUILD_DIR" && find . -exec touch -t 200001010000.00 {} + && \
  zip -q -X -r -D "$OUTPUT" . -x ".*" )

SIZE=$(du -h "$OUTPUT" | cut -f1)
echo "==> Built $OUTPUT ($SIZE)"
