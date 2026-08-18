"""Regression coverage for immutable images in remote dev deployments."""

from __future__ import annotations

from kamiwaza_extensions.dev_image_pinning import (
    pin_compose_image_refs,
    resolve_pushed_image_digests,
)


def test_pushed_service_and_dynamic_worker_refs_are_digest_pinned() -> None:
    api_ref = "registry.cluster.test/team/api:release-123"
    worker_ref = "registry.cluster.test/team/worker:release-123"
    api_push_ref = "registry.host.test/team/api:release-123"
    worker_push_ref = "registry.host.test/team/worker:release-123"
    api_digest = "sha256:" + "a" * 64
    worker_digest = "sha256:" + "b" * 64
    compose = {
        "services": {
            "api": {
                "image": api_ref,
                "environment": {
                    "AGENT_SERVER_IMAGE": worker_ref,
                    "UNCHANGED_PREFIX": "registry.cluster.test/team",
                },
            }
        }
    }

    resolved = {
        api_push_ref: api_digest,
        worker_push_ref: worker_digest,
    }
    pins = resolve_pushed_image_digests(
        [api_ref, worker_ref],
        {api_ref: api_push_ref, worker_ref: worker_push_ref},
        resolved.__getitem__,
    )
    pinned = pin_compose_image_refs(compose, pins)

    assert pinned["services"]["api"]["image"] == f"{api_ref}@{api_digest}"
    assert (
        pinned["services"]["api"]["environment"]["AGENT_SERVER_IMAGE"]
        == f"{worker_ref}@{worker_digest}"
    )
    assert (
        pinned["services"]["api"]["environment"]["UNCHANGED_PREFIX"]
        == "registry.cluster.test/team"
    )
    assert compose["services"]["api"]["image"] == api_ref
