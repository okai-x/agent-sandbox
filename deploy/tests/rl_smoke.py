"""Smoke-test agent-sandbox-rl with two tasks that each use a different image.

strategy="none" creates one size-1 SandboxTemplate + SandboxWarmPool per image,
claims a sandbox per task, runs commands through the Kubernetes exec API, and
deletes the pools afterwards. Needs no sandbox-router and no daemon in the
image. Uses the current kubeconfig context.
"""

import logging
import os
import sys
import time

from agent_sandbox_rl import ClusterConfig, FleetConfig, SandboxFleet
from agent_sandbox_rl.config import TemplateSpec

NAMESPACE = os.environ.get("SANDBOX_NAMESPACE", "agent-sandbox-rl")
TASKS = [
    {"id": "busybox-task", "image": "busybox:1.36"},
    {"id": "python-task", "image": "python:3.12-slim"},
]

log = logging.getLogger("rl_smoke")


def process(task, handle) -> dict[str, str]:
    kernel = handle.exec(["sh", "-c", "dmesg | head -1"]).strip()
    if task.id == "python-task":
        detail = handle.exec(["python3", "--version"]).strip()
    else:
        detail = handle.exec(["sh", "-c", "busybox | head -1"]).strip()
    return {"pod": handle.pod_name, "kernel": kernel, "detail": detail}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    fleet = SandboxFleet(
        FleetConfig(
            clusters=[ClusterConfig(name="sandbox", namespace=NAMESPACE)],
            max_concurrent=2,
            ready_timeout=600,
            # GKE schedules gvisor Pods onto sandbox nodes on its own, so no
            # node_selector is needed.
            template=TemplateSpec(runtime_class="gvisor"),
        )
    )
    fleet.load_tasks(TASKS)

    start = time.monotonic()
    results = fleet.run(process, strategy="none", concurrency=2)
    log.info("run finished in %.0fs", time.monotonic() - start)

    failed = 0
    for task, result in zip(fleet.tasks, results):
        if isinstance(result, BaseException) or "gVisor" not in result["kernel"]:
            failed += 1
            log.error("%s (%s): %r", task.id, task.image, result)
        else:
            log.info("%s (%s): %s", task.id, task.image, result)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
