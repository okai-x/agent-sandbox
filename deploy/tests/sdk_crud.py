"""Create, list, get, use, and delete one sandbox through the Python SDK.

Exercises the SDK's commands and files helpers through sandbox-router in Tunnel
mode: the SDK port-forwards to the router, which proxies to the sandbox Pod.

Terminating a sandbox deletes only its SandboxClaim, Sandbox, and Pod; the
SandboxTemplate and SandboxWarmPool stay. So the test creates both from
sdk-warmpool.yaml first and deletes them when it ends, pass or fail. Uses the
current kubeconfig context.
"""

import logging
import os
import pathlib
import sys
import time

import yaml
from kubernetes import client as k8s
from kubernetes import config as k8s_config
from kubernetes.client.rest import ApiException

from k8s_agent_sandbox import SandboxClient
from k8s_agent_sandbox.models import SandboxLocalTunnelConnectionConfig

NAMESPACE = os.environ.get("SANDBOX_NAMESPACE", "agent-sandbox-rl")
VERSION = os.environ.get("AGENT_SANDBOX_VERSION", "v1.0.9")
MANIFEST = pathlib.Path(__file__).with_name("sdk-warmpool.yaml")
GROUP = "extensions.agents.x-k8s.io"
API_VERSION = "v1beta1"
PLURALS = {"SandboxTemplate": "sandboxtemplates", "SandboxWarmPool": "sandboxwarmpools"}

log = logging.getLogger("sdk_crud")


def create_pool(api: k8s.CustomObjectsApi, created: list[tuple[str, str]]) -> str:
    """Create the template and warm pool; record each in `created` once it exists.

    Fails on AlreadyExists rather than reusing or overwriting an object that
    another run or another person owns. Returns the warm pool name.
    """
    text = MANIFEST.read_text().replace("AGENT_SANDBOX_VERSION", VERSION)
    pool = ""
    for doc in yaml.safe_load_all(text):
        plural = PLURALS[doc["kind"]]
        name = doc["metadata"]["name"]
        try:
            api.create_namespaced_custom_object(
                GROUP, API_VERSION, NAMESPACE, plural, doc
            )
        except ApiException as e:
            if e.status == 409:
                raise RuntimeError(
                    f"{doc['kind']} {NAMESPACE}/{name} already exists; "
                    "delete the leftover and rerun"
                ) from e
            raise
        created.append((plural, name))
        log.info("setup: created %s %s", doc["kind"], name)
        if doc["kind"] == "SandboxWarmPool":
            pool = name
    return pool


def delete_pool(api: k8s.CustomObjectsApi, created: list[tuple[str, str]]) -> None:
    # Reverse order: the warm pool goes before the template it references.
    for plural, name in reversed(created):
        try:
            api.delete_namespaced_custom_object(
                GROUP, API_VERSION, NAMESPACE, plural, name
            )
            log.info("cleanup: deleted %s %s", plural, name)
        except ApiException as e:
            if e.status != 404:
                log.error("cleanup: could not delete %s %s: %s", plural, name, e.reason)


def run_sandbox(warmpool: str) -> int:
    client = SandboxClient(connection_config=SandboxLocalTunnelConnectionConfig())

    start = time.monotonic()
    # The gVisor node pool may need to scale up from zero, so allow 5 minutes.
    sandbox = client.create_sandbox(
        warmpool=warmpool,
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


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    k8s_config.load_kube_config()
    api = k8s.CustomObjectsApi()

    created: list[tuple[str, str]] = []
    try:
        warmpool = create_pool(api, created)
        return run_sandbox(warmpool)
    finally:
        delete_pool(api, created)


if __name__ == "__main__":
    sys.exit(main())
