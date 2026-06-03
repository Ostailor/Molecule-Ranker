#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../../v1_9_pilot_demo"
./scripts/run_demo_workflows.sh "$@"
