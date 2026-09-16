"""Verify catalog sync preserves templates used by existing deployments."""

import argparse
import json
from pathlib import Path

import httpx
from instance_harness import selected, summarize


def read_state(client, version):
    templates = {}
    for kind in ("app", "service", "tool"):
        path = "/tool/templates" if kind == "tool" else "/apps/app_templates"
        response = client.get(path, params={"template_type": kind})
        response.raise_for_status()
        templates.update(summarize(selected(response.json(), version, [kind])))
    deployments = client.get("/apps/deployments")
    deployments.raise_for_status()
    ids = {row["id"]: row["template_id"] for row in deployments.json()}
    assert all(
        row["id"] in ids.values() for row in templates.values()
    ), "Each proof template must be deployed"
    return {"templates": templates, "deployment_templates": ids}


def verify_update(client, args):
    before = json.loads(args.before.read_text())
    for prefix, plural, kinds in [
        ("apps", "apps", ["app", "service"]),
        ("tool", "tools", ["tool"]),
    ]:
        response = client.get(
            f"/{prefix}/remote/{plural}", params={"force_refresh": "true"}
        )
        response.raise_for_status()
        selected(response.json(), args.remote_version, kinds)
        response = client.post(
            f"/{prefix}/remote/sync",
            json={"names": [f"compat-proof-{kind}" for kind in kinds]},
        )
        response.raise_for_status()
        assert not response.json().get("errors"), response.json()
    after = read_state(client, args.retained_version)
    assert (
        before == after
    ), "Catalog update mutated deployed template identity or deployment references"
    return {
        "before": before,
        "after": after,
        "selected_remote_version": args.remote_version,
        "retained_active_version": args.retained_version,
        "unchanged": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--retained-version", required=True)
    parser.add_argument("--remote-version", default="0.6.0")
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    with httpx.Client(
        base_url=args.base_url.rstrip("/"),
        verify=False,
        timeout=120,
        headers={"Authorization": f"Bearer {args.token_file.read_text().strip()}"},
    ) as client:
        if args.receipt:
            args.receipt.write_text(
                json.dumps(verify_update(client, args), indent=2) + "\n"
            )
        else:
            args.before.write_text(
                json.dumps(read_state(client, args.retained_version), indent=2) + "\n"
            )
    print("active template snapshot/guard passed")


if __name__ == "__main__":
    main()
