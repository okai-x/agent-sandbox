#!/usr/bin/env bash
# Creates the GKE Sandbox (gVisor) node pool that runs every sandbox Pod.
#
# The pool needs no custom node labels: GKE labels and taints sandbox nodes, and
# adds the matching node affinity and toleration to any Pod that sets
# runtimeClassName: gvisor.
set -euo pipefail
# shellcheck source=SCRIPTDIR/lib.sh
source "$(dirname "$0")/lib.sh"
require PROJECT REGION CLUSTER GVISOR_POOL GVISOR_MACHINE_TYPE \
  GVISOR_DISK_TYPE GVISOR_DISK_SIZE_GB GVISOR_MAX_NODES

# GKE Sandbox requires the Container-Optimized OS image. N2 machines need
# pd-balanced; C4 machines need hyperdisk-balanced. --num-nodes is the initial
# count per zone; without it gcloud starts 3.
gcloud container node-pools create "${GVISOR_POOL}" \
  --project="${PROJECT}" \
  --cluster="${CLUSTER}" \
  --location="${REGION}" \
  --sandbox=type=gvisor \
  --machine-type="${GVISOR_MACHINE_TYPE}" \
  --image-type=COS_CONTAINERD \
  --disk-type="${GVISOR_DISK_TYPE}" \
  --disk-size="${GVISOR_DISK_SIZE_GB}" \
  --num-nodes=0 \
  --enable-autoscaling \
  --min-nodes=0 \
  --max-nodes="${GVISOR_MAX_NODES}"
