# shellcheck shell=bash
# Sourced by the deploy scripts: loads env.local and checks required settings.

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Values already exported in the environment win over env.local.
if [[ -f "${DEPLOY_DIR}/env.local" ]]; then
  while IFS='=' read -r key value; do
    [[ -z "${key}" || "${key}" == \#* ]] && continue
    [[ -z "${!key:-}" ]] && export "${key}=${value}"
  done < "${DEPLOY_DIR}/env.local"
fi

require() {
  local name
  for name in "$@"; do
    if [[ -z "${!name:-}" ]]; then
      echo "missing setting ${name}; set it in ${DEPLOY_DIR}/env.local (see env.example)" >&2
      exit 1
    fi
  done
}

kube_context() {
  echo "gke_${PROJECT}_${REGION}_${CLUSTER}"
}
