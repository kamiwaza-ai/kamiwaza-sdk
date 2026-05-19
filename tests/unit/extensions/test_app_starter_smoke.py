"""Smoke tests for the app starter and reference chatbot example."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from kamiwaza_extensions.scaffolder import Scaffolder

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_DIR = REPO_ROOT / "examples" / "chatbot-app"
LOCAL_TS_LIB = REPO_ROOT / "kamiwaza-ai-extensions-lib"
LOCAL_TS_RUNTIME_ENTRYPOINTS = (
    LOCAL_TS_LIB / "dist" / "client" / "index.js",
    LOCAL_TS_LIB / "dist" / "server" / "index.js",
)

SYNC_FILES = [
    Path(".gitignore"),
    Path("AGENTS.md"),
    Path("CLAUDE.md"),
    Path("backend/Dockerfile"),
    Path("backend/app/main.py"),
    Path("backend/requirements.txt"),
    Path("docker-compose.yml"),
    Path("frontend/Dockerfile"),
    Path("frontend/next.config.js"),
    Path("frontend/package.json"),
    Path("frontend/public/kmza-icon.png"),
    Path("frontend/postcss.config.js"),
    Path("frontend/src/app/api/[...path]/route.ts"),
    Path("frontend/src/app/auth/login-url/route.ts"),
    Path("frontend/src/app/auth/logout/route.ts"),
    Path("frontend/src/app/globals.css"),
    Path("frontend/src/app/layout.tsx"),
    Path("frontend/src/app/logged-out/page.tsx"),
    Path("frontend/src/app/page.tsx"),
    Path("frontend/src/app/providers.tsx"),
    Path("frontend/src/app/session/route.ts"),
    Path("frontend/src/components/NavBar.tsx"),
    # ENG-4318 — chatbot example must ship the local-dev auth bridge
    # middleware so `kz-ext dev local --auth` works against the example
    # the same way it works against a freshly-scaffolded app. Round-4
    # review caught the example shipping the env passthrough but not
    # the middleware to consume it.
    Path("frontend/src/middleware.ts"),
    Path("frontend/start.mjs"),
    Path("frontend/tailwind.config.ts"),
    Path("frontend/tsconfig.json"),
]

BINARY_SYNC_FILES = {
    Path("frontend/public/kmza-icon.png"),
}


def _scaffold_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str = "chatbot-app") -> Path:
    target = tmp_path / "scaffolded-app"
    target.mkdir()
    monkeypatch.chdir(target)
    with patch("subprocess.run"):
        Scaffolder().create(type_="app", name=name)
    return target


def _copy_example(tmp_path: Path) -> Path:
    target = tmp_path / "chatbot-app"
    shutil.copytree(EXAMPLE_DIR, target)
    return target


def _point_frontend_to_local_runtime(frontend_dir: Path) -> None:
    package_json_path = frontend_dir / "package.json"
    package_json = json.loads(package_json_path.read_text())
    package_json["dependencies"]["@kamiwaza-ai/extensions-lib"] = f"file:{LOCAL_TS_LIB}"
    package_json_path.write_text(f"{json.dumps(package_json, indent=4)}\n")


def _run(command: list[str], cwd: Path) -> None:
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Command failed in {cwd}: {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


def _ensure_local_ts_runtime_built() -> None:
    if all(entrypoint.exists() for entrypoint in LOCAL_TS_RUNTIME_ENTRYPOINTS):
        return

    _run(["npm", "install"], LOCAL_TS_LIB)
    _run(["npm", "run", "build"], LOCAL_TS_LIB)

    missing_entrypoints = [str(entrypoint) for entrypoint in LOCAL_TS_RUNTIME_ENTRYPOINTS if not entrypoint.exists()]
    if missing_entrypoints:
        raise AssertionError(
            "Local TypeScript runtime build did not produce expected entrypoints:\n"
            + "\n".join(missing_entrypoints)
        )


def _load_backend_module(backend_dir: Path, module_name: str):
    main_path = backend_dir / "app" / "main.py"
    spec = importlib.util.spec_from_file_location(module_name, main_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _exercise_backend_chat(extension_dir: Path, monkeypatch: pytest.MonkeyPatch, module_name: str) -> None:
    module = _load_backend_module(extension_dir / "backend", module_name)
    seen: dict[str, object] = {}

    async def fake_list_available_models(_request):
        return [
            SimpleNamespace(
                id="dep-1",
                name="Qwen smoke test",
                repo_id=None,
                _extra={"endpoint": "https://kamiwaza.test/runtime/models/dep-1/v1"},
            )
        ]

    class FakeCompletion:
        def model_dump(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": "Hello from smoke test",
                        }
                    }
                ]
            }

    class FakeCompletions:
        async def create(self, model, messages):
            seen["model"] = model
            seen["messages"] = messages
            return FakeCompletion()

    class FakeChatClient:
        def __init__(self):
            self.chat = type("ChatNamespace", (), {"completions": FakeCompletions()})()

    async def fake_build_chat_client(_request, _endpoint):
        return FakeChatClient()

    monkeypatch.setattr(module, "list_available_models", fake_list_available_models)
    monkeypatch.setattr(module, "_build_chat_client", fake_build_chat_client)
    module.app.dependency_overrides[module.require_auth] = lambda: object()

    try:
        client = TestClient(module.app)
        response = client.post(
            "/api/chat",
            json={
                "model": "dep-1",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == "Hello from smoke test"
        assert seen["model"] == "kamiwaza"
    finally:
        module.app.dependency_overrides.clear()
        sys.modules.pop(module_name, None)


@pytest.mark.unit
def test_chatbot_example_matches_scaffolded_app_core_files(tmp_path, monkeypatch):
    scaffolded = _scaffold_app(tmp_path, monkeypatch)

    for relative_path in SYNC_FILES:
        scaffolded_path = scaffolded / relative_path
        example_path = EXAMPLE_DIR / relative_path
        if relative_path in BINARY_SYNC_FILES:
            assert scaffolded_path.read_bytes() == example_path.read_bytes()
        else:
            assert scaffolded_path.read_text() == example_path.read_text()


@pytest.mark.unit
def test_template_chat_endpoint_uses_container_routable_url_under_auth_split(
    tmp_path, monkeypatch
):
    """PR #87 round-8 review (claude + comprehensive consensus) — under
    `kz-ext dev local --auth` the runner sets:
        KAMIWAZA_API_URL=http://host.docker.internal:8000/api  (container)
        KAMIWAZA_PUBLIC_API_URL=http://localhost:8000          (browser)

    The template's `_normalize_model_endpoint` must build a chat
    endpoint reachable from INSIDE the backend container — i.e.
    container-routable, NOT browser-routable. Round-8 caught the
    template's local URL helper was still preferring `public_api_url`,
    so the resulting endpoint pointed at `localhost` which the
    container can't reach.

    Locks the fix in by exercising the real `_normalize_model_endpoint`
    function from the scaffolded backend and asserting the resolved
    chat endpoint contains `host.docker.internal` and NOT `localhost`.
    """
    scaffolded = _scaffold_app(tmp_path, monkeypatch)

    # Simulate the round-5 split env that --auth produces.
    monkeypatch.setenv(
        "KAMIWAZA_API_URL", "http://host.docker.internal:8000/api"
    )
    monkeypatch.setenv("KAMIWAZA_PUBLIC_API_URL", "http://localhost:8000")
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    monkeypatch.setenv("KZ_EXT_DEV_LOCAL_AUTH", "1")

    module = _load_backend_module(
        scaffolded / "backend", "scaffolded_split_env"
    )

    # Path 1: access_path-based endpoint (the typical platform shape).
    endpoint = module._normalize_model_endpoint(
        endpoint="",
        access_path="/runtime/models/dep-1",
    )
    assert "host.docker.internal" in endpoint, (
        f"access_path-built endpoint {endpoint!r} should be "
        "container-routable, not browser-facing localhost"
    )
    assert "localhost" not in endpoint

    # Path 2: pre-built endpoint (the rare, lib-supplied browser URL
    # path). Round-8 review High #3 — the template should re-host the
    # browser URL onto the container-routable base instead of passing
    # the browser URL through verbatim.
    rehosted = module._normalize_model_endpoint(
        endpoint="http://localhost:8000/runtime/models/dep-1/v1",
        access_path="",
    )
    assert "host.docker.internal" in rehosted, (
        f"pre-built endpoint {rehosted!r} should have its host "
        "rewritten to container-routable"
    )
    assert "localhost" not in rehosted

    sys.modules.pop("scaffolded_split_env", None)


@pytest.mark.unit
def test_template_chat_endpoint_preserves_backend_path_prefix(
    tmp_path, monkeypatch
):
    """PR #87 round-9 review High (Comprehensive) regression — when
    ``KAMIWAZA_API_URL`` carries an ingress sub-path (e.g.
    ``https://gateway.example.com/foo/api`` for an ext-instance behind
    a path-rewriting reverse proxy), ``_normalize_model_endpoint`` must
    preserve that prefix when re-hosting a browser-facing endpoint
    onto the container-routable base. Prior to round-9 the re-host
    only swapped scheme+netloc and silently dropped the ``/foo``
    prefix — the resulting AsyncOpenAI base URL hit the gateway root
    instead of the ext-instance ingress.

    Round-10 adds direct test coverage (round-9 fix shipped without
    a test, so a regression here would be undetectable).
    """
    scaffolded = _scaffold_app(tmp_path, monkeypatch)

    monkeypatch.setenv(
        "KAMIWAZA_API_URL", "https://gateway.example.com/foo/api"
    )
    monkeypatch.setenv(
        "KAMIWAZA_PUBLIC_API_URL", "https://gateway.example.com/foo"
    )
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")

    module = _load_backend_module(
        scaffolded / "backend", "scaffolded_path_prefix"
    )

    rehosted = module._normalize_model_endpoint(
        endpoint="https://gateway.example.com/runtime/models/dep-1/v1",
        access_path="",
    )

    # Path prefix must survive the re-host: gateway/foo/runtime/...
    assert "/foo/runtime/models/dep-1/v1" in rehosted, (
        f"path prefix dropped from re-host: {rehosted!r}"
    )
    assert rehosted.startswith("https://gateway.example.com/foo/"), (
        f"unexpected scheme/host: {rehosted!r}"
    )

    sys.modules.pop("scaffolded_path_prefix", None)


@pytest.mark.unit
def test_template_chat_endpoint_does_not_double_prefix_already_prefixed_endpoint(
    tmp_path, monkeypatch
):
    """PR #87 round-11 review Critical (codex) regression — round-9's
    path-prefix preservation MUST NOT double-prepend when the input
    endpoint already carries the ingress prefix.

    Production case: deployment metadata emits a fully-qualified
    ``endpoint`` value already under the ingress (e.g.
    ``https://gateway.example.com/foo/runtime/models/dep-1/v1``) AND
    ``backend_base`` is ``https://gateway.example.com/foo``. Round-9's
    unconditional prepend produced ``/foo/foo/runtime/...`` —
    AsyncOpenAI then hit the wrong path behind path-prefixed ingress.
    """
    scaffolded = _scaffold_app(tmp_path, monkeypatch)

    monkeypatch.setenv(
        "KAMIWAZA_API_URL", "https://gateway.example.com/foo/api"
    )
    monkeypatch.setenv(
        "KAMIWAZA_PUBLIC_API_URL", "https://gateway.example.com/foo/api"
    )
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")

    module = _load_backend_module(
        scaffolded / "backend", "scaffolded_already_prefixed"
    )

    # Endpoint already carries ``/foo`` — re-host must NOT re-add it.
    rehosted = module._normalize_model_endpoint(
        endpoint="https://gateway.example.com/foo/runtime/models/dep-1/v1",
        access_path="",
    )

    assert "/foo/foo/" not in rehosted, (
        f"path prefix duplicated in re-host: {rehosted!r}"
    )
    assert rehosted == "https://gateway.example.com/foo/runtime/models/dep-1/v1", (
        f"unexpected re-host shape: {rehosted!r}"
    )

    # Sanity: when endpoint *doesn't* carry the prefix, it still gets prepended
    # (round-9 fix still works for the original "browser URL re-host" case).
    rehosted_browser = module._normalize_model_endpoint(
        endpoint="https://gateway.example.com/runtime/models/dep-2/v1",
        access_path="",
    )
    assert rehosted_browser == "https://gateway.example.com/foo/runtime/models/dep-2/v1", (
        f"prefix should be prepended for non-prefixed endpoint, got {rehosted_browser!r}"
    )

    sys.modules.pop("scaffolded_already_prefixed", None)


def _exercise_backend_chat_error_path(
    extension_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
) -> None:
    """Drive the chat endpoint into APIStatusError and assert sanitization.

    The starter must NEVER echo upstream model-service body content (which
    can contain hostnames, paths, or stack traces) back to the browser
    (ENG-3919). Verified by injecting a sensitive ``exc.body`` and asserting
    the response detail does not leak it.
    """
    import httpx
    from openai import APIStatusError

    module = _load_backend_module(extension_dir / "backend", module_name)

    async def fake_list_available_models(_request):
        return [
            SimpleNamespace(
                id="dep-1",
                name="Qwen smoke",
                repo_id=None,
                _extra={"endpoint": "https://kamiwaza.test/runtime/models/dep-1/v1"},
            )
        ]

    sensitive_body = {
        "detail": "Internal hostname db-internal.svc unreachable at /v1/chat/completions",
        "stack": "Traceback ... internal_module.run_inference",
    }
    fake_response = httpx.Response(
        503,
        request=httpx.Request("POST", "https://kamiwaza.test/runtime/models/dep-1/v1"),
    )

    class FakeCompletions:
        async def create(self, model, messages):
            raise APIStatusError(
                message="upstream failed", response=fake_response, body=sensitive_body,
            )

    class FakeChatClient:
        def __init__(self):
            self.chat = type("ChatNamespace", (), {"completions": FakeCompletions()})()

    async def fake_build_chat_client(_request, _endpoint):
        return FakeChatClient()

    monkeypatch.setattr(module, "list_available_models", fake_list_available_models)
    monkeypatch.setattr(module, "_build_chat_client", fake_build_chat_client)
    module.app.dependency_overrides[module.require_auth] = lambda: object()

    try:
        client = TestClient(module.app)
        response = client.post(
            "/api/chat",
            json={
                "model": "dep-1",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    finally:
        module.app.dependency_overrides.clear()
        sys.modules.pop(module_name, None)

    assert response.status_code == 503
    detail = response.json().get("detail", "")
    # Generic, status-code-based message — no upstream content leaked.
    assert "503" in detail
    assert "db-internal.svc" not in detail
    assert "Traceback" not in detail
    assert "/v1/chat/completions" not in detail
    assert "internal_module" not in detail


def _exercise_info_endpoint(
    extension_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
) -> None:
    """Hit the unauthenticated ``/api/info`` and assert it does not leak
    cluster-internal URLs (ENG-3920).
    """
    # AuthConfig.from_env() reads several KAMIWAZA_* env vars; provide values
    # that include a sentinel cluster-internal URL so the test fails loudly
    # if the field is ever re-introduced.
    sentinel = "http://api:7777/api"
    monkeypatch.setenv("KAMIWAZA_API_URL", sentinel)
    monkeypatch.setenv("KAMIWAZA_APP_NAME", "test-app")
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")

    module = _load_backend_module(extension_dir / "backend", module_name)
    try:
        client = TestClient(module.app)
        response = client.get("/api/info")
    finally:
        sys.modules.pop(module_name, None)

    assert response.status_code == 200
    body = response.json()
    assert "api_url" not in body
    assert sentinel not in response.text
    assert body["app_name"] == "test-app"
    assert body["use_auth"] is True


@pytest.mark.extension_regression
@pytest.mark.unit
def test_template_sanitizes_upstream_model_errors(tmp_path, monkeypatch):
    # Template path (used for new scaffolds going forward).
    extension_dir = _scaffold_app(tmp_path, monkeypatch, name="sanitize-template-app")
    _exercise_backend_chat_error_path(
        extension_dir, monkeypatch, module_name="sanitize_template_main",
    )


@pytest.mark.unit
def test_example_sanitizes_upstream_model_errors(tmp_path, monkeypatch):
    # Example path (the checked-in chatbot-app).
    extension_dir = _copy_example(tmp_path)
    _exercise_backend_chat_error_path(
        extension_dir, monkeypatch, module_name="sanitize_example_main",
    )


@pytest.mark.extension_regression
@pytest.mark.unit
def test_template_info_endpoint_does_not_leak_internal_api_url(tmp_path, monkeypatch):
    extension_dir = _scaffold_app(tmp_path, monkeypatch, name="info-template-app")
    _exercise_info_endpoint(
        extension_dir, monkeypatch, module_name="info_template_main",
    )


@pytest.mark.unit
def test_example_info_endpoint_does_not_leak_internal_api_url(tmp_path, monkeypatch):
    extension_dir = _copy_example(tmp_path)
    _exercise_info_endpoint(
        extension_dir, monkeypatch, module_name="info_example_main",
    )

@pytest.mark.unit
@pytest.mark.parametrize(
    "compose_path",
    [
        Path("kamiwaza_extensions/templates/app/docker-compose.yml"),
        Path("examples/chatbot-app/docker-compose.yml"),
    ],
)
def test_compose_does_not_pin_host_ports(compose_path):
    # ENG-3889 P2: bare container-port specs let Docker auto-assign the
    # host port so `docker compose up` does not collide with kind-cluster
    # ports on developer laptops running Kamiwaza locally.
    import yaml

    data = yaml.safe_load((REPO_ROOT / compose_path).read_text())
    services = data.get("services", {})
    assert services, f"{compose_path} has no services"

    for svc_name, svc_config in services.items():
        for port_spec in svc_config.get("ports", []):
            assert ":" not in str(port_spec), (
                f"{compose_path}::{svc_name} has a host:container port mapping "
                f"({port_spec!r}); use a bare container port to avoid host-port "
                f"collisions on developer laptops."
            )


@pytest.mark.unit
def test_anonymous_identity_byte_identical_between_require_auth_and_session(
    tmp_path, monkeypatch,
):
    """ENG-3889 P5: under USE_AUTH=false, ``require_auth()`` and ``/session``
    must return the same canonical anonymous Identity so the frontend sees
    a single shape (§4.8 P5).
    """
    import asyncio

    from kamiwaza_extensions_lib.auth import require_auth
    from kamiwaza_extensions_lib.identity import Identity, anonymous_identity

    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "false")

    # Drive require_auth() against a header-less request (USE_AUTH=false path).
    extension_dir = _scaffold_app(tmp_path, monkeypatch, name="anon-shape-app")
    module = _load_backend_module(extension_dir / "backend", "anon_shape_main")
    try:
        client = TestClient(module.app)
        response = client.get("/session")
        assert response.status_code == 200

        from fastapi import Request

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/health",
            "headers": [],
        }
        request = Request(scope)
        identity_from_require = asyncio.run(require_auth(request))
    finally:
        sys.modules.pop("anon_shape_main", None)

    canonical = anonymous_identity()
    # Public-fields subset surfaced by /session — must match the runtime lib's
    # canonical Anonymous identity exactly.
    public_fields = {
        "user_id", "email", "name", "roles", "workroom_id",
        "workroom_role", "is_authenticated",
    }
    canonical_public = canonical.model_dump(include=public_fields)
    session_public = {k: response.json().get(k) for k in public_fields}
    assert session_public == canonical_public

    # require_auth() returns the full Identity (a model_dump match is the
    # strongest equality we can assert without relying on Identity hashability)
    assert isinstance(identity_from_require, Identity)
    assert identity_from_require.model_dump() == canonical.model_dump()


@pytest.mark.unit
@pytest.mark.parametrize(
    "page_path",
    [
        Path("kamiwaza_extensions/templates/app/frontend/src/app/page.tsx"),
        Path("examples/chatbot-app/frontend/src/app/page.tsx"),
    ],
)
def test_home_short_circuits_authguard_for_anonymous_session(page_path):
    """ENG-3889 P6: the scaffold's Home component must skip AuthGuard when
    ``/session`` reports the canonical ``Anonymous`` identity, so first-load
    under USE_AUTH=false does not stick on 'Verifying session…'.
    """
    src = (REPO_ROOT / page_path).read_text()
    # Cheap but sufficient — the short-circuit relies on these two predicates
    # being present together. If either disappears the local-dev render path
    # silently regresses to the AuthGuard round-trip.
    assert "isAnonymousLocalDev" in src
    assert 'name === "Anonymous"' in src
    assert "isAuthenticated === false" in src


@pytest.mark.slow
@pytest.mark.parametrize(
    ("source_name", "factory"),
    [
        ("scaffolded", "_scaffold"),
        ("example", "_example"),
    ],
)
def test_app_starter_and_example_build_against_local_sdk_repo(
    tmp_path,
    monkeypatch,
    source_name,
    factory,
):
    if shutil.which("npm") is None:
        pytest.skip("npm is required for frontend starter smoke tests")

    if factory == "_scaffold":
        extension_dir = _scaffold_app(tmp_path, monkeypatch, name=f"{source_name}-chatbot-app")
    else:
        extension_dir = _copy_example(tmp_path)

    frontend_dir = extension_dir / "frontend"
    _ensure_local_ts_runtime_built()
    _point_frontend_to_local_runtime(frontend_dir)

    _run(["npm", "install"], frontend_dir)
    _run(["npm", "run", "build"], frontend_dir)
    _run([sys.executable, "-m", "py_compile", "backend/app/main.py"], extension_dir)
    _exercise_backend_chat(
        extension_dir,
        monkeypatch,
        f"smoke_backend_{source_name}",
    )
