# Run the strict inference lifecycle provider

`kamiwaza-validate-inference` runs the same versioned local-inference scenario
that fleet automation consumes. It does not select pytest markers or accept raw
engine command-line arguments.

The `sdk.inference.lifecycle/v1` scenario proves these eight cases exactly once
for every selected target:

1. catalog discovery
2. download readiness
3. exact model-file and configuration selection
4. explicit-engine deployment
5. deployment readiness
6. OpenAI-compatible multi-turn chat
7. deployment stop
8. residual cleanup

A selected required case that cannot run is `failed`, never `skipped`.

## Prerequisites

- A reachable Kamiwaza gateway and an admin-capable PAT stored in a file. The
  configured `base_url` ends in `/api`, while the same origin must also route
  `/runtime/models/...` for OpenAI-compatible inference. A port-forward to the
  `core-api` service alone does not provide that runtime route. If API and
  runtime traffic use different origins, set `KAMIWAZA_RUNTIME_BASE_URL` to
  the gateway origin before invoking the provider.
- A kubeconfig with read access to pods in the `kamiwaza` namespace.
- `kubectl` on `PATH`.
- An owned-fixture target whose engine, model format, quantization, accelerator,
  architecture, and semantic runtime profile match the version 1 support
  policy. Version 1 accepts `llamacpp` with a recognized GGUF quantization on
  AMD gfx1151, NVIDIA Turing, GB10, or Ampere-and-newer targets. It
  accepts `vllm` with unquantized safetensors on AMD gfx1151, NVIDIA GB10, or
  Ampere-and-newer targets. NVIDIA Turing vLLM remains outside this provider's
  version 1 policy.

Apple M5 targets remain outside version 1 because the runtime observer currently
proves Kubernetes pod state only; the macOS host-process observer is tracked in
the later platform-qualification slice.

External fixture adoption is not implemented by this provider version. An
`external` profile fails during resolution instead of creating or deleting a
deployment under ambiguous ownership.

The kubeconfig is read-only during scenario execution. It is used to bind the
platform deployment ID to the emitted pod and record the pulled image digest
and effective container arguments. Product engine adapters remain the source of
truth for those arguments.

## Create the inputs

Save a validation profile as `profile.json`:

```json
{
  "schema": "kamiwaza.validation-profile/v1",
  "deployment": {
    "provider": "existing-host",
    "topology_id": "single-node-amd",
    "ephemeral": false
  },
  "clusters": [
    {
      "id": "evo-x2-2",
      "roles": ["controller", "inference"],
      "node_count": 1,
      "hardware": {
        "accelerators": [
          {"vendor": "amd", "architecture": "gfx1151", "count": 1}
        ]
      },
      "features": {}
    }
  ],
  "mesh": {"edges": []},
  "validation": {
    "level": "smoke",
    "fixture_mode": "owned",
    "include": ["sdk.inference.lifecycle/v1"],
    "exclude": []
  },
  "inference_targets": [
    {
      "id": "evo-x2-2-llamacpp-chat",
      "cluster_id": "evo-x2-2",
      "required": true,
      "repository": "Qwen/Qwen3-0.6B-GGUF",
      "engine": "llamacpp",
      "model_format": "gguf",
      "quantization": "q8_0",
      "runtime_profile": "product-default",
      "expected_image": null
    }
  ]
}
```

Use an immutable `repository@sha256:...` or `sha256:...` value for
`expected_image` when a run must verify an exact image candidate. Resolution
publishes that image as an install requirement before execution; configuration
owners remain responsible for selecting a candidate compatible with the target
engine and accelerator. The readiness case then proves the running pod pulled
the exact requested digest. The SDK does not claim it can infer compatibility
from an opaque digest alone.

Save the runtime references as `runtime.json`:

```json
{
  "schema": "kamiwaza.runtime-context/v1",
  "run_id": "manual-inference-001",
  "clusters": [
    {
      "id": "evo-x2-2",
      "base_url": "https://evo-x2-2.example/api",
      "api_key_ref": "file:///absolute/path/to/admin.pat",
      "kubeconfig_ref": "file:///absolute/path/to/evo-x2-2.kubeconfig"
    }
  ]
}
```

The provider reads the PAT only from the referenced file. It never writes the
token, kubeconfig, or their contents to the plan, state, evidence, or error
details. A `secret://` reference must be materialized to a temporary `file://`
reference by the invoking orchestrator before the provider process starts.
Every persisted state snapshot is authenticated with a keyed MAC derived in
memory from those materialized API credentials. `run` and `teardown` reject a
changed state or changed credential before issuing product API calls.

## Execute and always clean up

```bash
kamiwaza-validate-inference describe --json

kamiwaza-validate-inference resolve \
  --profile profile.json \
  --plan plan.json

kamiwaza-validate-inference prepare \
  --plan plan.json \
  --runtime runtime.json \
  --state state.json

kamiwaza-validate-inference run \
  --plan plan.json \
  --runtime runtime.json \
  --state state.json \
  --evidence evidence.json

kamiwaza-validate-inference teardown \
  --runtime runtime.json \
  --state state.json \
  --evidence cleanup.json
```

Invoke `teardown` even when `prepare` or `run` returns nonzero. The state file
is written before the first owned mutation and immediately after the deployment
is created, so teardown can reconcile a partial run. A failed `run` still
retains structurally valid `evidence.json`; a failed teardown retains
`cleanup.json` for diagnosis.

`evidence.json` records the requested target facts, actual engine, exact model
and configuration IDs, selected weight files, pulled image digest, effective
runtime arguments, per-case timings, and exact outcomes. It contains no raw PAT
or kubeconfig content.
