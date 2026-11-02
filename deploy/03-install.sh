#!/usr/bin/env bash
# Installs the agent-sandbox controller and extensions, sandbox-router, the
# metrics scrape configs, and the namespace the tests use. Safe to rerun.
set -euo pipefail
# shellcheck source=SCRIPTDIR/lib.sh
source "$(dirname "$0")/lib.sh"
require PROJECT REGION CLUSTER AGENT_SANDBOX_VERSION SANDBOX_NAMESPACE
KUBECTL=(kubectl --context="$(kube_context)")

"${KUBECTL[@]}" apply -f "https://github.com/kubernetes-sigs/agent-sandbox/releases/download/${AGENT_SANDBOX_VERSION}/sandbox-with-extensions.yaml"
"${KUBECTL[@]}" -n agent-sandbox-system rollout status deploy/agent-sandbox-controller --timeout=180s

# The router's NetworkPolicy must allow the control plane's private endpoint,
# which differs per cluster. Read it from the kubernetes Service endpoints.
api_ip="$("${KUBECTL[@]}" get endpointslices -n default \
  -l kubernetes.io/service-name=kubernetes \
  -o jsonpath='{.items[0].endpoints[0].addresses[0]}')"
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT
cp -r "${DEPLOY_DIR}/router/." "${work}/"
sed -i "s|API_ENDPOINT_CIDR|${api_ip}/32|" "${work}/netpol-gke-patch.yaml"
"${KUBECTL[@]}" apply -k "${work}"
"${KUBECTL[@]}" -n agent-sandbox-system rollout status deploy/sandbox-router --timeout=180s

# Needs Managed Service for Prometheus, which GKE enables by default.
"${KUBECTL[@]}" apply -f "${DEPLOY_DIR}/monitoring/podmonitorings.yaml"

"${KUBECTL[@]}" create namespace "${SANDBOX_NAMESPACE}" --dry-run=client -o yaml \
  | "${KUBECTL[@]}" apply -f -
