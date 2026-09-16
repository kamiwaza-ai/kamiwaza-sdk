# Installed extension compatibility qualification

These opt-in scripts exercise the actual SDK publisher against a running HTTP S3
server and the actual API of an installed Core. They never use production profiles.
The S3 endpoint must be loopback and AWS credentials are replaced with fixed dummy
values. Run them in a disposable VM; the publisher deliberately refuses repeat
publication of the same release unless the S3 server is restarted fresh.

Install `moto[server]==5.1.12`, a current `boto3`, `httpx`, and SDK dependencies in an
isolated virtualenv. Start `moto_server -H 127.0.0.1 -p 18333`. With SDK source on
`PYTHONPATH`, run:

```sh
python catalog_harness.py --phase baseline --http-root /tmp/catalog-staged --receipt /tmp/baseline.json
```

This publishes applications, services, and tools at 0.2.0 requiring Core 1.2.1,
0.3.0 requiring 1.3.0, 0.4.0 requiring 1.3.1, and 0.5.0 requiring 1.4.0. Future-only
fixtures require 9.0.0. It proves stale `IfMatch` and existing `IfNoneMatch` writes
receive HTTP 412, races four independent publishers, and verifies history and
opaque metadata survive. Receipt hashes cover the exact S3 object bytes exported
to the HTTP root. Moto is a protocol emulator, not a production R2 certification.

Serve this root by HTTP and configure the isolated Core's LOCAL catalog origin
and `compat-v1` generation accordingly. Wait until candidate Core is active before
exposing catalogs: old readers do not support overlapping histories. Verify the
canonical installed runtime version separately; the harness version argument is
only a receipt label and does not modify or prove runtime version.

For each fresh canonical Core 1.3.0 / 1.3.1 / 1.4.0, expect extension 0.3.0 / 0.4.0 /
0.5.0. Store an authorized token in a private file, then run:

```sh
python instance_harness.py --base-url https://isolated-host/api --token-file /private/token \
  --runtime-version 1.3.0 --expected 0.3.0 --receipt /tmp/instance-baseline.json
```

The harness checks remote selection, actual sync and persisted templates, future
release exclusion, and repeated sync stability for all three kinds. A successful
receipt requires every assertion. It does not deploy workloads; supplement with
actual deployment of each selected template and observe its HTTP endpoint.

After baseline cases, `catalog_harness.py --phase update` publishes 0.6.0 requiring
Core 1.3.1. Expected selections become 0.3.0 / 0.6.0 / 0.6.0. Preserve baseline
object bytes if cycling cases in one isolated instance. Never implement version
changes by manually changing API response fixtures or database template versions.

The workload uses a digest-pinned official Python image and returns its extension
name/version at port 8080 on every GET path. The digest identifies the runtime
image; each release's immutable compose command identifies its version payload.
