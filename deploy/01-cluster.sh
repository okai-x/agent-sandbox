#!/usr/bin/env bash
# Creates the subnet, Cloud NAT, and a regional GKE Standard cluster for agent
# sandboxes, then fetches credentials for it.
#
# Dataplane V2 can only be chosen at cluster creation. It enforces the
# NetworkPolicy objects the agent-sandbox controller creates and enables
# FQDNNetworkPolicy.
set -euo pipefail
# shellcheck source=SCRIPTDIR/lib.sh
source "$(dirname "$0")/lib.sh"
require PROJECT REGION NODE_ZONES NETWORK CLUSTER SUBNET \
  NODE_RANGE POD_RANGE SERVICE_RANGE AUTHORIZED_NETWORKS

gcloud compute networks subnets create "${SUBNET}" \
  --project="${PROJECT}" \
  --network="${NETWORK}" \
  --region="${REGION}" \
  --range="${NODE_RANGE}" \
  --secondary-range="pods=${POD_RANGE},services=${SERVICE_RANGE}" \
  --enable-private-ip-google-access

# Private nodes have no external IPs, so Cloud NAT provides egress for image
# pulls and calls to external APIs. NetworkPolicy still decides which Pods may
# use it.
gcloud compute routers create "${CLUSTER}-router" \
  --project="${PROJECT}" \
  --network="${NETWORK}" \
  --region="${REGION}"
gcloud compute routers nats create "${CLUSTER}-nat" \
  --project="${PROJECT}" \
  --router="${CLUSTER}-router" \
  --region="${REGION}" \
  --auto-allocate-nat-external-ips \
  --nat-custom-subnet-ip-ranges="${SUBNET},${SUBNET}:pods"

# The default node pool hosts system workloads and the controller; GKE Sandbox
# requires at least one non-sandbox pool. --num-nodes is per zone.
gcloud container clusters create "${CLUSTER}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --node-locations="${NODE_ZONES}" \
  --release-channel=regular \
  --enable-dataplane-v2 \
  --enable-fqdn-network-policy \
  --enable-ip-alias \
  --network="${NETWORK}" \
  --subnetwork="${SUBNET}" \
  --cluster-secondary-range-name=pods \
  --services-secondary-range-name=services \
  --enable-private-nodes \
  --enable-master-authorized-networks \
  --master-authorized-networks="${AUTHORIZED_NETWORKS}" \
  --workload-pool="${PROJECT}.svc.id.goog" \
  --machine-type=e2-standard-4 \
  --num-nodes=1

gcloud container clusters get-credentials "${CLUSTER}" \
  --project="${PROJECT}" --region="${REGION}"
