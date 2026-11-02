#!/usr/bin/env bash
# Runs the phase 1 checks against the cluster in env.local:
#   1. sdk_crud.py  - SDK lifecycle plus commands/files through sandbox-router.
#   2. rl_smoke.py  - agent-sandbox-rl, one dynamic template per image, exec API.
# Both leave nothing behind in SANDBOX_NAMESPACE.
set -euo pipefail
# shellcheck source=SCRIPTDIR/../lib.sh
source "$(dirname "$0")/../lib.sh"
require PROJECT REGION CLUSTER AGENT_SANDBOX_VERSION SANDBOX_NAMESPACE
TESTS_DIR="${DEPLOY_DIR}/tests"
REPO_DIR="$(cd "${DEPLOY_DIR}/.." && pwd)"

# The SDK and its kubectl port-forward use the current context. Point them at a
# one-context kubeconfig so the caller's own current context is left alone.
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT
kubectl config view --minify --flatten --context="$(kube_context)" > "${work}/kubeconfig"
export KUBECONFIG="${work}/kubeconfig"
export SANDBOX_NAMESPACE AGENT_SANDBOX_VERSION

VENV="${TESTS_DIR}/.venv"
if [[ ! -x "${VENV}/bin/python" ]]; then
  uv venv -q "${VENV}"
fi
uv pip install -q --python "${VENV}/bin/python" \
  "k8s-agent-sandbox==${AGENT_SANDBOX_VERSION#v}" \
  "${REPO_DIR}/examples/agent-sandbox-rl"

echo "== sdk_crud.py"
status=0
"${VENV}/bin/python" -I "${TESTS_DIR}/sdk_crud.py" || status=1

echo "== rl_smoke.py"
"${VENV}/bin/python" -I "${TESTS_DIR}/rl_smoke.py" || status=1

echo "== leftovers in ${SANDBOX_NAMESPACE}"
kubectl -n "${SANDBOX_NAMESPACE}" get sandboxtemplates,sandboxwarmpools,sandboxclaims,sandboxes,pods 2>&1

if [[ "${status}" -eq 0 ]]; then echo "PASS"; else echo "FAIL"; fi
exit "${status}"
