"""Create or update the Cloud Monitoring alert policies and dashboard for one cluster.

Every query is PromQL over Managed Service for Prometheus, so it covers both the
scraped series (controller, router, kube-state-metrics) and GKE system metrics.
Objects are matched by display name, so rerunning updates them in place instead
of creating duplicates.

Reads PROJECT, CLUSTER, NODE_ZONES, GVISOR_MAX_NODES, and optionally
ALERT_CHANNELS (comma-separated notification channel resource names) from the
environment. Authenticates with `gcloud auth print-access-token`.
"""

import json
import logging
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://monitoring.googleapis.com"
MANAGED_BY = "agent-sandbox-deploy"
SYSTEM_NAMESPACES = (
    "kube-system|kube-public|kube-node-lease|gke-.*|gmp-.*|agent-sandbox-system"
)

log = logging.getLogger("cloud_monitoring")


def call(method: str, path: str, token: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"{method} {path}: {e.code} {e.read().decode()[:500]}"
        ) from e


def alert_policies(cluster: str, max_nodes: int, channels: list[str]) -> list[dict]:
    c = f'cluster="{cluster}"'
    sandbox_ns = f'namespace!~"{SYSTEM_NAMESPACES}"'
    # GKE truncates pool names in node names, so match the "gvisor" fragment.
    gvisor_nodes = (
        f'kubernetes_io:node_cpu_allocatable_cores{{monitored_resource="k8s_node",'
        f'cluster_name="{cluster}",node_name=~".*gvisor.*"}}'
    )
    rules = [
        (
            "gVisor node pool at maximum size",
            f"count({gvisor_nodes}) >= {max_nodes}",
            "600s",
            f"The sandbox node pool has reached its {max_nodes}-node limit, so new "
            "sandboxes wait for capacity. Raise the pool's max nodes or add a pool.",
        ),
        (
            "Sandbox Pods pending",
            f'sum(kube_pod_status_phase{{{c},{sandbox_ns},phase="Pending"}}) > 0',
            "300s",
            "Sandbox Pods have been Pending for 5 minutes: no node fits them, the "
            "node pool is scaling, or the image is still pulling.",
        ),
        (
            "Sandbox claim startup p95 above 120s",
            "histogram_quantile(0.95, sum by (le) (rate("
            f"agent_sandbox_claim_controller_startup_latency_ms_bucket{{{c}}}[15m]))) > 120000",
            "300s",
            "SandboxClaims take more than 2 minutes to become Ready at the 95th "
            "percentile, usually because of node scale-up or image pulls.",
        ),
        (
            "Sandbox image pull failures",
            f"sum(kube_pod_container_status_waiting_reason{{{c},{sandbox_ns},"
            f'reason=~"ImagePullBackOff|ErrImagePull"}}) > 0',
            "300s",
            "A sandbox container cannot pull its image. Check the image name, "
            "registry access, and registry rate limits.",
        ),
        (
            "Cloud NAT dropping packets for lack of ports",
            "sum(rate(router_googleapis_com:nat_dropped_sent_packets_count{"
            f'monitored_resource="nat_gateway",gateway_name="{cluster}-nat",'
            'reason="OUT_OF_RESOURCES"}[5m])) > 0',
            "300s",
            "Sandbox egress fails because a node ran out of NAT ports. Raise the "
            "NAT's minimum ports per VM or enable dynamic port allocation.",
        ),
        (
            "Node ephemeral storage above 80%",
            "max by (node_name) (kubernetes_io:node_ephemeral_storage_used_bytes"
            f'{{cluster_name="{cluster}"}}) / max by (node_name) ('
            f'kubernetes_io:node_ephemeral_storage_total_bytes{{cluster_name="{cluster}"}}) > 0.8',
            "600s",
            "Images and container writes are filling a node's boot disk; kubelet "
            "evicts Pods when it fills.",
        ),
        (
            "agent-sandbox controller down",
            f'absent(up{{{c},job="agent-sandbox-controller"}} == 1)',
            "300s",
            "No healthy agent-sandbox controller is being scraped, so no sandbox "
            "is created, adopted, or cleaned up.",
        ),
        (
            "agent-sandbox controller reconcile errors",
            "sum(rate(controller_runtime_reconcile_errors_total{"
            f'{c},namespace="agent-sandbox-system"}}[10m])) > 0.1',
            "600s",
            "The controller reports more than one reconcile error every 10 seconds. "
            "Check its logs.",
        ),
    ]
    return [
        {
            "displayName": f"[{cluster}] {name}",
            "combiner": "OR",
            "conditions": [
                {
                    "displayName": name,
                    "conditionPrometheusQueryLanguage": {
                        "query": query,
                        "duration": duration,
                        "evaluationInterval": "60s",
                    },
                }
            ],
            "documentation": {"content": doc, "mimeType": "text/markdown"},
            "alertStrategy": {"autoClose": "1800s"},
            "notificationChannels": channels,
            "userLabels": {"managed_by": MANAGED_BY},
        }
        for name, query, duration, doc in rules
    ]


def dashboard(cluster: str) -> dict:
    c = f'cluster="{cluster}"'
    sandbox_ns = f'namespace!~"{SYSTEM_NAMESPACES}"'
    panels = [
        (
            "gVisor nodes",
            f'count(kubernetes_io:node_cpu_allocatable_cores{{monitored_resource="k8s_node",'
            f'cluster_name="{cluster}",node_name=~".*gvisor.*"}})',
        ),
        (
            "Sandbox Pods by phase",
            f"sum by (phase) (kube_pod_status_phase{{{c},{sandbox_ns}}})",
        ),
        (
            "Claim startup latency p50 / p95 (ms)",
            "histogram_quantile(0.5, sum by (le) (rate("
            f"agent_sandbox_claim_controller_startup_latency_ms_bucket{{{c}}}[15m]))) or "
            "histogram_quantile(0.95, sum by (le) (rate("
            f"agent_sandbox_claim_controller_startup_latency_ms_bucket{{{c}}}[15m])))",
        ),
        (
            "Sandboxes created per minute by launch type",
            f"sum by (launch_type) (rate(agent_sandbox_claim_creation_total{{{c}}}[5m])) * 60",
        ),
        (
            "gVisor node CPU in use (cores)",
            "sum by (node_name) (rate(kubernetes_io:node_cpu_core_usage_time"
            f'{{cluster_name="{cluster}",node_name=~".*gvisor.*"}}[5m]))',
        ),
        (
            "Node ephemeral storage used (%)",
            "100 * max by (node_name) (kubernetes_io:node_ephemeral_storage_used_bytes"
            f'{{cluster_name="{cluster}"}}) / max by (node_name) ('
            f'kubernetes_io:node_ephemeral_storage_total_bytes{{cluster_name="{cluster}"}})',
        ),
        (
            "Cloud NAT ports in use (max per VM)",
            "max(router_googleapis_com:nat_port_usage{"
            f'monitored_resource="nat_gateway",gateway_name="{cluster}-nat"}})',
        ),
        (
            "Cloud NAT packets dropped for lack of ports (/s)",
            "sum(rate(router_googleapis_com:nat_dropped_sent_packets_count{"
            f'monitored_resource="nat_gateway",gateway_name="{cluster}-nat",'
            'reason="OUT_OF_RESOURCES"}[5m]))',
        ),
        (
            "Controller reconcile errors (/s) and workqueue depth",
            "sum(rate(controller_runtime_reconcile_errors_total{"
            f'{c},namespace="agent-sandbox-system"}}[5m])) or '
            f'sum(workqueue_depth{{{c},namespace="agent-sandbox-system"}})',
        ),
        (
            "Router requests (/s) by status code",
            f"sum by (code) (rate(sandbox_router_requests_total{{{c}}}[5m]))",
        ),
    ]
    tiles = []
    for i, (title, query) in enumerate(panels):
        tiles.append(
            {
                "xPos": (i % 2) * 24,
                "yPos": (i // 2) * 16,
                "width": 24,
                "height": 16,
                "widget": {
                    "title": title,
                    "xyChart": {
                        "dataSets": [
                            {
                                "timeSeriesQuery": {"prometheusQuery": query},
                                "plotType": "LINE",
                            }
                        ]
                    },
                },
            }
        )
    return {
        "displayName": f"agent-sandbox: {cluster}",
        "mosaicLayout": {"columns": 48, "tiles": tiles},
        "labels": {"managed_by": MANAGED_BY},
    }


def upsert_policies(project: str, token: str, policies: list[dict]) -> None:
    flt = urllib.parse.quote(f'user_labels.managed_by="{MANAGED_BY}"')
    existing = call(
        "GET", f"/v3/projects/{project}/alertPolicies?filter={flt}&pageSize=200", token
    )
    by_name = {p["displayName"]: p["name"] for p in existing.get("alertPolicies", [])}
    for policy in policies:
        name = by_name.get(policy["displayName"])
        if name:
            call("PATCH", f"/v3/{name}", token, policy)
            log.info("updated alert policy %r", policy["displayName"])
        else:
            created = call(
                "POST", f"/v3/projects/{project}/alertPolicies", token, policy
            )
            log.info(
                "created alert policy %r (%s)", policy["displayName"], created["name"]
            )


def upsert_dashboard(project: str, token: str, board: dict) -> None:
    existing = call("GET", f"/v1/projects/{project}/dashboards?pageSize=1000", token)
    for d in existing.get("dashboards", []):
        if d.get("displayName") == board["displayName"]:
            call(
                "PATCH",
                f"/v1/{d['name']}",
                token,
                {**board, "name": d["name"], "etag": d["etag"]},
            )
            log.info("updated dashboard %r", board["displayName"])
            return
    created = call("POST", f"/v1/projects/{project}/dashboards", token, board)
    log.info("created dashboard %r (%s)", board["displayName"], created["name"])


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    project = os.environ["PROJECT"]
    cluster = os.environ["CLUSTER"]
    zones = [z for z in os.environ["NODE_ZONES"].split(",") if z]
    max_nodes = int(os.environ["GVISOR_MAX_NODES"]) * len(zones)
    channels = [ch for ch in os.environ.get("ALERT_CHANNELS", "").split(",") if ch]
    if not channels:
        log.warning(
            "ALERT_CHANNELS is empty: incidents appear in the console but notify no one"
        )

    token = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    upsert_policies(project, token, alert_policies(cluster, max_nodes, channels))
    upsert_dashboard(project, token, dashboard(cluster))
    return 0


if __name__ == "__main__":
    sys.exit(main())
