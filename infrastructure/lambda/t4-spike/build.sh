#!/usr/bin/env bash
# Builds the T4 spike Lambda deployment package without Docker: pip can fetch
# manylinux wheels for a target platform/Python version directly, which is
# enough here since every dependency ships prebuilt wheels (no compilation).
# Run from this directory before `cdk synth`/`cdk deploy` whenever
# requirements.txt or handler.py change.
set -euo pipefail
cd "$(dirname "$0")"

rm -rf build
pip install -r requirements.txt \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all: \
  --target build
cp handler.py build/handler.py
