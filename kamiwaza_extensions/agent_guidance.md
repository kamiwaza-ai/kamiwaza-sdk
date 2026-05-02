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
**read-only root filesystem**. These rules apply regardless of which
base image the extension uses:

- **Run as a non-root user**: end the runtime stage with a `USER`
  directive (e.g., `USER 1000:1000`, `USER nobody`, `USER 65532:65532`
  — pick whatever the base image actually provides).
- **No writes to the root filesystem at runtime**: anything that needs
  to write must target `/tmp` or an explicitly declared volume. See
  the read-only root section below.
- **Healthchecks must work** under the chosen base image (i.e., the
  binaries the healthcheck invokes must actually be present).

### Choosing a base image

**Respect the existing base image when it works.** If the source
extension already uses `python:3.11-slim`, `node:22-alpine`, etc., and
its Dockerfile is otherwise compliant with the runtime contract above,
do not swap the base image. Just patch what's broken (e.g., add a
`USER` line if it ran as root, point cache dirs at `/tmp`).

**For greenfield containerization** — when the source has no
Dockerfile and you're generating one from scratch — Kamiwaza ships
hardened Chainguard-distroless variants tuned for the runtime
contract: `cgr.dev/kamiwaza/python:<ver>` /
`cgr.dev/kamiwaza/python:<ver>-dev` for Python services and
`cgr.dev/kamiwaza/node:<ver>` / `:<ver>-dev` for Node. They are a good
default but not mandatory.

**Only swap an existing base image** when it is genuinely incompatible
with the runtime contract (e.g., the upstream image hard-codes root,
no `-slim` variant exists, package install requires write access to
`/`). Note the swap and the reason in `CONVERT_NOTES.md`.

### Distroless gotchas (apply when using a Chainguard image)

The Chainguard runtime images **do not include `/bin/sh`, `apt`,
package managers, or most system utilities**. Whenever the runtime
stage uses one (whether you chose it or the source already did):

- **Never use shell-form `CMD` or `ENTRYPOINT`** — always exec form
  (`CMD ["python", "-m", ...]`, not `CMD python -m ...`).
- **Never use shell-form `HEALTHCHECK`** — always
  `HEALTHCHECK CMD ["python", "-c", "..."]`, not
  `HEALTHCHECK CMD curl -f http://localhost/health`.
- **Compose healthchecks** must use array form starting with `"CMD"`
  (not `"CMD-SHELL"`): `test: ["CMD", "python", "-c", "..."]`.
- Do not invoke `sh`, `bash`, `curl`, `wget`, `apt-get` etc. in the
  runtime stage. Install everything in the `-dev` build stage and
  copy artifacts forward.
- Use `USER 65532:65532` (Chainguard's `nonroot`) — `nobody` may not
  be defined in `/etc/passwd`.

These rules are *also* good practice on slim/alpine bases (exec form
is just better) but they are *required* on Chainguard distroless.

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
the source and import it via **PYTHONPATH** (Python) or
**node_modules bind/copy** (Node) — do **not** try to `pip wheel` or
`npm pack` the vendored source in-image. Source trees from monorepos
often have build configs that depend on workspace tooling (workspace
hoisting, hatch / setuptools custom layouts, monorepo-wide
`packages = [...]` overrides). When you copy just the source files,
that build config rarely works standalone.

The robust, build-system-agnostic pattern for vendored Python source:

1. Copy the package source under `backend/vendor/<pkgname>/` so the
   resulting layout has `backend/vendor/<pkgname>/__init__.py` etc.
2. In the runtime stage of the Dockerfile, ensure `/app/vendor` is on
   `PYTHONPATH`: add `ENV PYTHONPATH=/app/vendor:${PYTHONPATH}` (or
   include `/app/vendor` in an existing `PYTHONPATH` line).
3. Strip the vendored package from `requirements.txt` so pip doesn't
   try to fetch a published version that doesn't exist.

For vendored Node packages where the upstream ships a tarball, the
tarball is already a self-contained installable artifact — keep using
`file:` references in `package.json` and let `npm install` consume the
copied tarball during the build stage.

For vendored Node packages where you only have the source (no
tarball), bind-mount or copy the source into
`/app/node_modules/@scope/pkg` directly, the same way
`dev local --sdk-repo` does.

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
