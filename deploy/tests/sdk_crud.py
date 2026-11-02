"""Create, list, get, use, and delete one sandbox through the Python SDK.

Exercises the SDK's commands and files helpers through sandbox-router in Tunnel
mode: the SDK port-forwards to the router, which proxies to the sandbox Pod.
Needs the warm pool from sdk-warmpool.yaml. Uses the current kubeconfig context.
"""

import logging
import os
import sys
import time

from k8s_agent_sandbox import SandboxClient
from k8s_agent_sandbox.models import SandboxLocalTunnelConnectionConfig

NAMESPACE = os.environ.get("SANDBOX_NAMESPACE", "agent-sandbox-rl")
WARMPOOL = "c1-m2g"

log = logging.getLogger("sdk_crud")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    client = SandboxClient(connection_config=SandboxLocalTunnelConnectionConfig())

    start = time.monotonic()
    # The gVisor node pool may need to scale up from zero, so allow 5 minutes.
    sandbox = client.create_sandbox(
        warmpool=WARMPOOL,
        namespace=NAMESPACE,
        sandbox_ready_timeout=300,
        shutdown_after_seconds=1800,
    )
    claim = sandbox.claim_name
    log.info("create: claim=%s ready in %.0fs", claim, time.monotonic() - start)

    try:
        log.info("list: %s", client.list_all_sandboxes(namespace=NAMESPACE))

        same = client.get_sandbox(claim, namespace=NAMESPACE)
        log.info("get: claim=%s", same.claim_name)

        result = sandbox.commands.run("uname -a && dmesg | head -1")
        log.info("run: exit=%s stdout=%s", result.exit_code, result.stdout.strip())
        if result.exit_code != 0:
            return 1

        sandbox.files.write("hello.txt", "hello from the SDK\n")
        content = sandbox.files.read("hello.txt")
        log.info("files: read back %r", content)
        if content != b"hello from the SDK\n":
            return 1
    finally:
        sandbox.terminate()
        log.info("delete: claim=%s", claim)

    remaining = client.list_all_sandboxes(namespace=NAMESPACE)
    log.info("list after delete: %s", remaining)
    return 1 if claim in remaining else 0


if __name__ == "__main__":
    sys.exit(main())
