"""Real HTTP S3 publication qualification; never loads a saved publish profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from botocore.exceptions import ClientError

from kamiwaza_extensions.catalog_publisher import CatalogPublisher
from kamiwaza_extensions.profile_manager import PublishProfile
from kamiwaza_extensions.registry_builder import RegistryBuilder

IMAGE = (
    "docker.io/library/python:3.12-alpine@sha256:"
    "b64631e04e4920160c50fbe8d8df828f7f35f06f425cb44aa09bca53e708a35a"
)
BASELINE = [
    ("0.2.0", "1.2.1"),
    ("0.3.0", "1.3.0"),
    ("0.4.0", "1.3.1"),
    ("0.5.0", "1.4.0"),
]
BUCKET = "extension-compatibility-qualification"


def publisher(endpoint):
    """Refuse non-loopback endpoints and override all AWS credential discovery."""
    if urlparse(endpoint).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Qualification S3 endpoint must be loopback")
    os.environ.update(
        AWS_ACCESS_KEY_ID="qualification-only",
        AWS_SECRET_ACCESS_KEY="qualification-only",
        AWS_EC2_METADATA_DISABLED="true",
        AWS_DEFAULT_REGION="us-east-1",
        AWS_CONFIG_FILE="/dev/null",
        AWS_SHARED_CREDENTIALS_FILE="/dev/null",
    )
    os.environ.pop("AWS_SESSION_TOKEN", None)
    os.environ.pop("AWS_PROFILE", None)
    profile = PublishProfile("qualification", "localhost:5000", endpoint, BUCKET, "env")
    return CatalogPublisher(profile, catalog_schema="compat-v1")


def entry(kind, version, minimum, name=None):
    """Build through the real RegistryBuilder, using an immutable public image."""
    name = name or f"compat-proof-{kind}"
    metadata = dict(
        name=name,
        version=version,
        kamiwaza_version=f">={minimum}",
        description="Isolated compatibility qualification",
        extension_type=kind,
        template_type=kind,
        tags=["extension-compatibility-qualification"],
        qualification_metadata={"preserve": True, "minimum": minimum},
    )
    program = (
        "from http.server import BaseHTTPRequestHandler,HTTPServer\n"
        "import json\nclass H(BaseHTTPRequestHandler):\n"
        " def do_GET(self):\n  self.send_response(200)\n"
        "  self.send_header('Content-Type','application/json');self.end_headers()\n"
        f"  self.wfile.write(json.dumps({dict(name=name, version=version)!r}).encode())\n"
        "HTTPServer(('0.0.0.0',8080),H).serve_forever()\n"
    )
    compose = {
        "services": {
            "web": {
                "image": IMAGE,
                "ports": ["8080:8080"],
                "command": ["python", "-u", "-c", program],
            }
        }
    }
    return RegistryBuilder().build_entry(metadata, compose, "localhost:5000", version)


def conditional_checks(client):
    """Prove the running server enforces stale and first-create preconditions."""
    key = f"qualification/cas-probe-{uuid4()}.json"
    first = client.put_object(Bucket=BUCKET, Key=key, Body=b"first", IfNoneMatch="*")
    client.put_object(Bucket=BUCKET, Key=key, Body=b"second", IfMatch=first["ETag"])
    failures = []
    for condition in ({"IfMatch": first["ETag"]}, {"IfNoneMatch": "*"}):
        try:
            client.put_object(Bucket=BUCKET, Key=key, Body=b"clobber", **condition)
        except ClientError as exc:
            code = exc.response["ResponseMetadata"]["HTTPStatusCode"]
            assert code == 412, exc.response
            failures.append(code)
        else:
            raise AssertionError("S3 server ignored conditional PUT")
    assert client.get_object(Bucket=BUCKET, Key=key)["Body"].read() == b"second"
    return failures


def publish_phase(pub, phase):
    releases = BASELINE if phase == "baseline" else [("0.6.0", "1.3.1")]
    for kind in ("app", "service", "tool"):
        for version, minimum in releases:
            pub.publish(entry(kind, version, minimum), kind)
        if phase == "baseline":
            pub.publish(entry(kind, "9.0.0", "9.0.0", f"compat-future-{kind}"), kind)


def concurrent_publication(endpoint):
    """Independent real clients race against one catalog object."""

    def publish_one(index):
        pub = publisher(endpoint)
        return pub.publish(
            entry("app", f"0.1.{index}", "1.3.0", "compat-race-app"), "app"
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(publish_one, range(4)))
    pub = publisher(endpoint)
    rows = json.loads(
        pub._s3.get_object(Bucket=BUCKET, Key="garden/compat-v1/apps.json")[
            "Body"
        ].read()
    )
    assert sorted(r["version"] for r in rows if r["name"] == "compat-race-app") == [
        f"0.1.{i}" for i in range(4)
    ]
    return 4


def export_catalog(pub, output):
    receipts = {}
    for filename in ("apps.json", "tools.json"):
        key = f"garden/compat-v1/{filename}"
        response = pub._s3.get_object(Bucket=BUCKET, Key=key)
        body = response["Body"].read()
        rows = json.loads(body)
        assert all(r["qualification_metadata"]["preserve"] for r in rows)
        assert response["Metadata"]["writer-capability"] == "compat-v1-cas"
        target = output / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.with_suffix(".tmp").write_bytes(body)
        target.with_suffix(".tmp").replace(target)
        receipts[key] = {
            "sha256": hashlib.sha256(body).hexdigest(),
            "etag": response["ETag"],
            "metadata": response["Metadata"],
            "releases": [
                {
                    "name": r["name"],
                    "version": r["version"],
                    "kamiwaza_version": r["kamiwaza_version"],
                }
                for r in rows
            ],
        }
    return receipts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:18333")
    parser.add_argument(
        "--phase", choices=["baseline", "update", "export"], required=True
    )
    parser.add_argument("--http-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    pub = publisher(args.endpoint)
    pub._s3.create_bucket(Bucket=BUCKET)
    receipt = {"phase": args.phase, "endpoint": args.endpoint, "image": IMAGE}
    receipt["conditional_rejections"] = conditional_checks(pub._s3)
    if args.phase != "export":
        publish_phase(pub, args.phase)
    if args.phase == "baseline":
        receipt["concurrent_releases_retained"] = concurrent_publication(args.endpoint)
    receipt["objects"] = export_catalog(pub, args.http_root)
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"receipt": str(args.receipt), "phase": args.phase}))


if __name__ == "__main__":
    main()
