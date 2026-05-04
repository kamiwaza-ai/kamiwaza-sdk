"""Map transformed compose + metadata to CreateExtension SDK model."""

from __future__ import annotations

import hashlib
import json
import socket
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from kamiwaza_sdk.schemas.extensions import (
    CreateExtension,
    ExtensionPort,
    ExtensionServiceSpec,
    KamiwazaIntegrationSpec,
    NetworkingSpec,
    ResourceSpec,
    SecuritySpec,
)

from kamiwaza_extensions.compose_transformer import detect_service_url_rewrites
from kamiwaza_extensions.connections import ConnectionInfo

# CRD annotation keys — namespace is ``kamiwaza.io/*`` (NOT ``kamiwaza.ai/*``).
# The platform's annotation persister filters incoming Extension CR annotations
# to the ``kamiwaza.io/*`` namespace; ``kamiwaza.ai/*`` annotations are
# silently dropped on the platform side, leaving ``kz-ext status`` unable
# to surface "Last deployed by..." and similar observability fields.
# (ENG-3901 dry-run finding F-010 — tactical SDK-side workaround until the
# platform broadens its allow-list. The ``.ai`` namespace was tried
# originally for SDK-team-set annotations but is unsupported in practice.)
ANNOTATION_DEPLOYER = "kamiwaza.io/deployer"
ANNOTATION_BUILD_HOST = "kamiwaza.io/build-host"
ANNOTATION_REVISION = "kamiwaza.io/revision"
ANNOTATION_DEPLOYED_AT = "kamiwaza.io/deployed-at"

# The kamiwaza-extension-operator reads this annotation at deploy time
# and rewrites cross-service URL env values from the compose short name
# (``http://backend:8000``) to the deployment-prefixed K8s service name
# (``http://my-app-dev-abc-backend:8000``). Without this annotation,
# bare ``backend`` doesn't resolve in K8s DNS — the frontend's API
# proxy fails with ENOTFOUND. Namespace is ``extensions.kamiwaza.io/*``
# (different from the ``kamiwaza.io/*`` deploy-metadata namespace
# above). The operator recognizes both.
ANNOTATION_SERVICE_REF_REWRITES = "extensions.kamiwaza.io/service-ref-rewrites"


def _compose_resources_to_k8s(resources: Dict[str, str]) -> Dict[str, str]:
    """Translate Docker Compose resource keys to Kubernetes format.

    - ``cpus`` → ``cpu`` (e.g. ``"0.5"`` → ``"500m"``)
    - ``memory`` passed through as-is (``"512M"`` is valid in both)
    """
    out: Dict[str, str] = {}
    for key, val in resources.items():
        if key == "cpus":
            # Convert decimal CPU to millicpu
            try:
                millicpu = int(float(val) * 1000)
                out["cpu"] = f"{millicpu}m"
            except (ValueError, TypeError):
                out["cpu"] = val
        else:
            out[key] = val
    return out


class PayloadBuilder:
    """Build a ``CreateExtension`` request from extension metadata and
    a transformed compose dict."""

    def build(
        self,
        metadata: Dict[str, Any],
        transformed_compose: Dict[str, Any],
        connection: ConnectionInfo,
        dev_name: str,
        *,
        deployer: Optional[str] = None,
        revision: Optional[str] = None,
    ) -> CreateExtension:
        ext_type = self._resolve_type(metadata)
        app_path = (
            f"/runtime/apps/{dev_name}"
            if ext_type == "app"
            else f"/runtime/{ext_type}s/{dev_name}"
        )
        # ``effective_verify_ssl`` centralizes the SSL precedence:
        # KAMIWAZA_VERIFY_SSL env var > dev-TLD auto-disable > persisted
        # connection.verify_ssl. Drives both the per-service env
        # injection (``_build_services``) and the
        # ``tlsRejectUnauthorized`` spec field so the deployed
        # extension's in-cluster callbacks match the developer's intent.
        verify_ssl = connection.effective_verify_ssl()

        services = self._build_services(
            transformed_compose,
            app_path=app_path,
            verify_ssl=verify_ssl,
            extension_type=ext_type,
        )
        origin = connection.url.removesuffix("/api")
        tls_reject = "0" if not verify_ssl else "1"

        kwargs: Dict[str, Any] = dict(
            name=dev_name,
            type=ext_type,
            version=metadata.get("version", "0.0.0"),
            services=services,
            kamiwaza=KamiwazaIntegrationSpec(
                api_url=connection.url,
                public_api_url=connection.url,
                origin=origin,
                use_auth="true",
                tls_reject_unauthorized=tls_reject,
            ),
            networking=NetworkingSpec(ingress_enabled=True),
            security=SecuritySpec(
                risk_tier=metadata.get("risk_tier", 1),
                source_type=metadata.get("source_type", "kamiwaza"),
                verified=metadata.get("verified", False),
            ),
        )

        annotations = self.build_annotations(deployer=deployer, revision=revision)

        # Cross-service URL rewrites: scan each service's env for
        # references to sibling services by short name and emit the
        # operator-consumed ``service-ref-rewrites`` annotation. Ships
        # only when at least one rewrite is needed (no annotation when
        # there are no cross-service URLs).
        rewrites = detect_service_url_rewrites(
            transformed_compose.get("services") or {}, dev_name
        )
        if rewrites:
            annotations[ANNOTATION_SERVICE_REF_REWRITES] = json.dumps(
                rewrites, sort_keys=True, separators=(",", ":")
            )

        if annotations:
            # `CreateExtension` has `extra="allow"` — annotations ride on the
            # request body for the platform to attach to the CRD metadata.
            kwargs["annotations"] = annotations

        return CreateExtension(**kwargs)

    @staticmethod
    def build_annotations(
        *,
        deployer: Optional[str],
        revision: Optional[str],
    ) -> Dict[str, str]:
        """Build the CRD-metadata annotations attached on every dev deploy.

        - ``kamiwaza.io/deployer``     — email of the deploying user
        - ``kamiwaza.io/build-host``   — local hostname of the developer machine
        - ``kamiwaza.io/revision``     — revision tag (image tag suffix)
        - ``kamiwaza.io/deployed-at``  — ISO-8601 UTC timestamp of the deploy

        Empty values are dropped so consumers can ``in``-check without
        seeing meaningless empty strings.
        """
        out: Dict[str, str] = {}
        if deployer:
            out[ANNOTATION_DEPLOYER] = deployer
        try:
            host = socket.gethostname()
        except OSError:
            host = ""
        if host:
            out[ANNOTATION_BUILD_HOST] = host
        if revision:
            out[ANNOTATION_REVISION] = revision
        out[ANNOTATION_DEPLOYED_AT] = datetime.now(timezone.utc).isoformat()
        return out

    # ------------------------------------------------------------------
    # Dev naming
    # ------------------------------------------------------------------

    @staticmethod
    def make_dev_name(extension_name: str, user_id: Optional[str] = None) -> str:
        """Generate a unique dev deployment name.

        Format: ``{name}-dev-{hash6}`` where *hash6* is derived from the
        user ID (or ``"local"`` when no user context).
        """
        seed = user_id or "local"
        h = hashlib.sha256(seed.encode()).hexdigest()[:6]
        return f"{extension_name}-dev-{h}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_services(
        self,
        transformed: Dict[str, Any],
        app_path: str = "",
        verify_ssl: bool = True,
        extension_type: str = "app",
    ) -> List[ExtensionServiceSpec]:
        services_dict = transformed.get("services") or {}
        specs: List[ExtensionServiceSpec] = []

        # Determine primary service: prefer "frontend", fall back to first with ports
        primary_name = None
        for svc_name, svc in services_dict.items():
            ports = self._parse_ports(svc.get("ports", []))
            if svc_name == "frontend" and ports:
                primary_name = svc_name
                break
        if primary_name is None:
            for svc_name, svc in services_dict.items():
                if self._parse_ports(svc.get("ports", [])):
                    primary_name = svc_name
                    break

        for svc_name, svc in services_dict.items():
            ports = self._parse_ports(svc.get("ports", []))
            env = self._parse_env(svc.get("environment", []))
            resources = self._parse_resources(svc)

            is_primary = svc_name == primary_name

            # Inject platform env vars
            if is_primary and app_path:
                env.append({"name": "KAMIWAZA_APP_PATH", "value": app_path})
            if not verify_ssl:
                env.append({"name": "KAMIWAZA_VERIFY_SSL", "value": "false"})

            health_check = self._default_health_check(
                svc_name,
                svc,
                ports,
                extension_type=extension_type,
                is_primary=is_primary,
            )

            spec_kwargs: Dict[str, Any] = dict(
                name=svc_name,
                image=svc.get("image", ""),
                primary=is_primary,
                ports=ports,
                env=env if env else None,
                replicas=1,
                resources=resources,
            )
            if health_check:
                spec_kwargs["healthCheck"] = health_check

            specs.append(ExtensionServiceSpec(**spec_kwargs))

        return specs

    @staticmethod
    def _parse_ports(ports: List[Any]) -> List[ExtensionPort]:
        result = []
        for p in ports:
            s = str(p)
            # Strip protocol suffix if present
            proto = "TCP"
            if "/" in s:
                s, proto_str = s.rsplit("/", 1)
                proto = (
                    proto_str.upper() if proto_str.upper() in ("TCP", "UDP") else "TCP"
                )
            try:
                result.append(
                    ExtensionPort(container_port=int(s), protocol=proto, name="http")
                )
            except (ValueError, TypeError):
                continue
        return result

    @staticmethod
    def _parse_env(env: Any) -> List[Dict[str, Any]]:
        """Convert compose env formats to K8s env format."""
        result = []
        if isinstance(env, list):
            for item in env:
                if isinstance(item, str):
                    if "=" in item:
                        key, _, val = item.partition("=")
                        result.append({"name": key, "value": val})
                    else:
                        result.append({"name": item})
                elif isinstance(item, dict):
                    for k, v in item.items():
                        result.append({"name": str(k), "value": str(v)})
        elif isinstance(env, dict):
            for k, v in env.items():
                entry: Dict[str, Any] = {"name": str(k)}
                if v is not None:
                    entry["value"] = str(v)
                result.append(entry)
        return result

    @staticmethod
    def _default_health_check(
        svc_name: str,
        svc: Dict[str, Any],
        ports: List[ExtensionPort],
        *,
        extension_type: str = "app",
        is_primary: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Generate a default health check based on service type.

        Path-selection rules (ENG-3901 / F-013):

        - Frontend (Next.js) services use a node-based exec probe that
          resolves the basePath env var dynamically.
        - Backend services in app-type extensions probe ``/health`` —
          the scaffolded FastAPI backend ships an explicit /health route.
        - Primary services in **service** and **tool** extensions probe
          ``/`` — those scaffolds ship a minimal stub (``python -m
          http.server`` for service, FastMCP for tool) that doesn't
          declare /health. Probing /health on those stubs returns 404
          and the K8s startup probe fails the pod into restart loops.
          Probing ``/`` works because both stubs respond to it.

        Precedence note: the Node-frontend probe (when its detection
        hits — service name ``frontend`` AND Node-like hints in env)
        wins over the per-type tool/service rules below. That's the
        right call because a Node Next.js frontend needs a node probe
        regardless of extension shape. Realistic conflict only arises
        if a tool extension's primary is named ``frontend`` AND carries
        ``NEXT_PUBLIC_*`` env keys — vanishingly rare in practice
        (the tool template names its primary ``tool``).
        """
        if not ports:
            return None
        port = ports[0].container_port

        if _should_use_node_frontend_probe(svc_name, svc):
            # Frontend: use node to resolve basePath env vars reliably
            # (shell-based wget probes fail with nested ${} on Alpine)
            probe_script = (
                "const v=s=>(s&&!s.includes('${'))?s:'';"
                "const base=(v(process.env.NEXT_PUBLIC_APP_BASE_PATH)"
                "||v(process.env.KAMIWAZA_APP_PATH)||'').replace(/\\/$/,'')||'/';"
                f"require('http').get({{host:'127.0.0.1',port:{port},path:base}},"
                "(res)=>process.exit(res.statusCode===200?0:1))"
                ".on('error',()=>process.exit(1));"
            )
            return {
                "exec": {
                    "command": ["node", "-e", probe_script],
                },
                "initialDelaySeconds": 30,
                "periodSeconds": 10,
                "failureThreshold": 30,
                "startPeriod": 300,
                "timeoutSeconds": 5,
            }
        # Path-selection rules (see docstring). Tool primary probes /sse —
        # the FastMCP SSE endpoint — even though the response body streams
        # past the K8s 5s probe timeout. The platform's CR API currently
        # rejects ``tcpSocket`` and ``exec`` healthCheck shapes with an
        # opaque 500, leaving httpGet as the only path. /sse is preferable
        # to /health (404 under FastMCP) — the headers come back 200 even
        # if the probe times out reading the body, which keeps the pod
        # restart-loop from firing. Net result: pod runs and serves
        # requests, but K8s may take longer to mark it Ready. The proper
        # fix is to teach the platform API to accept tcpSocket/exec
        # probes (tracked separately).
        if extension_type == "service" and is_primary:
            path = "/"
        elif extension_type == "tool" and is_primary:
            path = "/sse"
        elif svc_name == "frontend":
            path = "/"
        else:
            path = "/health"
        return {
            "httpGet": {"path": path, "port": port},
            "initialDelaySeconds": 10,
            "periodSeconds": 10,
            "failureThreshold": 3,
            "startPeriod": 5,
            "timeoutSeconds": 5,
        }

    @staticmethod
    def _parse_resources(svc: Dict[str, Any]) -> Optional[ResourceSpec]:
        deploy = svc.get("deploy", {})
        res = deploy.get("resources", {})
        limits = res.get("limits")
        requests = res.get("requests") or res.get("reservations")
        if limits or requests:
            return ResourceSpec(
                limits=_compose_resources_to_k8s(limits) if limits else None,
                requests=_compose_resources_to_k8s(requests) if requests else None,
            )
        return None

    @staticmethod
    def _resolve_type(metadata: Dict[str, Any]) -> str:
        t = metadata.get("template_type") or metadata.get("type", "app")
        if t not in ("app", "tool", "service"):
            return "app"
        return t


def _should_use_node_frontend_probe(svc_name: str, svc: Dict[str, Any]) -> bool:
    """Use the Node-based probe only for likely Node/Next frontends.

    Generic/static web services may still be called "frontend", so avoid
    forcing a Node probe unless the compose service carries Node-like hints.
    """
    if svc_name != "frontend":
        return False

    env = svc.get("environment", {})
    env_keys: List[str] = []
    if isinstance(env, dict):
        env_keys = [str(key) for key in env.keys()]
    elif isinstance(env, list):
        for item in env:
            if isinstance(item, str):
                key, _, _ = item.partition("=")
                env_keys.append(key or item)
            elif isinstance(item, dict):
                env_keys.extend(str(key) for key in item.keys())

    if any(key == "BACKEND_URL" or key.startswith("NEXT_PUBLIC_") for key in env_keys):
        return True

    for key in ("command", "entrypoint"):
        value = svc.get(key)
        if isinstance(value, list):
            text = " ".join(str(part) for part in value).lower()
        else:
            text = str(value).lower()
        if "node" in text or "next" in text:
            return True

    image = str(svc.get("image", "")).lower()
    if any(token in image for token in ("nginx", "caddy", "httpd", "apache", "static")):
        return False

    ports = svc.get("ports", [])
    for port in ports:
        port_str = str(port).split("/", 1)[0]
        try:
            container_port = int(port_str.rsplit(":", 1)[-1])
        except ValueError:
            continue
        if container_port in {3000, 3001, 4173, 5173}:
            return True

    return False
