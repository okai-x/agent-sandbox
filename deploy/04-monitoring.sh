#!/usr/bin/env bash
# Creates or updates the Cloud Monitoring alert policies and dashboard for the
# cluster. 03-install.sh already set up metrics collection. Safe to rerun.
set -euo pipefail
# shellcheck source=SCRIPTDIR/lib.sh
source "$(dirname "$0")/lib.sh"
require PROJECT CLUSTER NODE_ZONES GVISOR_MAX_NODES
export PROJECT CLUSTER NODE_ZONES GVISOR_MAX_NODES ALERT_CHANNELS="${ALERT_CHANNELS:-}"

python3 -I "${DEPLOY_DIR}/monitoring/cloud_monitoring.py"
