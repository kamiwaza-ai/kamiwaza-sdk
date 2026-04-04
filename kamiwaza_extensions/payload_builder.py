"""Map transformed compose + metadata to CreateExtension SDK model."""

from __future__ import annotations

import hashlib
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

from kamiwaza_extensions.connections import ConnectionInfo


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
    ) -> CreateExtension:
        ext_type = self._resolve_type(metadata)
        app_path = f"/runtime/apps/{dev_name}" if ext_type == "app" else f"/runtime/{ext_type}s/{dev_name}"
        services = self._build_services(
            transformed_compose, app_path=app_path, verify_ssl=connection.verify_ssl,
        )
        origin = connection.url.removesuffix("/api")
        tls_reject = "0" if not connection.verify_ssl else "1"

        return CreateExtension(
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
        self, transformed: Dict[str, Any], app_path: str = "",
        verify_ssl: bool = True,
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

            is_primary = (svc_name == primary_name)

            # Inject platform env vars
            if is_primary and app_path:
                env.append({"name": "KAMIWAZA_APP_PATH", "value": app_path})
            if not verify_ssl:
                env.append({"name": "KAMIWAZA_VERIFY_SSL", "value": "false"})

            health_check = self._default_health_check(svc_name, ports)

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
                proto = proto_str.upper() if proto_str.upper() in ("TCP", "UDP") else "TCP"
            try:
                result.append(ExtensionPort(container_port=int(s), protocol=proto, name="http"))
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
        svc_name: str, ports: List[ExtensionPort],
    ) -> Optional[Dict[str, Any]]:
        """Generate a default health check based on service type."""
        if not ports:
            return None
        port = ports[0].container_port

        if svc_name == "frontend":
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
        else:
            # Backend: HTTP GET /health
            return {
                "httpGet": {"path": "/health", "port": port},
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
