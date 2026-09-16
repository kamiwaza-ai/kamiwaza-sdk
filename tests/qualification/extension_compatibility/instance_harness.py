"""Verify selected remote catalogs and persisted templates on an installed Core."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import httpx


def selected(rows, expected, kinds):
    proof = {r["name"]: r for r in rows if r["name"].startswith("compat-proof-")}
    assert len(proof) == len(kinds), list(proof)
    for kind in kinds:
        assert proof[f"compat-proof-{kind}"]["version"] == expected, proof
    assert not any(r["name"].startswith("compat-future-") for r in rows), rows
    return proof


def summarize(rows):
    result = {}
    for name, row in rows.items():
        compose = row.get("compose_yml", "")
        result[name] = {
            "id": row.get("id"),
            "version": row["version"],
            "kamiwaza_version": row.get("kamiwaza_version"),
            "compose_sha256": hashlib.sha256(compose.encode()).hexdigest(),
            "docker_images": row.get("docker_images"),
        }
    return result


def verify_kind(client, options):
    prefix, plural, kinds, expected = options
    remote = client.get(f"/{prefix}/remote/{plural}", params={"force_refresh": "true"})
    remote.raise_for_status()
    candidates = selected(remote.json(), expected, kinds)
    names = [f"compat-proof-{kind}" for kind in kinds]
    names += [f"compat-future-{kind}" for kind in kinds]
    syncs = []
    identities = []
    path = "/apps/app_templates" if prefix == "apps" else "/tool/templates"
    for _ in range(2):
        sync = client.post(f"/{prefix}/remote/sync", json={"names": names})
        sync.raise_for_status()
        syncs.append(sync.json())
        assert not sync.json().get("errors"), sync.json()
        stored_rows = []
        for kind in kinds:
            stored = client.get(path, params={"template_type": kind})
            stored.raise_for_status()
            stored_rows.extend(stored.json())
        records = selected(stored_rows, expected, kinds)
        identities.append(summarize(records))
    assert (
        identities[0] == identities[1]
    ), "Repeated sync changed persisted template identity"
    return {
        "remote": summarize(candidates),
        "persisted": identities[-1],
        "syncs": syncs,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url", required=True, help="Installed Core API root, ending /api"
    )
    parser.add_argument(
        "--token-file", type=Path, required=True, help="Private raw bearer token file"
    )
    parser.add_argument(
        "--runtime-version",
        required=True,
        help="Label; runtime proof supplied separately",
    )
    parser.add_argument("--expected", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    token = args.token_file.read_text().strip()
    receipt = {"runtime_case": args.runtime_version, "expected": args.expected}
    with httpx.Client(
        base_url=args.base_url.rstrip("/"),
        verify=False,
        timeout=120,
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        receipt["apps"] = verify_kind(
            client, ("apps", "apps", ("app", "service"), args.expected)
        )
        receipt["tools"] = verify_kind(
            client, ("tool", "tools", ("tool",), args.expected)
        )
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"receipt": str(args.receipt), "verified": True}))


if __name__ == "__main__":
    main()
