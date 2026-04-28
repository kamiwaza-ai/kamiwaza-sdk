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
