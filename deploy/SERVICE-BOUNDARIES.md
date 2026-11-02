# Agent sandbox service boundaries (2026-11-02)

This document records what the phase 1 agent sandbox platform can and cannot do
as of 2026-11-02. Every capability listed as available was exercised against
the live cluster on that date. [README.md](README.md) describes how to create
the platform; this document describes what you get once it exists.

**Verdict:** the platform is ready for trial use by trusted team members who
already hold credentials for the cluster. It is not ready to serve other
services or external callers, because sandbox-router has no authentication and
nothing stops a caller from defining a sandbox without gVisor.

## Environment

| Item | Value |
| --- | --- |
| Region | One region, `asia-east1`. Nodes run in zones a and b. |
| Cluster | `agent-sandbox-asia-east1`: regional GKE Standard 1.36.4 on the regular channel, Dataplane V2, private nodes, and control plane access restricted to authorized networks |
| agent-sandbox | v1.0.9: controller with extensions (1 replica) and 4 CRDs |
| sandbox-router | v1.0.9, 2 replicas, `--authz-mode=allow-all` |
| System node pool | `default-pool`: 2 × e2-standard-4, always on |
| Sandbox node pool | `gvisor-n2-highmem-8`: gVisor, autoscaled from 0 to 5 nodes per zone (10 in total), with no custom labels |
| Sandbox namespace | `agent-sandbox-rl`, empty between runs |
| Egress | Cloud NAT with one auto-allocated external IP |

## Access model

A caller needs all three of the following. No single URL or API key gives
access to the platform.

1. **Cluster credentials.** GKE access in the GCP project, used through a
   kubeconfig for the cluster. Every SDK call goes through the Kubernetes API.
2. **An allowed source address.** The control plane accepts only the office
   egress IP and the node and Pod ranges of the existing workload cluster in
   the same VPC. `AUTHORIZED_NETWORKS` in `env.local` holds the exact list.
3. **A client library.** Either `k8s-agent-sandbox==1.0.9` from PyPI or
   agent-sandbox-rl installed from `examples/agent-sandbox-rl` in this
   repository. agent-sandbox-rl is not on PyPI.

## Capabilities

| Capability | How | Requirement on the sandbox image | Evidence |
| --- | --- | --- | --- |
| Create, list, get, and delete sandboxes | SDK `create_sandbox`, `list_all_sandboxes`, `get_sandbox`, `terminate`, with an optional expiry (`shutdown_after_seconds`) | None | `tests/sdk_crud.py` |
| Run commands and read and write files through the SDK | `sandbox.commands.run` and `sandbox.files.*` over sandbox-router in Tunnel mode | Must serve the python-runtime API on port 8888 | `tests/sdk_crud.py` |
| Run any image | agent-sandbox-rl creates a SandboxTemplate and SandboxWarmPool per image, runs commands over the Kubernetes exec API, and deletes both afterwards | Must contain `sh` | `tests/rl_smoke.py` with `busybox:1.36` and `python:3.12-slim` |
| gVisor isolation | `runtimeClassName: gvisor` on the template. GKE then places the Pod on sandbox nodes without a node selector. | None | Sandbox kernel reports `4.19.0-gvisor` and `Starting gVisor...` |
| Scale to zero | The sandbox node pool scales down when idle and back up on demand | None | Node pool started from 0 nodes during the tests |
| Multi-cluster dispatch | agent-sandbox-rl accepts several `ClusterConfig` entries and a placement policy | None | Not tested, because only one cluster exists |

### Timing

| Situation | Time until the sandbox is ready |
| --- | --- |
| Sandbox node pool at zero, image not cached | 50–100 s |
| Node running, image cached | A few seconds |

Through sandbox-router, the router's `--proxy-timeout` caps each SDK command
request at 180 s.

## Network behavior

These results come from a busybox sandbox under the controller's default
per-template NetworkPolicy:

| Destination | Result |
| --- | --- |
| DNS name resolution (`pypi.org`) | Works |
| Public HTTPS by name (`https://pypi.org`) | Works |
| Public IP (`1.1.1.1`) | Works |
| Metadata server (`169.254.169.254`) | Blocked (times out) |
| Control plane private endpoint | Blocked (times out) |
| Ingress | Only sandbox-router may connect |

Sandboxes can therefore install packages and call public APIs, but they cannot
read node credentials or reach the Kubernetes API. Egress to the public
internet is not restricted by destination.

## Capacity

| Item | Limit |
| --- | --- |
| Allocatable per sandbox node | 7.91 CPU, about 57 GiB memory |
| Sandbox nodes | 10 at most, about 80 vCPU in total |
| Default agent-sandbox-rl sandbox | Requests 250m CPU and 512Mi memory, with no limit, unless the caller sets `TemplateSpec.resources` |
| Machine family | N2 only. C4 is faster per core, but its quota in this region is 0. |
| GPU | None |

## Not available

### Security gaps that block wider use

- **sandbox-router has no authentication.** Any Pod in the cluster, and anyone
  allowed to port-forward to the router, can reach every sandbox.
- **No admission policy.** Anyone who can create a SandboxTemplate can define
  any Pod spec, including one without gVisor or with privileges. Draft
  policies exist but are not deployed.
- **Unrestricted public egress.** FQDN network policy is not enabled on this
  cluster, and no allowlist exists.

### Missing features

- **No single endpoint.** There is no URL plus API key for callers, and the
  SDK's Gateway mode is not set up.
- **No file API on the exec path.** With agent-sandbox-rl, files move only
  through exec, for example with `tar` or base64.
- **No persistent storage.** Sandbox data disappears when the sandbox is
  deleted. Suspend, resume, and snapshots are not configured.
- **No image mirror.** Images come from public registries through Cloud NAT,
  so Docker Hub pull limits apply.
- **No per-user quotas or cost reports.** The only capacity guard is the node
  pool maximum.

## Observability

Added on 2026-11-02, after the rest of this document was written.

- Managed Prometheus scrapes the controller and both router replicas every
  30 s, alongside GKE's node, container, and kube-state metrics.
- Cloud Monitoring has a dashboard named `agent-sandbox: agent-sandbox-asia-east1`
  and 8 alert policies. The policies cover the node pool reaching its maximum,
  Pending sandbox Pods, slow claim startup, image pull failures, NAT port
  exhaustion, node disk usage, a missing controller, and controller reconcile
  errors.
- The image pull and Pending alert expressions were checked against a Pod
  with a nonexistent image. The other expressions were checked for valid
  queries only, because their conditions could not be triggered safely.
- **No notification channel is attached.** Incidents show in the console but
  notify no one.

### Availability

- One region and one cluster. The controller runs a single replica.
- Multi-region means one cluster per region, created with the same scripts
  and a separate settings file. agent-sandbox has no cross-cluster control
  plane. The scripts currently read only `env.local`, so each region needs its
  own copy of `deploy/` or a change to the settings loading.

## Suitable uses

| Use | Status |
| --- | --- |
| Team members running data synthesis or evaluation jobs with the SDK or agent-sandbox-rl | Supported |
| Sandboxes that install packages or call public APIs | Supported |
| Other services or teams calling the platform through an API | Not supported: no shared endpoint and no authentication |
| External users, or running sandbox definitions from untrusted sources | Not supported: open router and no admission policy |

## Re-verify

Run the phase 1 checks:

```sh
deploy/tests/run-tests.sh
```

Inspect the platform state:

```sh
kubectl -n agent-sandbox-system get deploy
kubectl get nodes -L cloud.google.com/gke-nodepool
kubectl -n agent-sandbox-rl get sandboxtemplates,sandboxwarmpools,sandboxclaims,sandboxes
```
