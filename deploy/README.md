# Phase 1 deployment: agent sandboxes on GKE

These scripts stand up the minimum platform for running agent sandboxes on GKE
and check that it works. Phase 1 covers two ways to use a sandbox:

- **SDK through sandbox-router.** The Python SDK creates, lists, gets, and
  deletes sandboxes, and runs commands and reads and writes files through
  sandbox-router in Tunnel mode. The sandbox image must serve the
  python-runtime API on port 8888.
- **agent-sandbox-rl with the Kubernetes exec API.** The library creates one
  SandboxTemplate and SandboxWarmPool per task image, runs commands with
  Kubernetes exec, and deletes the template and pool afterwards. Any image with
  `sh` works, with no daemon inside it.

Every sandbox runs under gVisor (GKE Sandbox).

For what the running platform can and cannot do, see
[SERVICE-BOUNDARIES.md](SERVICE-BOUNDARIES.md).

Phase 1 leaves out admission policies, a namespace-wide NetworkPolicy,
FQDN egress rules, an Artifact Registry mirror, and router authentication. See
[Not included](#not-included).

## Files

| File | What it does |
| --- | --- |
| `env.example` | Every setting the scripts read. Copy to `env.local`. |
| `lib.sh` | Loads `env.local` and checks required settings. Sourced by the scripts. |
| `01-cluster.sh` | Creates the subnet, Cloud Router and NAT, and the cluster. |
| `02-gvisor-nodepool.sh` | Creates the gVisor node pool. |
| `03-install.sh` | Installs the agent-sandbox controller and extensions, sandbox-router, the metrics scrape configs, and the test namespace. Safe to rerun. |
| `04-monitoring.sh` | Creates or updates the Cloud Monitoring alert policies and dashboard. Safe to rerun. |
| `router/` | Kustomization for sandbox-router: `base/` is an unmodified copy of `sandbox-router/deploy` at v1.0.9, plus the GKE network policy patch. Refresh `base/` and the image tag when you change `AGENT_SANDBOX_VERSION`. |
| `monitoring/podmonitorings.yaml` | Managed Prometheus scrape configs for the controller and sandbox-router. |
| `monitoring/cloud_monitoring.py` | The alert policies and dashboard that `04-monitoring.sh` applies. |
| `tests/run-tests.sh` | Runs both checks below and reports PASS or FAIL. |
| `tests/sdk_crud.py`, `tests/sdk-warmpool.yaml` | SDK lifecycle, commands, and files through sandbox-router. The script creates the template and warm pool from the YAML and deletes them when it ends, pass or fail. |
| `tests/rl_smoke.py` | agent-sandbox-rl with two tasks on two different images. |
| `99-teardown.sh` | Deletes everything `01-cluster.sh` created. Needs `CONFIRM=yes`. |

## Required resources

### Google Cloud project

- APIs enabled: `container.googleapis.com` and `compute.googleapis.com`.
- An existing custom-mode VPC (`NETWORK`). The scripts add one subnet to it.
  `NODE_RANGE`, `POD_RANGE`, and `SERVICE_RANGE` must not overlap any range in
  that VPC or in networks peered with it.
- `GVISOR_MACHINE_TYPE` must be offered in every zone in `NODE_ZONES`. Check
  with `gcloud compute machine-types list --filter="name=<type> AND zone~<region>"`.

### Regional quota

Values for the default settings: two zones, `n2-highmem-8`, and up to 5 gVisor
nodes per zone.

| Quota | Always on | At full scale | Notes |
| --- | --- | --- | --- |
| `CPUS` | 8 | 88 | 2 × e2-standard-4 system nodes plus 10 × 8 vCPU gVisor nodes |
| `N2_CPUS` | 0 | 80 | gVisor nodes only. C4 machines use the separate `CPUS_PER_VM_FAMILY` quota, which can be 0 in a region even when `CPUS` is not. |
| `SSD_TOTAL_GB` (pd-balanced) | 200 | 2200 | 100 GB per system node, `GVISOR_DISK_SIZE_GB` per gVisor node |
| External IP addresses | 1 | 1 | Allocated automatically by Cloud NAT |

The gVisor pool scales to zero when no sandbox runs.

### IAM for the operator

- To run `01`–`03` and `99`: `roles/container.admin`,
  `roles/compute.networkAdmin`, and `roles/iam.serviceAccountUser` on the node
  service account (the Compute Engine default service account, unless you
  change it).
- To run only the tests on an existing cluster, `roles/container.developer`
  should be enough. It covers custom resources, `pods/exec`, and
  `pods/portforward`. This narrower role has not been tested here.

### Operator machine

- Its public egress IP must be in `AUTHORIZED_NETWORKS`. The control plane
  rejects everything else.
- Tools: `gcloud` with `gke-gcloud-auth-plugin`, `kubectl` (with built-in
  kustomize), and [`uv`](https://docs.astral.sh/uv/). `uv` creates the Python
  3.11+ virtualenv in `tests/.venv`.
- Outbound access to GitHub (the release manifest) and PyPI
  (`k8s-agent-sandbox`).
- A checkout of this repository. `run-tests.sh` installs agent-sandbox-rl
  from `examples/agent-sandbox-rl`, which is not published on PyPI.

### Cluster egress

Nodes are private and reach the internet through Cloud NAT. The tests pull
`registry.k8s.io/agent-sandbox/*` and, for `rl_smoke.py`, `busybox:1.36` and
`python:3.12-slim` from Docker Hub.

## What gets created

| Layer | Resource | Default |
| --- | --- | --- |
| VPC | Subnet `SUBNET` with secondary ranges `pods` and `services` | `10.90.0.0/20`, `10.92.0.0/14`, `10.96.0.0/20` |
| VPC | Cloud Router `<CLUSTER>-router` and NAT `<CLUSTER>-nat` | One auto-allocated external IP |
| GKE | Regional Standard cluster `CLUSTER` | Regular channel, Dataplane V2, FQDN network policy enabled, private nodes, authorized networks, Workload Identity |
| GKE | Node pool `default-pool` | 1 × e2-standard-4 per zone, always on |
| GKE | Node pool `GVISOR_POOL` | gVisor, 0 to `GVISOR_MAX_NODES` per zone, autoscaled |
| Kubernetes | agent-sandbox controller and extensions, 4 CRDs | Release `AGENT_SANDBOX_VERSION` in `agent-sandbox-system` |
| Kubernetes | sandbox-router | 2 replicas in `agent-sandbox-system`, `--authz-mode=allow-all` |
| Kubernetes | Namespace `SANDBOX_NAMESPACE` | Empty between test runs |
| Kubernetes | PodMonitorings `agent-sandbox-controller` and `sandbox-router` | Scraped every 30 s by Managed Prometheus |
| Cloud Monitoring | 8 alert policies named `[<CLUSTER>] ...`, labeled `managed_by=agent-sandbox-deploy` | Notify `ALERT_CHANNELS`; none when it is empty |
| Cloud Monitoring | Dashboard `agent-sandbox: <CLUSTER>` | 10 PromQL charts |

Standing cost comes from the cluster control plane, the two system nodes and
their disks, and Cloud NAT. gVisor nodes cost only while sandboxes run, plus
the autoscaler's scale-down delay of about 10 minutes.

## Run

```sh
cd deploy
cp env.example env.local   # then edit env.local
./01-cluster.sh            # about 10 minutes
./02-gvisor-nodepool.sh    # about 3 minutes
./03-install.sh
./04-monitoring.sh
tests/run-tests.sh         # about 3–5 minutes, longer when gVisor nodes start from zero
```

`04-monitoring.sh` uses the system `python3` (3.10 or later) with no extra
packages.

## Monitoring

GKE already collects node, container, and kube-state metrics and sends system
and workload logs to Cloud Logging. `03-install.sh` adds scrapes of the
controller's sandbox metrics (see [docs/metrics.md](../docs/metrics.md)) and
the router's request metrics. The router patch admits scrapes from
`gmp-system`, because the upstream policy admits only a namespace named
`monitoring`.

`04-monitoring.sh` creates these alert policies. Every condition is PromQL.

| Alert | Fires when |
| --- | --- |
| gVisor node pool at maximum size | The gVisor node count reaches `GVISOR_MAX_NODES` × zones for 10 minutes |
| Sandbox Pods pending | Any Pod outside system namespaces stays Pending for 5 minutes |
| Sandbox claim startup p95 above 120s | `agent_sandbox_claim_controller_startup_latency_ms` p95 over 15 minutes exceeds 120 s |
| Sandbox image pull failures | A container outside system namespaces waits in `ImagePullBackOff` or `ErrImagePull` for 5 minutes |
| Cloud NAT dropping packets for lack of ports | `<CLUSTER>-nat` drops packets with reason `OUT_OF_RESOURCES` |
| Node ephemeral storage above 80% | Any node's boot disk is more than 80% used for 10 minutes |
| agent-sandbox controller down | No controller target reports `up == 1` for 5 minutes |
| agent-sandbox controller reconcile errors | More than one reconcile error every 10 seconds, sustained for 10 minutes |

The thresholds are starting points. Change them in
`monitoring/cloud_monitoring.py` and rerun `04-monitoring.sh`.

Without `ALERT_CHANNELS`, incidents open in the Cloud Monitoring console but
notify no one. Create a channel in the console, put its resource name in
`env.local`, and rerun `04-monitoring.sh`.

`run-tests.sh` prints `PASS` when both checks succeed:

- `sdk_crud.py` logs `create`, `list`, `get`, `run` with `exit=0` and a
  `4.19.0-gvisor` kernel, `files` reading back `b'hello from the SDK\n'`, and
  an empty `list after delete`.
- `rl_smoke.py` logs both tasks with `Starting gVisor...`, `BusyBox v1.36.1`,
  and `Python 3.12.x`.
- No SandboxTemplate, SandboxWarmPool, SandboxClaim, Sandbox, or Pod remains in
  `SANDBOX_NAMESPACE`.

The tests use a temporary one-context kubeconfig, so your current `kubectl`
context does not change.

## Not included

- **Admission policies** that force `runtimeClassName: gvisor` and restrict
  image registries. Anyone who can create a SandboxTemplate can define any Pod
  spec.
- **A namespace-wide NetworkPolicy.** Templates use the controller's
  per-template policy, which admits only sandbox-router and allows egress to
  public IPs. With many images, switch templates to `Unmanaged` and add one
  policy per namespace (see `examples/agent-sandbox-rl-network-policy`).
- **FQDN egress rules.** The feature is enabled on the cluster, but no
  FQDNNetworkPolicy exists.
- **Router authentication.** sandbox-router runs with `--authz-mode=allow-all`.
  Any Pod in the cluster and anyone allowed to port-forward to it can reach
  every sandbox.
- **Gateway mode.** The SDK reaches the router only through `kubectl
  port-forward`, so callers need cluster credentials.
- **An Artifact Registry mirror.** Images come from public registries through
  Cloud NAT.
- **Alert notifications.** No notification channel is configured.
