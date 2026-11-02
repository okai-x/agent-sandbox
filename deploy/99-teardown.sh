#!/usr/bin/env bash
# Deletes everything 01-cluster.sh created: the cluster (with its node pools and
# all sandboxes), Cloud NAT, the router, and the subnet. The VPC is left alone.
# Irreversible; run with CONFIRM=yes.
set -euo pipefail
# shellcheck source=SCRIPTDIR/lib.sh
source "$(dirname "$0")/lib.sh"
require PROJECT REGION CLUSTER SUBNET

if [[ "${CONFIRM:-}" != "yes" ]]; then
  echo "This deletes cluster ${CLUSTER}, its NAT, router, and subnet ${SUBNET} in ${PROJECT}/${REGION}." >&2
  echo "Rerun with CONFIRM=yes to proceed." >&2
  exit 1
fi

gcloud container clusters delete "${CLUSTER}" \
  --project="${PROJECT}" --region="${REGION}" --quiet
gcloud compute routers nats delete "${CLUSTER}-nat" \
  --project="${PROJECT}" --router="${CLUSTER}-router" --region="${REGION}" --quiet
gcloud compute routers delete "${CLUSTER}-router" \
  --project="${PROJECT}" --region="${REGION}" --quiet
gcloud compute networks subnets delete "${SUBNET}" \
  --project="${PROJECT}" --region="${REGION}" --quiet
