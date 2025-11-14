from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Tuple

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

def _load_env_file(env_file: Path) -> None:
    if not env_file.exists():
        return
    for raw_line in env_file.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value

def _load_local_env() -> None:
    candidates: list[Path] = [PROJECT_ROOT / ".env.local"]
    root = os.environ.get("KAMIWAZA_ROOT")
    if root:
        candidates.append(Path(root).expanduser() / ".env.local")

    seen: set[Path] = set()
    for env_file in candidates:
        try:
            resolved = env_file.resolve()
        except FileNotFoundError:
            resolved = env_file
        if resolved in seen:
            continue
        seen.add(resolved)
        _load_env_file(env_file)

_load_local_env()

DEFAULT_BASE_URL = os.environ.get("KAMIWAZA_BASE_URL", "https://localhost/api").rstrip("/")


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("kamiwaza")
    group.addoption(
        "--live-base-url",
        action="store",
        default=DEFAULT_BASE_URL,
        help="Base URL used by live/e2e tests (defaults to env KAMIWAZA_BASE_URL or https://localhost/api).",
    )
    group.addoption(
        "--live-api-key",
        action="store",
        default=os.environ.get("KAMIWAZA_API_KEY", ""),
        help="API key used by live/e2e tests (defaults to env KAMIWAZA_API_KEY).",
    )
    group.addoption(
        "--live-username",
        action="store",
        default=os.environ.get("KAMIWAZA_USERNAME", "admin"),
        help="Username used for live/e2e password auth fallback (defaults to admin).",
    )
    group.addoption(
        "--live-password",
        action="store",
        default=os.environ.get("KAMIWAZA_PASSWORD", "kamiwaza"),
        help="Password used for live/e2e password auth fallback (defaults to kamiwaza).",
    )


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def qwen_model_id() -> str:
    """Canonical downloadable/deployable model ID for tests."""

    return "mlx-community/Qwen3-4B-4bit"


@pytest.fixture(scope="session")
def artifact_cache_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Workspace for test artifacts (model downloads, temp datasets, etc.)."""

    cache = tmp_path_factory.mktemp("kamiwaza-artifacts")
    return cache


@pytest.fixture(scope="session")
def hf_cache_dir() -> Path:
    """Shared cache for Hugging Face snapshots to avoid repeated downloads."""

    path = Path("build") / "hf-cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


class DummyAPIClient:
    """Minimal HTTP client stub that records calls and replays canned responses."""

    def __init__(self, responses: Dict[Tuple[str, str], Any]):
        self.responses = responses
        self.calls: list[tuple[str, str, dict]] = []

    def _dispatch(self, method: str, path: str, **kwargs) -> Any:
        self.calls.append((method, path, kwargs))
        key = (method, path)
        if key not in self.responses:
            available = ", ".join(f"{m} {p}" for m, p in self.responses)
            raise AssertionError(f"Unexpected request {method} {path}. Known: {available}")
        return self.responses[key]

    def get(self, path: str, **kwargs) -> Any:
        return self._dispatch("get", path, **kwargs)

    def post(self, path: str, **kwargs) -> Any:
        return self._dispatch("post", path, **kwargs)

    def patch(self, path: str, **kwargs) -> Any:
        return self._dispatch("patch", path, **kwargs)

    def delete(self, path: str, **kwargs) -> Any:
        return self._dispatch("delete", path, **kwargs)


@pytest.fixture
def dummy_client() -> Callable[[Dict[Tuple[str, str], Any]], DummyAPIClient]:
    """Factory fixture for unit tests needing a simple recorded-response client."""

    def _factory(responses: Dict[Tuple[str, str], Any]) -> DummyAPIClient:
        return DummyAPIClient(responses)

    return _factory


@pytest.fixture(scope="session")
def live_base_url(pytestconfig: pytest.Config) -> str:
    return str(pytestconfig.getoption("live_base_url")).rstrip("/")


@pytest.fixture(scope="session")
def live_api_key(pytestconfig: pytest.Config) -> str:
    return str(pytestconfig.getoption("live_api_key"))


@pytest.fixture(scope="session")
def live_username(pytestconfig: pytest.Config) -> str:
    return str(pytestconfig.getoption("live_username"))


@pytest.fixture(scope="session")
def live_password(pytestconfig: pytest.Config) -> str:
    return str(pytestconfig.getoption("live_password"))


@pytest.fixture
def client_factory():
    """Factory fixture for building configured Kamiwaza clients."""

    from kamiwaza_sdk import KamiwazaClient

    def _factory(
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        authenticator=None,
    ) -> KamiwazaClient:
        return KamiwazaClient(
            base_url or DEFAULT_BASE_URL,
            api_key=api_key,
            authenticator=authenticator,
        )

    return _factory
