# Extension Failure Classes — Runbook

When an extension cannot serve a request, the runtime libs raise one of four
canonical exception classes. Each class has a stable name, a CLI exit code,
and a fix hint surfaced by `kz-ext doctor`. This runbook explains each class,
its typical causes, and how to fix it.

## At a glance


| Class name               | Python / TS exception      | CLI exit code | `kz-ext doctor` hint                          |
| ------------------------ | -------------------------- | ------------- | --------------------------------------------- |
| `misbound_auth`          | `MisboundAuthError`        | 10            | Required envelope header missing or malformed |
| `unexpected_context`     | `UnexpectedContextError`   | 11            | Envelope shape wrong for this context         |
| `out_of_envelope_access` | `OutOfEnvelopeAccessError` | 12            | Cross-workroom / out-of-scope access attempt  |
| `platform_outage`        | `PlatformOutageError`      | 13            | Platform API unreachable or returning 5xx     |


The `class_name` strings are canonical — they appear in:

- The JSON body returned by non-SDK extensions (under `{"error": {"class": ...}}`).
- `kamiwaza_extensions_lib/exception_names.json` (single source of truth).
- `kz-ext doctor` output (as `Failure class: <class_name>` reference entries).

## `misbound_auth` — exit 10

**What it means:** A required envelope header (`X-User-Id` or `X-Workroom-Id`)
is missing or empty at the time the runtime lib tried to parse the request.

**Typical causes:**

- The request reached the extension pod *without* passing through Traefik
(e.g., direct in-cluster call, port-forward, or misconfigured ingress).
- The platform ForwardAuth layer is not injecting the envelope correctly
(transient platform outage or misconfiguration).
- Local development with `KAMIWAZA_USE_AUTH=true` but no platform to populate
the envelope — expected failure mode.

**Fix:**

1. Confirm the request is reaching the extension *through* Traefik. Check
  ingress rules and the request URL.
2. Run `kz-ext doctor` — a failed `Kamiwaza connection` check often precedes
  `misbound_auth`.
3. For local dev, set `KAMIWAZA_USE_AUTH=false` and restart the extension.

**Code example:**

```python
from kamiwaza_extensions_lib.errors import MisboundAuthError
from kamiwaza_extensions_lib.identity import extract_identity

try:
    identity = extract_identity(dict(request.headers))
except MisboundAuthError as exc:
    # Return 401 to the caller — do not leak internal details.
    return JSONResponse(
        {"error": {"class": exc.class_name, "detail": "Authentication required"}},
        status_code=401,
    )
```

Note: the built-in `require_auth()` FastAPI dependency handles this for you —
you only need to catch `MisboundAuthError` if you are parsing headers manually
or implementing a non-SDK extension.

## `unexpected_context` — exit 11

**What it means:** The envelope headers are present but the shape or content
doesn't match the extension's expected runtime context.

**Typical causes:**

- Running a local-dev envelope against a production-signed extension (rare;
extensions no longer verify HMAC as of 2026-04-23, but shape mismatches
still apply).
- Envelope fields present but all empty — platform misconfiguration.

**Fix:**

1. Compare `request.headers` against the list in
  `kamiwaza-sdk/docs/extensions/non-sdk-flow.md` (when available) or
   `kamiwaza_extensions_lib/identity.py`.
2. Check that `X-User-Id` and `X-Workroom-Id` are non-empty strings, not
  placeholder tokens.
3. Verify the deployment environment — a PR preview env often has different
  envelope provenance than staging or prod.

## `out_of_envelope_access` — exit 12

**What it means:** The extension attempted to access a resource the envelope
does not cover — typically a cross-workroom access attempt.

**Typical causes:**

- The extension is resolving a workroom ID from user input and calling the
platform, but the caller does not have membership in that workroom.
- A stale token referencing a deleted workroom.
- Extension logic that assumes admin-like access when the caller is a
workroom viewer.

**Fix:**

1. Always thread the request's `Identity.workroom_id` through platform API
  calls — don't substitute a workroom ID from user input without re-checking
   membership.
2. The platform enforces workroom membership at data-access time; the
  extension should surface a friendly error, not try to work around it.
3. Code example:

```python
from kamiwaza_extensions_lib.errors import OutOfEnvelopeAccessError

try:
    result = client.workrooms.get_resource(identity.workroom_id, resource_id)
except OutOfEnvelopeAccessError:
    return JSONResponse(
        {"error": {"class": "out_of_envelope_access",
                   "detail": "You do not have access to this workroom"}},
        status_code=403,
    )
```

## `platform_outage` — exit 13

**What it means:** The platform API is unreachable or returning 5xx responses.

**Typical causes:**

- Platform is down or degraded (check Kamiwaza status).
- Network partition between the extension and the platform — especially in
split-cluster deployments.
- Upstream model-access boundary is failing (5xx propagation).

**Fix:**

1. Run `kz-ext doctor` — the `Kamiwaza connection` check will fail in the
  same direction.
2. Check the platform status page / oncall channels.
3. For transient failures, the runtime lib's `TokenRefreshMiddleware` already
  retries once on 401 mid-stream. A double failure surfaces as
   `PlatformOutageError`.
4. Do **not** catch and swallow — let it bubble to the top-level error
  handler so the exit code is preserved.

## How the exit codes flow

1. The runtime lib raises a `KamiwazaRuntimeError` subclass.
2. If the exception bubbles into a `kz-ext` subcommand, the CLI's top-level
  `_handle_exception` catches it and calls `exit_code_for(exc.class_name)`.
3. The process exits with the canonical code from `exception_names.json`.

CI and operators can key off the exit code alone:

```bash
kz-ext dev
case $? in
  0)  echo "OK" ;;
  10) echo "Platform envelope missing — check Traefik" ;;
  11) echo "Envelope shape wrong — check deployment context" ;;
  12) echo "Cross-workroom access — extension logic bug" ;;
  13) echo "Platform outage — retry later" ;;
  23) echo "Cluster operator not ready — run kz-ext doctor" ;;
  *)  echo "Generic failure ($?)" ;;
esac
```

Exit code 23 (`CLUSTER_NOT_READY`) is a related but distinct failure — it
comes from the `cluster_extension_readiness` probe in `kz-ext doctor`, not
from a runtime-lib exception. See ENG-3888 / §4.2.8 B1a.

## The single-source-of-truth invariant

If you add a new exception class to the hierarchy, update
`kamiwaza_extensions_lib/exception_names.json` in the same commit. The
`test_doctor_hint_coverage.py` contract test will fail loudly if the JSON
drifts from either the `ExitCode` enum or the `kz-ext doctor` output.

## Design references

- `§4.2.7 RuntimeLibExceptionHierarchy + IdentityExtractor + TokenRefreshMiddleware`
- `§4.2.8 DoctorUACFailureHints + ExitCodeMap`
- `§5 Q6 Security implications and auth interactions`
- Linear: [ENG-3885](https://linear.app/kamiwaza/issue/ENG-3885)

