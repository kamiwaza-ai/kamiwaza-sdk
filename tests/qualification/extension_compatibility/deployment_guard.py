"""Attempt an incompatible template deployment; preserve existing deployments."""

import argparse
import json
from pathlib import Path
from uuid import uuid4

import httpx


def verify_guard(client, reference):
    """Clone a proven template with a future minimum and require refusal."""
    name = f"compat-blocked-{uuid4().hex[:8]}"
    payload = {
        "name": name,
        "version": reference["version"],
        "compose_yml": reference["compose_yml"],
        "source_type": "kamiwaza",
        "template_type": "app",
        "kamiwaza_version": ">=9.0.0",
        "visibility": "public",
        "validate_containers": False,
    }
    created = client.post("/apps/app_templates", json=payload)
    created.raise_for_status()
    template = created.json()
    assert template["kamiwaza_version"] == ">=9.0.0", template
    before = client.get("/apps/deployments")
    before.raise_for_status()
    attempted = client.post(
        "/apps/deploy_app",
        json={
            "name": name,
            "template_id": template["id"],
            "is_ephemeral_session": False,
        },
    )
    assert attempted.status_code == 400, attempted.text
    after = client.get("/apps/deployments")
    after.raise_for_status()
    assert {row["id"] for row in before.json()} == {row["id"] for row in after.json()}
    deleted = client.delete(f"/apps/app_templates/{template['id']}")
    deleted.raise_for_status()
    return {
        "template_name": name,
        "template_id": template["id"],
        "minimum": ">=9.0.0",
        "deployment_http_status": attempted.status_code,
        "response": attempted.json(),
        "deployment_ids_unchanged": True,
        "template_removed": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    with httpx.Client(
        base_url=args.base_url.rstrip("/"),
        verify=False,
        timeout=120,
        headers={"Authorization": f"Bearer {args.token_file.read_text().strip()}"},
    ) as client:
        templates = client.get("/apps/app_templates")
        templates.raise_for_status()
        reference = next(
            row for row in templates.json() if row["name"] == "compat-proof-app"
        )
        result = verify_guard(client, reference)
    args.receipt.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"receipt": str(args.receipt), "verified": True}))


if __name__ == "__main__":
    main()
