"""DoctorChecker — environment diagnostics for kz-ext."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import List, Literal, Optional

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from kamiwaza_extensions import __version__
from kamiwaza_extensions.connections import ConnectionManager

# kubectl prints distinct stderr for "resource is genuinely absent" vs
# "I can't even reach the API server / I'm not authorized / wrong context".
# The former is a real platform-install problem; the latter is a config
# issue we shouldn't misdiagnose as a broken cluster (review re-review
# PR #84 H3). Match on the canonical bracketed `(NotFound)` token kubectl
# emits, plus a word-bounded ``not found`` fallback so the trailing
# clause in messages like
# ``Error from server (NotFound): customresourcedefinitions ... not found``
# still matches even if the bracket form is reformatted upstream. The
# word boundary prevents the fallback from matching unrelated stderr
# that happens to contain the substring (review re-re-re-review PR #84 M3).
_NOT_FOUND_FALLBACK_RE = re.compile(r"\bnot\s+found\b", re.IGNORECASE)


def _is_kubectl_not_found_error(stderr: str) -> bool:
    if not stderr:
        return False
    lowered = stderr.lower()
    if "(notfound)" in lowered:
        return True
    return bool(_NOT_FOUND_FALLBACK_RE.search(stderr))


def _registry_exit_code() -> int:
    from kamiwaza_extensions.exit_codes import ExitCode

    return int(ExitCode.REGISTRY_AUTH)


@lru_cache(maxsize=1)
def _uac_9d_hints() -> list[dict]:
    """Load UAC-9d class hints from the runtime lib's canonical JSON."""
    data = json.loads(
        resources.files("kamiwaza_extensions_lib")
        .joinpath("exception_names.json")
        .read_text(encoding="utf-8")
    )
    return list(data["classes"])


@lru_cache(maxsize=1)
def _compatibility_bundle() -> dict:
    """Load the per-CLI-version compatibility map (ENG-3897 / T2.18).

    The bundle ships with the package so it works in wheel, sdist, and
    editable installs alike — the file is a package-data resource, not a
    module import.
    """
    return json.loads(
        resources.files("kamiwaza_extensions")
        .joinpath("compatibility.json")
        .read_text(encoding="utf-8")
    )


# Match the leading version-like substring of an npm semver range.
# Handles caret/tilde/range/exact forms commonly seen in package.json:
#   ^0.3.0 → 0.3.0
#   ~0.3   → 0.3
#   0.1.5  → 0.1.5
#   >=0.2  → 0.2 (we read whichever lower bound appears first)
_NPM_VERSION_HEAD_RE = re.compile(r"(\d+(?:\.\d+){0,2})")


def _spec_bounds(spec_set: SpecifierSet) -> tuple[Optional[Version], Optional[Version]]:
    """Extract (lower, upper) Version bounds from a ``SpecifierSet``.

    Returns ``(None, None)`` for unbounded sides. ``>=X`` / ``>X`` / ``~=X``
    contribute to lower; ``<X`` / ``<=X`` contribute to upper. Multiple
    specs of the same direction collapse to the most-restrictive bound
    (max for lower, min for upper).

    Round-5 ultrareview H2: ``~=`` is a *compatible-release* operator and
    contributes BOTH a lower and an upper bound (PEP 440 §5.5):

      * ``~=X.Y.Z`` → ``>=X.Y.Z, <X.(Y+1)``
      * ``~=X.Y``   → ``>=X.Y,   <(X+1)``
      * ``~=X``     → invalid per PEP 440 (must have ≥ 2 release segments)

    The previous implementation treated ``~=`` as lower-only, so a tight
    pin like ``kamiwaza-extensions-lib~=0.3.0`` (which expands to
    ``>=0.3.0,<0.4.0`` and is fully inside ``>=0.2,<0.4``) was falsely
    warned as having no upper bound.
    """
    lower: Optional[Version] = None
    upper: Optional[Version] = None
    for spec in spec_set:
        op = spec.operator
        raw = spec.version
        try:
            ver = Version(raw)
        except InvalidVersion:
            continue
        if op in (">=", ">", "~="):
            if lower is None or ver > lower:
                lower = ver
        elif op in ("<", "<="):
            if upper is None or ver < upper:
                upper = ver
        if op == "~=":
            # Derive the implied upper from the *raw* string — packaging's
            # Version normalises ``"0.3"`` and ``"0.3.0"`` to equal values,
            # so we can't recover the segment count from ``ver``.
            implied_upper = _tilde_eq_upper(raw)
            if implied_upper is not None and (upper is None or implied_upper < upper):
                upper = implied_upper
    return lower, upper


def _tilde_eq_upper(raw: str) -> Optional[Version]:
    """Compute the implied upper bound for a PEP 440 ``~=`` operand.

    Returns ``None`` for malformed input (lets the caller fall through to
    the original lower-only behavior rather than crash).
    """
    # Strip a trailing dev/pre/post release tag — ``~=1.2.dev0`` still
    # caps at ``2.0`` for X.Y form. We only need the leading release
    # segments to count them.
    head = re.match(r"^\s*(\d+(?:\.\d+)*)", raw)
    if head is None:
        return None
    parts = [int(p) for p in head.group(1).split(".")]
    if len(parts) < 2:
        return None
    if len(parts) >= 3:
        # ~=X.Y.Z[.…] → upper at X.(Y+1)
        return Version(f"{parts[0]}.{parts[1] + 1}")
    # ~=X.Y → upper at (X+1)
    return Version(f"{parts[0] + 1}")


def _bounds_outside_supported(
    declared_lower: Optional[Version],
    declared_upper: Optional[Version],
    supported_lower: Optional[Version],
    supported_upper: Optional[Version],
) -> Optional[str]:
    """Shared bounds-based containment check for both Python and TS specs.

    Round-4 ultrareview H2 + H3 — declared must be fully contained in
    supported (every admitted version of declared must also be admitted
    by supported). Sufficient by checking that declared's bounds don't
    extend beyond supported's bounds.
    """
    if supported_lower is not None:
        if declared_lower is None:
            return (
                f"declared has no lower bound but supported requires "
                f">={supported_lower}"
            )
        if declared_lower < supported_lower:
            return (
                f"declared lower bound {declared_lower} is below supported "
                f"floor {supported_lower}"
            )
    if supported_upper is not None:
        if declared_upper is None:
            return (
                f"declared has no upper bound but supported requires <{supported_upper}"
            )
        if declared_upper > supported_upper:
            return (
                f"declared upper bound {declared_upper} is above supported "
                f"ceiling {supported_upper}"
            )
    return None


def _python_spec_outside_supported(
    declared: SpecifierSet, supported: SpecifierSet
) -> Optional[str]:
    """Return a human reason if ``declared`` admits versions outside ``supported``.

    Round-4 ultrareview H2 — the prior implementation only checked
    *overlap* (does declared admit any supported version?). That misses
    open-ended specs like ``>=0.2`` or ``~=0.2`` (= ``>=0.2,<1.0``)
    against ``>=0.2,<0.4``: there IS overlap (0.2 satisfies both), but
    pip can legally resolve ``0.4+`` which is outside supported.

    The correct check is *full containment*: every version that
    ``declared`` admits must also be admitted by ``supported``.
    Sufficient (and conservative) by checking endpoints:
      * Each ``==`` pin must be in ``supported``.
      * If ``supported`` has a lower bound, ``declared`` must too AND
        declared's lower must be ≥ supported's lower. (Otherwise declared
        admits versions below the floor.)
      * If ``supported`` has an upper bound, ``declared`` must too AND
        declared's upper must be ≤ supported's upper. (Otherwise declared
        admits versions above the ceiling — the round-4 case.)

    Returns ``None`` when ``declared`` is fully contained in ``supported``.
    """
    pinned: list[Version] = []
    for spec in declared:
        if spec.operator == "==":
            try:
                pinned.append(Version(spec.version))
            except InvalidVersion:
                pass
    for ver in pinned:
        if ver not in supported:
            return f"pinned {ver} not in {supported}"
    # If declared is purely a set of == pins, the pinned check above is
    # sufficient (supported admits each one).
    has_range = any(spec.operator != "==" for spec in declared)
    if not has_range and pinned:
        return None

    declared_lower, declared_upper = _spec_bounds(declared)
    supported_lower, supported_upper = _spec_bounds(supported)
    return _bounds_outside_supported(
        declared_lower, declared_upper, supported_lower, supported_upper
    )


def _npm_bounds(spec: str) -> tuple[Optional[Version], Optional[Version]]:
    """Parse an npm semver spec into ``(lower, upper)`` PEP 440 ``Version``
    bounds. Round-4 ultrareview H3 — the prior implementation only
    looked at the leading version token (lower bound), so an
    open-ended spec like ``">=0.2"`` or ``"0.2"`` (bare/exact) had no
    detected upper bound. The doctor then passed ``>=0.2`` against a
    supported ``>=0.2,<0.4`` range even though npm could resolve a
    future ``0.4+`` release.

    This handles the common npm shapes:
      * ``^X.Y.Z``    → [X.Y.Z, (X+1).0.0) or, for X=0, [0.Y.Z, 0.(Y+1).0)
      * ``~X.Y.Z``    → [X.Y.Z, X.(Y+1).0)
      * ``X.Y.Z``     → exact (lower == upper)
      * ``>=X,<Y`` (or whitespace-separated) → explicit bounds
      * ``>=X``, ``<X``, ``>X``, ``<=X`` → single-direction
      * ``*`` / ``latest`` / unparseable → ``(None, None)`` (fail open)
    """
    spec = spec.strip()
    if not spec or spec in ("*", "latest", "x", "X"):
        return (None, None)
    if spec.startswith("^"):
        v = _npm_lower_bound(spec[1:])
        if v is None:
            return (None, None)
        if v.major > 0:
            upper = Version(f"{v.major + 1}.0.0")
        elif v.minor > 0:
            upper = Version(f"0.{v.minor + 1}.0")
        else:
            upper = Version(f"0.0.{v.micro + 1}")
        return (v, upper)
    if spec.startswith("~"):
        v = _npm_lower_bound(spec[1:])
        if v is None:
            return (None, None)
        upper = Version(f"{v.major}.{v.minor + 1}.0")
        return (v, upper)
    # Range or exact: split on comma or whitespace.
    lower: Optional[Version] = None
    upper: Optional[Version] = None
    for part in re.split(r"[,\s]+", spec):
        part = part.strip()
        if not part:
            continue
        if part.startswith(">="):
            v = _npm_lower_bound(part[2:])
            if v is not None:
                lower = v if lower is None or v > lower else lower
        elif part.startswith(">"):
            v = _npm_lower_bound(part[1:])
            if v is not None:
                lower = v if lower is None or v > lower else lower
        elif part.startswith("<="):
            v = _npm_lower_bound(part[2:])
            if v is not None:
                upper = v if upper is None or v < upper else upper
        elif part.startswith("<"):
            v = _npm_lower_bound(part[1:])
            if v is not None:
                upper = v if upper is None or v < upper else upper
        elif part.startswith("="):
            v = _npm_lower_bound(part[1:])
            if v is not None:
                lower = v
                upper = v
        else:
            # Bare X.Y.Z → exact pin.
            v = _npm_lower_bound(part)
            if v is not None:
                lower = v
                upper = v
    return (lower, upper)


def _npm_lower_bound(spec: str) -> Optional[Version]:
    """Best-effort: parse the first version-like substring from an npm range.

    Conservative — returns None on shapes we can't parse (workspace:, git+,
    URL deps). The CompatibilityBundle check warns rather than errors, so
    a None result simply means "skip this check, don't crash doctor."
    """
    match = _NPM_VERSION_HEAD_RE.search(spec)
    if not match:
        return None
    raw = match.group(1)
    # Pad to a 3-component version so packaging.Version is happy with "0.3".
    parts = raw.split(".")
    while len(parts) < 3:
        parts.append("0")
    try:
        return Version(".".join(parts))
    except InvalidVersion:
        return None


@dataclass
class CheckResult:
    name: str
    status: Literal["pass", "fail", "warn"]
    message: str
    fix: Optional[str] = None
    exit_code: Optional[int] = None


class DoctorChecker:
    """Runs diagnostic checks on the development environment."""

    def __init__(self, config_dir: Optional[Path] = None) -> None:
        self._conn_mgr = ConnectionManager(config_dir=config_dir)

    def run_all(self) -> List[CheckResult]:
        results: List[CheckResult] = []

        # System checks (always run)
        results.append(self._check_python_version())
        results.append(self._check_docker_installed())
        results.append(self._check_compose())
        results.append(self._check_docker_running())

        # Connection checks (if configured)
        results.append(self._check_connection())

        # Registry checks (if configured)
        results.extend(self._check_registry_readiness())

        # Cluster extension readiness — operator image / CRD / Deployment
        results.append(self.cluster_extension_readiness())

        # Extension checks (if in extension directory)
        ext_results = self._check_extension_context()
        results.extend(ext_results)

        # SDK override checks (if configured)
        sdk_results = self._check_sdk_override()
        results.extend(sdk_results)

        # UAC-9d reference hints (always emitted)
        results.extend(self._uac_9d_reference_hints())

        return results

    # ------------------------------------------------------------------
    # UAC-9d reference hints
    # ------------------------------------------------------------------

    def _uac_9d_reference_hints(self) -> List[CheckResult]:
        """Reference entries for each UAC-9d runtime-lib exception class.

        These are always-pass informational entries; they surface the
        canonical doctor_hint so extension authors seeing an exception
        in their logs can find the matching diagnosis + fix in
        ``kz-ext doctor`` output (§4.2.8 DoctorUACFailureHints).
        """
        return [
            CheckResult(
                name=f"Failure class: {entry['name']}",
                status="pass",
                message=entry["doctor_hint"],
                fix=entry["doctor_hint"],
            )
            for entry in _uac_9d_hints()
        ]

    # ------------------------------------------------------------------
    # System checks
    # ------------------------------------------------------------------

    def _check_python_version(self) -> CheckResult:
        v = sys.version_info
        version_str = f"{v.major}.{v.minor}.{v.micro}"
        if v >= (3, 10):
            return CheckResult("Python version", "pass", f"{version_str}")
        return CheckResult(
            "Python version",
            "fail",
            f"{version_str} (requires >= 3.10)",
            fix="Install Python 3.10 or newer",
        )

    def _check_docker_installed(self) -> CheckResult:
        try:
            result = subprocess.run(
                ["docker", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                version = result.stdout.strip().split(",")[0]
                return CheckResult("Docker installed", "pass", version)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return CheckResult(
            "Docker installed",
            "fail",
            "Not found",
            fix="Install Docker Desktop: https://docs.docker.com/get-docker/",
        )

    def _check_compose(self) -> CheckResult:
        # Try v2
        try:
            result = subprocess.run(
                ["docker", "compose", "version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return CheckResult("Docker Compose", "pass", result.stdout.strip())
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        # Try v1
        try:
            result = subprocess.run(
                ["docker-compose", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return CheckResult("Docker Compose", "pass", result.stdout.strip())
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return CheckResult(
            "Docker Compose",
            "fail",
            "Not found",
            fix="Install Docker Desktop (includes Compose v2) or pip install docker-compose",
        )

    def _check_docker_running(self) -> CheckResult:
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return CheckResult("Docker running", "pass", "Docker daemon is running")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return CheckResult(
            "Docker running",
            "fail",
            "Docker daemon not responding",
            fix="Start Docker Desktop or run 'sudo systemctl start docker'",
        )

    # ------------------------------------------------------------------
    # Connection checks
    # ------------------------------------------------------------------

    def _check_connection(self) -> CheckResult:
        conn = self._conn_mgr.get_active_connection()
        if conn is None:
            return CheckResult(
                "Kamiwaza connection",
                "warn",
                "No connection configured",
                fix="Run 'kz-ext login <url>' to connect",
            )
        token = self._conn_mgr.get_token()
        if token is None:
            return CheckResult(
                "Kamiwaza connection",
                "warn",
                f"Connection '{conn.name}' has no token",
                fix=f"Run 'kz-ext login {conn.url}' to re-authenticate",
            )
        # Try health endpoints — platform may expose different ones
        import requests

        for path in ("/auth/ping", "/auth/health", "/health"):
            try:
                resp = requests.get(
                    f"{conn.url}{path}",
                    headers={"Authorization": f"Bearer {token.access_token}"},
                    timeout=5,
                    verify=conn.verify_ssl,
                )
                if resp.ok:
                    return CheckResult(
                        "Kamiwaza connection", "pass", f"{conn.name} ({conn.url})"
                    )
            except requests.ConnectionError:
                # Server unreachable — no point trying more paths on same host
                return CheckResult(
                    "Kamiwaza connection",
                    "warn",
                    f"{conn.name}: unreachable ({conn.url})",
                    fix="Check network connectivity or VPN",
                )
            except requests.RequestException:
                continue

        # All endpoints responded but none returned 200
        return CheckResult(
            "Kamiwaza connection",
            "warn",
            f"{conn.name}: no health endpoint found",
            fix="Server is reachable but health check failed",
        )

    # ------------------------------------------------------------------
    # Registry checks
    # ------------------------------------------------------------------

    def _check_registry_readiness(self) -> List[CheckResult]:
        connection = self._conn_mgr.get_active_connection()
        if connection is None:
            return []

        # Mirror what ``kz-ext dev`` actually does: derive registry resolution
        # and the insecure-registry gate from the *effective* verify-SSL
        # (env override / dev-hostname auto-disable /
        # persisted flag), not the persisted ``verify_ssl`` alone. Otherwise
        # doctor greenlights a config that ``dev`` then fails on -- e.g. a
        # ``kamiwaza.test`` connection with ``verify_ssl=True`` whose TLS is
        # auto-disabled (ENG-5719 follow-up). ``getattr`` fallback keeps
        # doctor robust against connection objects without the method.
        effective_verify = (
            connection.effective_verify_ssl()
            if hasattr(connection, "effective_verify_ssl")
            else getattr(connection, "verify_ssl", True)
        )

        try:
            from kamiwaza_extensions.registry_resolution import (
                BUILD_VM_LOOPBACK_ALIAS_SOURCE,
                docker_accepts_insecure_push_to,
                insecure_registry_daemon_json_fix,
                resolve_dev_registries,
            )

            # ``kz-ext dev`` builds with Docker on the default path today, so
            # doctor validates the same Docker push topology. Explicit
            # ``--no-build`` runs may push with Podman, but doctor has no flag
            # context for that specialized path.
            push_engine = "docker"
            resolution = resolve_dev_registries(
                connection,
                push_engine=push_engine,
            )
        except ValueError as exc:
            return [
                CheckResult(
                    "Registry resolution",
                    "fail",
                    str(exc),
                    fix="Set KAMIWAZA_REGISTRY explicitly or configure the platform registry",
                )
            ]

        results = [
            CheckResult(
                "Registry resolution",
                "pass",
                (
                    f"image={resolution.image_registry} "
                    f"({resolution.image_registry_source}), "
                    f"push={resolution.push_registry} "
                    f"({resolution.push_registry_source})"
                ),
            )
        ]
        verify_ssl = effective_verify
        results.append(
            self._check_registry_http_endpoint(
                "Registry image endpoint",
                resolution.image_registry,
                connection_verify_ssl=verify_ssl,
            )
        )
        if resolution.push_registry != resolution.image_registry:
            results.append(
                self._check_push_registry_endpoint(
                    resolution.push_registry,
                    connection_verify_ssl=verify_ssl,
                )
            )
            # The push registry differs from the image registry, which means
            # we'll retag and push to an alias. Docker won't push to that
            # alias over HTTP unless it's in ``insecure-registries``. Catch
            # this in doctor too (jxstanford iter-4 Critical #1) so users
            # see the fix before they hit it at ``kz-ext dev`` time.
            #
            # Gate on the auto loopback-alias rewrite so a legitimate
            # user-supplied secure-HTTPS push override isn't refused just
            # because the active Kamiwaza connection itself is insecure.
            insecure = not verify_ssl
            if (
                insecure
                and push_engine == "docker"
                and resolution.push_registry_source == BUILD_VM_LOOPBACK_ALIAS_SOURCE
                and not docker_accepts_insecure_push_to(resolution.push_registry)
            ):
                results.append(
                    CheckResult(
                        "Docker insecure-registries",
                        "fail",
                        f"Docker won't push insecurely to {resolution.push_registry}",
                        fix=insecure_registry_daemon_json_fix(resolution.push_registry),
                        exit_code=_registry_exit_code(),
                    )
                )
        return results

    def _check_push_registry_endpoint(
        self,
        registry: str,
        *,
        connection_verify_ssl: Optional[bool] = None,
    ) -> CheckResult:
        from kamiwaza_extensions.registry_resolution import (
            DOCKER_VM_HOST_ALIAS,
            PODMAN_VM_HOST_ALIAS,
            running_podman_machine_name,
        )

        # Podman machine: probe from inside the VM via SSH.
        if registry.startswith(f"{PODMAN_VM_HOST_ALIAS}:"):
            machine = running_podman_machine_name()
            if machine is None:
                return CheckResult(
                    "Registry push endpoint",
                    "warn",
                    f"{registry} is a Podman VM alias but no Podman machine is running",
                    fix="Start the Podman machine or set KAMIWAZA_PUSH_REGISTRY",
                )
            try:
                result = subprocess.run(
                    [
                        "podman",
                        "machine",
                        "ssh",
                        machine,
                        "curl",
                        "-fsS",
                        f"http://{registry}/v2/",
                        "-o",
                        "/dev/null",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                return CheckResult(
                    "Registry push endpoint",
                    "warn",
                    f"Could not probe from Podman VM: {exc}",
                )
            if result.returncode == 0:
                return CheckResult(
                    "Registry push endpoint",
                    "pass",
                    f"{registry} reachable from Podman machine '{machine}'",
                )
            return CheckResult(
                "Registry push endpoint",
                "fail",
                f"{registry} not reachable from Podman machine '{machine}'",
                fix=result.stderr.strip() or "Check KAMIWAZA_PUSH_REGISTRY",
                exit_code=_registry_exit_code(),
            )

        # Docker Desktop VM: alias only resolves inside the Docker VM, not
        # from the macOS/Windows host. We don't run a one-shot ``docker run``
        # probe here because it would pull an image just to confirm DNS;
        # the push itself will surface a clearer error if the alias is wrong.
        if registry.startswith(f"{DOCKER_VM_HOST_ALIAS}:"):
            return CheckResult(
                "Registry push endpoint",
                "warn",
                f"{registry} is a Docker Desktop VM alias; "
                "skipping host-side probe (verified at push time)",
            )

        return self._check_registry_http_endpoint(
            "Registry push endpoint",
            registry,
            connection_verify_ssl=connection_verify_ssl,
        )

    def _check_registry_http_endpoint(
        self,
        name: str,
        registry: str,
        *,
        connection_verify_ssl: Optional[bool] = None,
    ) -> CheckResult:
        import warnings

        import requests

        # Use the urllib3 vendored under ``requests`` rather than the top-
        # level package so we ride whatever version ``requests`` pins —
        # jxstanford Medium #6: ``urllib3`` isn't a declared dependency.
        from requests.packages import urllib3  # type: ignore[import-untyped]
        from kamiwaza_extensions.registry_resolution import is_loopback_registry

        loopback = is_loopback_registry(registry)
        # Probe HTTPS first for non-loopback registries: real registries
        # almost always front /v2/ with TLS, and HTTPS-only registries
        # whose port-80 returns an HTML landing page would otherwise be
        # mis-reported as a hard failure on the first probe. For loopback
        # registries (k0s/kind local dev), plain HTTP is the convention.
        if loopback:
            urls = (f"http://{registry}/v2/", f"https://{registry}/v2/")
        else:
            urls = (f"https://{registry}/v2/", f"http://{registry}/v2/")

        failures: list[str] = []
        for url in urls:
            # TLS verification policy:
            #   - Loopback HTTPS: always skip verify (dev self-signed).
            #   - Non-loopback HTTPS with connection.verify_ssl=False:
            #     mirror the connection's choice so doctor doesn't
            #     contradict every other request the SDK makes for the
            #     same cluster (jxstanford Medium #1).
            #   - Otherwise: verify against the system CA so real cert
            #     issues surface in doctor output.
            if url.startswith("https://"):
                if loopback:
                    verify = False
                elif connection_verify_ssl is False:
                    verify = False
                else:
                    verify = True
            else:
                verify = True
            try:
                if verify:
                    response = requests.get(url, timeout=5)
                else:
                    # Suppress the ``InsecureRequestWarning`` that ``verify=False``
                    # would otherwise emit to stderr; structured CheckResult is
                    # the user-facing surface here.
                    with warnings.catch_warnings():
                        warnings.simplefilter(
                            "ignore", urllib3.exceptions.InsecureRequestWarning
                        )
                        response = requests.get(url, timeout=5, verify=False)
            except requests.RequestException as exc:
                failures.append(f"{url}: {exc}")
                continue
            content_type = response.headers.get("content-type", "")
            body_prefix = getattr(response, "content", b"")
            if isinstance(body_prefix, bytes):
                body_start = body_prefix[:512].lstrip().lower()
                starts_html = body_start.startswith((b"<!doctype html", b"<html"))
            elif isinstance(body_prefix, str):
                body_start = body_prefix[:512].lstrip().lower()
                starts_html = body_start.startswith(("<!doctype html", "<html"))
            else:
                starts_html = False
            is_html = "text/html" in content_type.lower() or starts_html
            if is_html:
                # HTML on this URL is not a real /v2/ response — record it
                # and continue so the alternate scheme still gets a chance.
                failures.append(f"{url} returned HTML, not a registry /v2/ response")
                continue
            # Treat 200/401 as definitive registry-valid. Also accept 4xx
            # whose ``WWW-Authenticate: Bearer ...`` proves a registry is
            # behind a token authorizer (e.g., GHCR catalog disabled,
            # jxstanford Medium #3).
            if response.status_code in (200, 401) or (
                400 <= response.status_code < 500
                and "bearer"
                in (response.headers.get("WWW-Authenticate", "") or "").lower()
            ):
                return CheckResult(
                    name,
                    "pass",
                    f"{url} returned {response.status_code}",
                )
            failures.append(f"{url}: HTTP {response.status_code}")

        # If every URL we tried returned HTML, the host is not serving a
        # registry endpoint at all — that's a hard fail, not a warn.
        if failures and all("returned HTML" in f for f in failures):
            return CheckResult(
                name,
                "fail",
                f"{registry} did not serve a registry /v2/ endpoint on HTTP or HTTPS",
                fix=(
                    "Use the platform-advertised registry host or set "
                    "KAMIWAZA_REGISTRY explicitly"
                ),
                exit_code=_registry_exit_code(),
            )
        return CheckResult(
            name,
            "warn",
            f"Could not confirm registry /v2/ endpoint for {registry}",
            fix="; ".join(failures[-2:]) if failures else None,
        )

    # ------------------------------------------------------------------
    # Cluster extension readiness (B1a / §4.2.8)
    # ------------------------------------------------------------------

    def cluster_extension_readiness(self) -> CheckResult:
        """Check that the cluster can actually run extensions.

        Probes (in order):
          1. ``kamiwazaextensions.extensions.kamiwaza.io`` CRD installed.
          2. ``extension-operator`` Deployment in ``kamiwaza-system`` is
             ``Available`` and observed-generation matches.
          3. Operator image tag is in ``OPERATOR_COMPATIBLE_TAGS`` (warn on
             mismatch when otherwise Available; fail on ``ImagePullBackOff``
             surfacing the kubelet error).

        On failure, ``exit_code`` is set to ``ExitCode.CLUSTER_NOT_READY``
        (23) so the ``doctor`` command can surface it distinctly from the
        generic CLI failure path.
        """
        from kamiwaza_extensions.exit_codes import ExitCode
        from kamiwaza_extensions.platform_compat import (
            EXTENSION_CRD,
            OPERATOR_COMPATIBLE_TAGS,
            OPERATOR_DEPLOYMENT,
            OPERATOR_NAMESPACE,
            is_compatible_tag,
            is_digest_ref,
            parse_image_ref,
        )

        name = "Cluster extension readiness"
        not_ready = int(ExitCode.CLUSTER_NOT_READY)

        # No Kamiwaza connection configured — skip rather than failing on
        # an unrelated kube-context. Without a connection there's no
        # cluster the user is *trying* to reach, so a hard fail like
        # "CRD not installed" would exit doctor non-zero on every
        # workstation that has kubectl pointed at something else
        # (review High #1 on PR #84).
        connection = self._conn_mgr.get_active_connection()
        if connection is None:
            return CheckResult(
                name,
                "warn",
                "No Kamiwaza connection configured; cluster readiness probe skipped",
                fix="Run 'kz-ext login <url>' to connect, then re-run doctor",
            )

        # Remote SaaS / non-local connection — the local kube-context has
        # no verified relationship to the Kamiwaza cluster (the connection
        # is HTTP-only; ConnectionInfo carries no kube-context binding).
        # Probing the local context could inspect an unrelated cluster
        # and emit confidently-wrong guidance like "CRD not installed,
        # reinstall the platform" (review re-review PR #84 H1). Skip with
        # a transparent warn instead.
        from kamiwaza_extensions.platform_compat import is_local_connection

        if not is_local_connection(connection.url):
            return CheckResult(
                name,
                "warn",
                f"Connection '{connection.name}' is remote ({connection.url}); "
                "local kubectl context cannot be verified to target the same cluster — "
                "probe skipped",
                fix=(
                    "Inspect the cluster directly via the platform UI, or run "
                    "doctor on a host whose kubectl context is bound to the "
                    "Kamiwaza cluster"
                ),
            )

        # kubectl availability — soft skip
        kubectl_check = self._kubectl_available()
        if not kubectl_check:
            return CheckResult(
                name,
                "warn",
                "kubectl not available; cluster readiness probe skipped",
                fix="Install kubectl and configure access to the target cluster",
            )

        # 1. CRD presence
        crd_status, crd_err = self._kubectl_get(["crd", EXTENSION_CRD])
        if crd_status != 0:
            # Distinguish "CRD genuinely absent" from "kubectl can't reach
            # the cluster / auth expired / wrong context" (review re-review
            # PR #84 H3). Only the former is a CLUSTER_NOT_READY=23 fail
            # ("reinstall the platform"); the latter is a transient/config
            # issue we should warn about rather than misdiagnose as a
            # broken cluster.
            if _is_kubectl_not_found_error(crd_err):
                return CheckResult(
                    name,
                    "fail",
                    f"Extension CRD '{EXTENSION_CRD}' not installed on cluster",
                    fix=f"Install or upgrade the kamiwaza platform on this cluster: {crd_err}",
                    exit_code=not_ready,
                )
            return CheckResult(
                name,
                "warn",
                f"Could not query cluster for CRD: {crd_err}",
                fix=(
                    "Verify your kubeconfig is valid and points at the "
                    "Kamiwaza cluster (`kubectl get nodes` to test). "
                    "kubectl reachability errors are not the same as a "
                    "missing platform install."
                ),
            )

        # 2. Operator Deployment + image
        deploy_status, deploy_payload = self._kubectl_get_json(
            ["deploy", OPERATOR_DEPLOYMENT, "-n", OPERATOR_NAMESPACE]
        )
        if deploy_status != 0 or not isinstance(deploy_payload, dict):
            # Same distinction here: "Deployment not found" is a real
            # platform-install issue; transient kubectl failures are not.
            err_msg = deploy_payload if isinstance(deploy_payload, str) else ""
            if _is_kubectl_not_found_error(err_msg):
                return CheckResult(
                    name,
                    "fail",
                    f"extension-operator Deployment not found in '{OPERATOR_NAMESPACE}'",
                    fix="Re-run the platform installer on this cluster",
                    exit_code=not_ready,
                )
            return CheckResult(
                name,
                "warn",
                f"Could not query cluster for extension-operator Deployment: {err_msg}",
                fix=(
                    "Verify your kubeconfig is valid and points at the "
                    "Kamiwaza cluster (`kubectl get nodes` to test)."
                ),
            )

        # Image tag
        # Match by container name (review re-review PR #84 M3) so a future
        # sidecar — init container, metrics exporter — doesn't shadow the
        # operator container at index 0.
        image_ref = ""
        try:
            containers = (
                deploy_payload.get("spec", {})
                .get("template", {})
                .get("spec", {})
                .get("containers", [])
            )
            for container in containers:
                if container.get("name") == OPERATOR_DEPLOYMENT:
                    image_ref = container.get("image", "")
                    break
            else:
                if containers:
                    image_ref = containers[0].get("image", "")
        except (AttributeError, TypeError):
            image_ref = ""
        _, image_tag = parse_image_ref(image_ref) if image_ref else ("", None)

        # ImagePullBackOff detection — most actionable signal
        backoff_msg = self._operator_pod_backoff_message()
        if backoff_msg:
            expected = ", ".join(OPERATOR_COMPATIBLE_TAGS)
            return CheckResult(
                name,
                "fail",
                f"extension-operator pod is in ImagePullBackOff: {backoff_msg}",
                fix=(
                    f"Operator image '{image_ref}' cannot be pulled. "
                    f"Expected one of: {expected}. "
                    f"Re-run the platform installer with a published tag."
                ),
                exit_code=not_ready,
            )

        # Available condition
        status_obj = deploy_payload.get("status", {}) or {}
        observed_gen = status_obj.get("observedGeneration")
        spec_gen = deploy_payload.get("metadata", {}).get("generation")
        conditions = status_obj.get("conditions", []) or []
        available = any(
            c.get("type") == "Available" and c.get("status") == "True"
            for c in conditions
        )
        if not available or (observed_gen is not None and observed_gen != spec_gen):
            return CheckResult(
                name,
                "fail",
                "extension-operator Deployment is not Available",
                fix=(
                    "Operator is not ready to reconcile extensions. "
                    "Inspect: kubectl describe deploy/extension-operator "
                    f"-n {OPERATOR_NAMESPACE}"
                ),
                exit_code=not_ready,
            )

        # 3. Tag compatibility — warn (not fail) when Available but mismatched.
        # Digest-pinned refs (`@sha256:...`) are opaque from the tag-name
        # perspective so we can't determine compat without a registry
        # round-trip. Treat them as opaque-but-trusted: pass with a note
        # that compat couldn't be verified, but don't emit a misleading
        # warning (review re-review PR #84 H1).
        if image_tag and is_digest_ref(image_tag):
            return CheckResult(
                name,
                "pass",
                f"CRD installed, extension-operator Available "
                f"(digest-pinned: {image_tag[:19]}…)",
            )
        if not is_compatible_tag(image_tag):
            expected = ", ".join(OPERATOR_COMPATIBLE_TAGS)
            return CheckResult(
                name,
                "warn",
                f"extension-operator running '{image_tag}' (Available)",
                fix=(
                    f"Tag '{image_tag}' is not in this CLI's compatible set "
                    f"({expected}). Behavior may diverge from this CLI version."
                ),
            )

        return CheckResult(
            name,
            "pass",
            f"CRD installed, extension-operator Available ({image_tag})",
        )

    @staticmethod
    def _kubectl_available() -> bool:
        try:
            result = subprocess.run(
                ["kubectl", "version", "--client=true", "-o", "json"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    @staticmethod
    def _kubectl_get(args: list[str]) -> tuple[int, str]:
        try:
            result = subprocess.run(
                ["kubectl", "get", *args],
                capture_output=True,
                text=True,
                timeout=15,
            )
            return result.returncode, (result.stderr.strip() or result.stdout.strip())
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return 1, str(exc)

    @staticmethod
    def _kubectl_get_json(args: list[str]) -> tuple[int, object]:
        try:
            result = subprocess.run(
                ["kubectl", "get", *args, "-o", "json"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                return result.returncode, result.stderr.strip()
            return 0, json.loads(result.stdout)
        except (
            FileNotFoundError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
        ) as exc:
            return 1, str(exc)

    @staticmethod
    def _operator_pod_backoff_message() -> Optional[str]:
        """Return the kubelet ImagePullBackOff message for the operator pod, if any."""
        from kamiwaza_extensions.platform_compat import (
            OPERATOR_DEPLOYMENT,
            OPERATOR_NAMESPACE,
        )

        try:
            result = subprocess.run(
                [
                    "kubectl",
                    "get",
                    "pods",
                    "-n",
                    OPERATOR_NAMESPACE,
                    "-l",
                    f"app.kubernetes.io/name={OPERATOR_DEPLOYMENT}",
                    "-o",
                    "json",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                return None
            payload = json.loads(result.stdout)
        except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return None

        for pod in payload.get("items", []):
            statuses = (pod.get("status", {}) or {}).get("containerStatuses", []) or []
            for cs in statuses:
                waiting = (cs.get("state", {}) or {}).get("waiting", {}) or {}
                reason = waiting.get("reason", "")
                if reason in ("ImagePullBackOff", "ErrImagePull"):
                    return waiting.get("message") or reason
        return None

    # ------------------------------------------------------------------
    # Extension context checks
    # ------------------------------------------------------------------

    def _check_extension_context(self) -> List[CheckResult]:
        results: List[CheckResult] = []
        cwd = Path.cwd()

        metadata_path = cwd / "kamiwaza.json"
        if not metadata_path.exists():
            # Check one level deep
            found = list(cwd.glob("*/kamiwaza.json"))
            if found:
                metadata_path = found[0]
            else:
                return results  # Not in an extension dir — skip extension checks

        # CLI version compatibility
        try:
            with metadata_path.open() as f:
                data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return results

        kz_ext_version = data.get("kz_ext_version")
        if kz_ext_version:
            results.append(self._check_cli_version(kz_ext_version))

        ext_dir = metadata_path.parent

        # Python runtime lib
        req_file = ext_dir / "requirements.txt"
        if not req_file.exists():
            req_file = ext_dir / "backend" / "requirements.txt"
        if req_file.exists():
            results.append(self._check_python_runtime_lib(req_file))

        # TypeScript runtime lib
        pkg_file = ext_dir / "package.json"
        if not pkg_file.exists():
            pkg_file = ext_dir / "frontend" / "package.json"
        if pkg_file.exists():
            results.append(self._check_ts_runtime_lib(pkg_file))

        return results

    def _check_cli_version(self, specifier_str: str) -> CheckResult:
        try:
            spec = SpecifierSet(specifier_str)
            ver = Version(__version__)
            if ver in spec:
                return CheckResult(
                    "CLI version compatibility",
                    "pass",
                    f"{__version__} matches {specifier_str}",
                )
            return CheckResult(
                "CLI version compatibility",
                "fail",
                f"{__version__} does not match {specifier_str}",
                fix=f"Install a compatible version: pip install 'kamiwaza-sdk{specifier_str}'",
            )
        except (InvalidSpecifier, InvalidVersion) as exc:
            return CheckResult(
                "CLI version compatibility",
                "warn",
                f"Could not parse kz_ext_version: {exc}",
            )

    def _check_python_runtime_lib(self, req_file: Path) -> CheckResult:
        compat_range = (
            _compatibility_bundle()
            .get("runtime_lib_compat", {})
            .get("python", {})
            .get("kamiwaza-extensions-lib", "")
        )
        # PR-86 H7: parse via ``packaging.requirements.Requirement`` so we
        # don't accidentally match prefix-aliases like
        # ``kamiwaza-extensions-lib-extras`` and we tolerate PEP 508 extras
        # / env-markers (``kamiwaza-extensions-lib[extras]>=0.3``).
        declared_req: Optional[Requirement] = None
        for line in req_file.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            try:
                req = Requirement(line)
            except InvalidRequirement:
                continue
            if req.name.lower() != "kamiwaza-extensions-lib":
                continue
            declared_req = req
            break
        if declared_req is None:
            # ENG-3901 / F-003: include the CLI version in the fix hint so a
            # developer with a stale ``kz-ext`` install can spot the version
            # skew. Without this, ``compat_range`` is read from the bundle
            # baked into the installed wheel — which may differ from what
            # develop says is current — and the suggested pin sounds
            # authoritative even when the CLI itself is out of date.
            return CheckResult(
                "Runtime lib (Python)",
                "warn",
                "kamiwaza-extensions-lib not found in requirements.txt",
                fix=(
                    f"Add 'kamiwaza-extensions-lib{compat_range}' to "
                    f"requirements.txt (range from kz-ext {__version__}; "
                    "if this looks stale, upgrade kz-ext first: "
                    "pip install --upgrade kamiwaza-sdk)"
                ),
            )
        declared_spec = str(declared_req.specifier) or ""
        if not compat_range:
            return CheckResult(
                "Runtime lib (Python)",
                "pass",
                f"kamiwaza-extensions-lib{declared_spec} matches any (no compat range bundled)",
            )
        try:
            supported = SpecifierSet(compat_range)
        except InvalidSpecifier:
            # Bundle range malformed — fail open with the declared spec passing.
            return CheckResult(
                "Runtime lib (Python)",
                "pass",
                f"kamiwaza-extensions-lib{declared_spec} (compat range unparseable, skipping check)",
            )
        # PR-86 M6: range-vs-range overlap. ``declared_req.specifier`` is a
        # SpecifierSet too — every version that satisfies it MUST also satisfy
        # ``supported`` for the dependency to be safe. We approximate by
        # checking the declared spec's lower bound + a few key probe points.
        out_of_range_reason = _python_spec_outside_supported(
            declared_req.specifier, supported
        )
        if out_of_range_reason is not None:
            # ENG-3901 / F-003: the compat range is read from the bundle baked
            # into THIS kz-ext install. If the CLI is stale, the bundle is
            # too — and our "Update to ..." suggestion would point at the
            # wrong window. Surfacing the CLI version in the hint lets
            # developers spot the skew themselves.
            return CheckResult(
                "Runtime lib (Python)",
                "warn",
                f"kamiwaza-extensions-lib{declared_spec} is outside CLI compatibility range {compat_range} ({out_of_range_reason})",
                fix=(
                    f"Update to 'kamiwaza-extensions-lib{compat_range}' "
                    f"(range from kz-ext {__version__}; if this looks stale, "
                    "upgrade kz-ext first: pip install --upgrade kamiwaza-sdk)"
                ),
            )
        return CheckResult(
            "Runtime lib (Python)",
            "pass",
            f"kamiwaza-extensions-lib{declared_spec} matches {compat_range}",
        )

    def _check_ts_runtime_lib(self, pkg_file: Path) -> CheckResult:
        compat_range = (
            _compatibility_bundle()
            .get("runtime_lib_compat", {})
            .get("typescript", {})
            .get("@kamiwaza-ai/extensions-lib", "")
        )
        try:
            with pkg_file.open() as f:
                data = json.load(f)
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        except (json.JSONDecodeError, FileNotFoundError):
            return CheckResult(
                "Runtime lib (TypeScript)",
                "warn",
                "package.json could not be parsed",
            )
        declared = deps.get("@kamiwaza-ai/extensions-lib")
        if declared is None:
            # ENG-3901 / F-003: surface CLI version + upgrade hint so a
            # stale ``kz-ext`` (with a stale bundled compat range) doesn't
            # silently mislead. Mirrors the Python sibling check above.
            return CheckResult(
                "Runtime lib (TypeScript)",
                "warn",
                "@kamiwaza-ai/extensions-lib not found in package.json",
                fix=(
                    f'Add "@kamiwaza-ai/extensions-lib": '
                    f'"{compat_range or "^0.4.0"}" to package.json '
                    f"dependencies (range from kz-ext {__version__}; "
                    "if this looks stale, upgrade kz-ext first: "
                    "pip install --upgrade kamiwaza-sdk)"
                ),
            )
        if compat_range:
            # Round-5 C1: parse the bundle's TS range with the npm-semver
            # parser, not ``SpecifierSet``. The bundle's TS entry uses npm
            # syntax (whitespace-separated bounds) so it can be rendered
            # directly into ``frontend/package.json`` — feeding it to
            # ``SpecifierSet`` (PEP 440, comma-separated) crashed with
            # ``InvalidSpecifier`` and the doctor silently fell open.
            supported_lower, supported_upper = _npm_bounds(compat_range)
            if supported_lower is not None or supported_upper is not None:
                # Round-4 H3: full-containment check (was: lower-only probe).
                # Parse npm semver bounds from `declared` and compare both
                # endpoints against `supported` to catch open-ended specs
                # like ">=0.2" or "0.2" that admit future major releases.
                declared_lower, declared_upper = _npm_bounds(declared)
                reason = _bounds_outside_supported(
                    declared_lower,
                    declared_upper,
                    supported_lower,
                    supported_upper,
                )
                if reason is not None:
                    # ENG-3901 / F-003: same CLI-version hint as the
                    # Python check — a stale ``kz-ext`` ships a stale
                    # bundle, and the suggested range would be wrong.
                    return CheckResult(
                        "Runtime lib (TypeScript)",
                        "warn",
                        f"@kamiwaza-ai/extensions-lib {declared} is outside CLI compatibility range {compat_range} ({reason})",
                        fix=(
                            f'Update to "@kamiwaza-ai/extensions-lib": '
                            f'"{compat_range}" '
                            f"(range from kz-ext {__version__}; "
                            "if this looks stale, upgrade kz-ext first: "
                            "pip install --upgrade kamiwaza-sdk)"
                        ),
                    )
        return CheckResult(
            "Runtime lib (TypeScript)",
            "pass",
            f"@kamiwaza-ai/extensions-lib {declared} matches {compat_range or 'any'}",
        )

    # ------------------------------------------------------------------
    # SDK override checks
    # ------------------------------------------------------------------

    def _check_sdk_override(self) -> List[CheckResult]:
        """Check SDK override configuration if present."""
        from kamiwaza_extensions.sdk_override import (
            resolve_sdk_override,
            validate_sdk_override,
        )

        results: List[CheckResult] = []
        cwd = Path.cwd()

        spec = resolve_sdk_override(None, cwd)
        if spec is None:
            return results  # No override configured — skip

        results.append(
            CheckResult(
                "SDK override config",
                "pass",
                f"Loaded from .kz-ext/local.yaml (sdk_repo: {spec.sdk_repo})",
            )
        )

        validation = validate_sdk_override(spec)

        # SDK repo exists
        if spec.sdk_repo.is_dir():
            results.append(CheckResult("SDK repo exists", "pass", str(spec.sdk_repo)))
        else:
            results.append(
                CheckResult(
                    "SDK repo exists",
                    "fail",
                    f"Not found: {spec.sdk_repo}",
                    fix="Check path in .kz-ext/local.yaml",
                )
            )
            return results  # Can't check further

        # Python lib
        if spec.python:
            if spec.python_lib_path.is_dir():
                results.append(
                    CheckResult(
                        "SDK Python lib",
                        "pass",
                        "kamiwaza_extensions_lib/ found",
                    )
                )
            else:
                results.append(
                    CheckResult(
                        "SDK Python lib",
                        "fail",
                        "kamiwaza_extensions_lib/ not found in SDK repo",
                    )
                )

        # TypeScript lib
        if spec.typescript:
            if spec.typescript_lib_path.is_dir():
                results.append(
                    CheckResult(
                        "SDK TypeScript lib",
                        "pass",
                        "kamiwaza-ai-extensions-lib/ found",
                    )
                )
            else:
                results.append(
                    CheckResult(
                        "SDK TypeScript lib",
                        "fail",
                        "kamiwaza-ai-extensions-lib/ not found in SDK repo",
                    )
                )

            # dist/ check
            if spec.typescript_lib_path.is_dir():
                if spec.typescript_dist_path.is_dir():
                    # Check staleness via validation warnings
                    stale = any("stale" in w for w in validation.warnings)
                    if stale:
                        results.append(
                            CheckResult(
                                "SDK TypeScript dist/",
                                "warn",
                                "dist/ exists but may be stale (src/ is newer)",
                                fix=f"cd {spec.typescript_lib_path} && npm run build",
                            )
                        )
                    else:
                        results.append(
                            CheckResult(
                                "SDK TypeScript dist/",
                                "pass",
                                "Built and up to date",
                            )
                        )
                else:
                    results.append(
                        CheckResult(
                            "SDK TypeScript dist/",
                            "warn",
                            "dist/ not found — will be built on first run",
                            fix=f"cd {spec.typescript_lib_path} && npm install && npm run build",
                        )
                    )

        # Template contract check
        ext_dir = cwd
        metadata_path = ext_dir / "kamiwaza.json"
        if not metadata_path.exists():
            found = list(ext_dir.glob("*/kamiwaza.json"))
            if found:
                ext_dir = found[0].parent

        contract_ok = True
        req_file = ext_dir / "backend" / "requirements.txt"
        if not req_file.exists():
            req_file = ext_dir / "requirements.txt"
        if req_file.exists():
            content = req_file.read_text()
            if "kamiwaza-extensions-lib" not in content:
                contract_ok = False

        pkg_file = ext_dir / "frontend" / "package.json"
        if not pkg_file.exists():
            pkg_file = ext_dir / "package.json"
        if pkg_file.exists():
            try:
                with pkg_file.open() as f:
                    data = json.load(f)
                deps = {
                    **data.get("dependencies", {}),
                    **data.get("devDependencies", {}),
                }
                if "@kamiwaza-ai/extensions-lib" not in deps:
                    contract_ok = False
            except (json.JSONDecodeError, FileNotFoundError):
                pass

        if contract_ok:
            results.append(
                CheckResult(
                    "SDK override contract",
                    "pass",
                    "requirements.txt and package.json reference runtime libs",
                )
            )
        else:
            results.append(
                CheckResult(
                    "SDK override contract",
                    "warn",
                    "Non-standard install — SDK override may need manual configuration",
                    fix="Ensure requirements.txt has kamiwaza-extensions-lib and "
                    "package.json has @kamiwaza-ai/extensions-lib",
                )
            )

        return results
