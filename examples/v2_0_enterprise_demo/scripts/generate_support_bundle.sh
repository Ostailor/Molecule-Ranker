#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../../v1_9_pilot_demo"
./scripts/generate_support_bundle.sh "$@"
