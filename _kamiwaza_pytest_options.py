from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent
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
        if (
            value.startswith(("'", '"'))
            and value.endswith(("'", '"'))
            and len(value) >= 2
        ):
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

DEFAULT_BASE_URL = (
    os.environ.get("KAMIWAZA_BASE_URL")
    or os.environ.get("KAMIWAZA_BASE_URI")
    or "https://kamiwaza.test/api"
).rstrip("/")


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def add_live_options(parser: pytest.Parser) -> None:
    group = parser.getgroup("kamiwaza")
    group.addoption(
        "--live-base-url",
        action="store",
        default=DEFAULT_BASE_URL,
        help="Base URL used by live/e2e tests (defaults to env KAMIWAZA_BASE_URL or https://kamiwaza.test/api).",
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
        default=os.environ.get("KAMIWAZA_PASSWORD", ""),
        help=(
            "Password used for live/e2e password auth fallback "
            "(defaults to env KAMIWAZA_PASSWORD, else integration fixture resolves via kz-login)."
        ),
    )
    # ENG-9748 - build identity recorded in scenario-evidence.v2 run records.
    # ENG-10715 - version-first: the leading `; `-separated segment is the
    # release a consumer asks by, and the digest/environment follow as
    # annotations. KAMIWAZA_RELEASE supplies the release when this option
    # carries only the annotation.
    group.addoption(
        "--build",
        action="store",
        default=os.environ.get("KAMIWAZA_BUILD", ""),
        help=(
            "Build identity the e2e scenario run executed against, recorded in "
            "scenario-evidence.v2 artifacts (defaults to env KAMIWAZA_BUILD). "
            "Version-first: '1.3.0; <image digest>; <environment>', or "
            "'develop@<sha>; ...' for a dev build. The scenario harness refuses "
            "to run without one, or with one no version query could reach. Set "
            "KAMIWAZA_RELEASE=1.3.0 to have the release composed in front of a "
            "digest-only value."
        ),
    )
    # ENG-5784 - federation peer cluster for two-cluster live tests.
    group.addoption(
        "--live-peer-base-url",
        action="store",
        default=os.environ.get("KAMIWAZA_PEER_BASE_URL", ""),
        help=(
            "Base URL of the federation peer cluster for two-cluster live "
            "tests marked @pytest.mark.requires_two_clusters "
            "(defaults to env KAMIWAZA_PEER_BASE_URL). When empty, peer-required "
            "tests are auto-deselected."
        ),
    )
    group.addoption(
        "--live-peer-api-key",
        action="store",
        default=os.environ.get("KAMIWAZA_PEER_API_KEY", ""),
        help=(
            "API key for the federation peer cluster (defaults to env "
            "KAMIWAZA_PEER_API_KEY). Required when --live-peer-base-url is set."
        ),
    )
    group.addoption(
        "--skip-diffusion",
        action="store_true",
        default=_env_flag("KAMIWAZA_SKIP_DIFFUSION"),
        help=(
            "Explicitly skip live diffusion deployment and image-generation "
            "coverage (defaults to env KAMIWAZA_SKIP_DIFFUSION)."
        ),
    )
    group.addoption(
        "--require-federation-edge",
        action="store_true",
        default=os.environ.get("KAMIWAZA_REQUIRE_FEDERATION_EDGE", "").lower()
        in {"1", "true", "yes", "on"},
        help=(
            "Run the required shared-IDP two-cluster edge fail-closed: all six "
            "contract cases must collect and any skip is promoted to failure."
        ),
    )
    group.addoption(
        "--require-delegated-workload-edge",
        action="store_true",
        default=os.environ.get("KAMIWAZA_REQUIRE_DELEGATED_WORKLOAD_EDGE", "").lower()
        in {"1", "true", "yes", "on"},
        help=(
            "Run the delegated shared-IDP workload edge fail-closed: its live "
            "case must collect and any skip is promoted to failure."
        ),
    )


def mark_skipped_diffusion_items(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if not config.getoption("skip_diffusion"):
        return
    skip_marker = pytest.mark.skip(reason="disabled by explicit --skip-diffusion")
    for item in items:
        if "diffusion" in item.keywords:
            item.add_marker(skip_marker)
