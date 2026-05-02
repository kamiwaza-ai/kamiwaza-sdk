# Kamiwaza Extension Authoring Guidance

You are an AI agent helping convert an existing application into a valid
Kamiwaza extension via `kz-ext convert`. Read this entire file before
proposing modifications. The per-call prompt explains the *task*; this
file is the *contract*.

When you are running as a CLI agent (Claude Code, Codex CLI), this file
is loaded as `CLAUDE.md` / `AGENTS.md` in your working directory and is
the canonical source of truth. The per-call prompt may add task-specific
context but does not override these rules.

---

## What a valid Kamiwaza extension looks like

Every extension must end up with:

1. **`kamiwaza.json`** — manifest at the extension root. Minimum schema:
   `name`, `version`, `type` (or legacy `template_type`) ∈ `{"app", "tool", "service"}`,
   `source_type`, `visibility`, `description`, `risk_tier`,
   `kz_ext_version` (or legacy `kamiwaza_version`).
2. **`docker-compose.yml`** at the extension root. Each service must
   declare `deploy.resources.limits` (cpus + memory) and a healthcheck
   on the primary HTTP service.
3. **One Dockerfile per service** that builds non-root and is compatible
   with a read-only root filesystem at runtime.
4. **`CONVERT_NOTES.md`** summarizing what the conversion changed.

Validation at the end of conversion runs `kz-ext validate` against the
staged output. If it fails, you will get a "Validation Feedback" section
in your next prompt and must repair.

---

## Runtime contract (read this carefully)

Kamiwaza deploys extension containers as **non-root** with a
**read-only root filesystem**. This drives several patterns:

### Distroless / minimal base images

Prefer `cgr.dev/kamiwaza/python:<ver>` (runtime) and
`cgr.dev/kamiwaza/python:<ver>-dev` (build) for Python services.
Same for `cgr.dev/kamiwaza/node:<ver>` and `:<ver>-dev`. These are
Chainguard-style minimal images.

The runtime images **do not include `/bin/sh`, `apt`, package managers,
or most system utilities**. This means:

- **Never use shell-form `CMD` or `ENTRYPOINT`** in runtime stages —
  always exec form (`CMD ["python", "-m", ...]`, not `CMD python -m ...`).
- **Never use shell-form `HEALTHCHECK`** — always
  `HEALTHCHECK CMD ["python", "-c", "..."]`, not
  `HEALTHCHECK CMD curl -f http://localhost/health`.
- **Compose healthchecks** must use array form starting with `"CMD"`
  (not `"CMD-SHELL"`): `test: ["CMD", "python", "-c", "..."]`.
- Do not invoke `sh`, `bash`, `curl`, `wget`, `apt-get`, etc. in the
  runtime stage. Install everything in the `-dev` build stage and copy
  the artifacts forward.
- Do not assume `/etc/passwd` has a `nobody` user — Chainguard uses
  UID `65532` (`nonroot`); use that explicitly: `USER 65532:65532`.

If the existing application genuinely requires a shell at runtime
(rare), call this out in `manual_items` and pick a non-distroless base
(e.g., `python:3.11-slim`). Default to distroless.

### Read-only root filesystem

The deployment may set the root filesystem read-only. Anything that
needs to write at runtime must target an explicitly writable mount:

- **`/tmp` is writable** by convention. Set `TMPDIR=/tmp`,
  `HOME=/tmp`, `XDG_CACHE_HOME=/tmp` as needed.
- **Next.js**: the `.next` directory must be writable for runtime
  caches. The standard pattern is to build into `/app/.next-template`
  in the build stage, then have the entrypoint copy it to a writable
  location at startup (e.g., `/tmp/.next` with `NEXT_DIST_DIR=/tmp/.next`,
  or a tmpfs mount on `/app/.next`).
- **SQLite** and other on-disk state: confirm the path resolves under
  `/tmp` or a declared volume; never assume the working directory is
  writable.

### Ports

- Prefer an unprivileged in-container port — `8080` is the platform
  convention for HTTP. If the existing app uses `3000` / `8000` and
  you have no reason to remap, leave it; just don't introduce ports
  below 1024.
- In compose, list ports as `"<container-port>"` (just the container
  port, no host:container mapping). The platform handles host port
  assignment.

### Multi-stage builds

The standard shape:

```dockerfile
# syntax=docker/dockerfile:1
ARG CG_PYTHON_DEV_IMAGE=cgr.dev/kamiwaza/python:3.11-dev
ARG CG_PYTHON_IMAGE=cgr.dev/kamiwaza/python:3.11

FROM ${CG_PYTHON_DEV_IMAGE} AS build
USER root
WORKDIR /build
RUN python -m venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}" PIP_NO_CACHE_DIR=1
COPY backend/requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt
COPY backend/app ./app

FROM ${CG_PYTHON_IMAGE} AS runtime
WORKDIR /app
COPY --from=build --chown=65532:65532 /app/.venv /app/.venv
COPY --from=build --chown=65532:65532 /build/app /app/app
ENV PATH="/app/.venv/bin:${PATH}" PYTHONUNBUFFERED=1
USER 65532:65532
EXPOSE 8000
HEALTHCHECK CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5).status == 200 else 1)"]
ENTRYPOINT ["python"]
CMD ["-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Mirror for Node / TypeScript with the `cgr.dev/kamiwaza/node` images.

---

## Vendoring (the `copy` action)

When converting from a monorepo, the original application may depend on
shared artifacts that live outside the extension directory (wheels,
tarballs, source packages). The extension must build standalone, so
vendor those artifacts into the extension via the `copy` action:

```json
{
  "path": "backend/vendor/auth-1.0.whl",
  "action": "copy",
  "source_path": "shared/python/dist/auth-1.0.whl",
  "description": "Vendor shared auth wheel for standalone build"
}
```

`source_path` is relative to the original CLI directory (the monorepo
root in rebased conversions). The available files are listed in the
**Source Tree Outside Extension Root** section of the prompt.

If the prebuilt artifact is missing but the source is available, vendor
the source and build the artifact in-image (see the multi-`copy` pattern
the converter has used to vendor entire `kamiwaza_auth/` source trees).

After vendoring, **update the Dockerfile / compose / package.json**
references to point at the in-extension paths. A vendor without the
matching reference update is a half-finished change.

---

## `manual_items` discipline

`manual_items` is for follow-up the **user** must do. Aim to leave it
empty.

**DO NOT** restate work you have scheduled as a modification. If you
emitted a `copy` for `shared/foo.whl` and modified the Dockerfile to
reference it, do not also write `"vendor shared/foo.whl"` as a manual
item. The post-processor strips the most obvious cases but you should
not rely on it.

**DO NOT** include informational notes about preserved files
(e.g., "kamiwaza.json preserved") — those go in the `summary` or
`CONVERT_NOTES.md`.

**DO** include items where the user genuinely has a choice or must
verify something at runtime (e.g., "decide whether to keep ports
3000/8000 or remap to 8080", "verify the SQLite path resolves under
`/tmp` after first request").

---

## What you do not own

- **The conversion mode** (`preserve_existing_runtime`,
  `add_minimal_wrapper`, `containerize_repo_root`, `multi_service`)
  is decided in the strategy step. Stick with it in the modification
  step.
- **The metadata seed** (`kamiwaza.json` content) is provided. You may
  modify the manifest if needed but do not invent fields.
- **Existing user source files** are preserved. Add wrappers / config /
  Dockerfiles around them, do not rewrite them.

---

## Output format

Always return ONLY the JSON envelope specified in the per-call prompt.
No preamble, no explanation outside the JSON. The orchestrator parses
fenced ```json``` blocks.
