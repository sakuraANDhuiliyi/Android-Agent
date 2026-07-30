#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 /path/to/Android Agent.app" >&2
  exit 2
fi

artifact=$1
if [[ ! -e "$artifact" ]]; then
  echo "artifact does not exist: $artifact" >&2
  exit 2
fi

codesign --verify --deep --strict --verbose=2 "$artifact"
spctl --assess --type execute --verbose=2 "$artifact"
