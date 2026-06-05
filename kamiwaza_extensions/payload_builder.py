"""Map transformed compose + metadata to CreateExtension SDK model."""

from __future__ import annotations

import hashlib
import json
import re
import socket
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from kamiwaza_extensions.compose_ports import extract_container_port
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
from kamiwaza_extensions.validators.compose import INVALID_DEPLOY_REQUESTS_TEXT
from kamiwaza_extensions.volume_utils import looks_like_host_path

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


_DNS_LABEL_RE = re.compile(r"[^a-z0-9-]+")


def _build_volume_specs(
    transformed: Dict[str, Any],
) -> tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    """Translate named compose volumes to K8s emptyDir volumes and mounts."""
    volumes: List[Dict[str, Any]] = []
    mounts_by_service: Dict[str, List[Dict[str, Any]]] = {}
    source_to_name: Dict[str, str] = {}
    # Pre-reserve the operator-injected volume names. The kamiwaza-
    # extension-operator rebuilds each Deployment's volume list as
    # ``[tmp emptyDir] + (data PVC if persistence) + svc.Volumes``; if a
    # user's compose volume normalizes to ``tmp`` or ``data``, the
    # reconciled pod would carry duplicate volume names and the K8s API
    # would reject the spec. Seeding the set forces such a volume to a
    # collision-suffixed name (``tmp-2``/``data-2``).
    used_names: set[str] = {"tmp", "data"}

    for svc_name, svc in (transformed.get("services") or {}).items():
        if not isinstance(svc, dict):
            continue
        mounts: List[Dict[str, Any]] = []
        for raw_volume in svc.get("volumes", []) or []:
            parsed = _parse_named_volume_mount(raw_volume)
            if not parsed:
                continue
            source, target, read_only = parsed
            if source not in source_to_name:
                name = _unique_k8s_volume_name(source, used_names)
                source_to_name[source] = name
                volumes.append({"name": name, "emptyDir": {}})

            mount: Dict[str, Any] = {
                "name": source_to_name[source],
                "mountPath": target,
            }
            if read_only:
                mount["readOnly"] = True
            mounts.append(mount)
        if mounts:
            mounts_by_service[svc_name] = mounts

    return volumes, mounts_by_service


def _parse_named_volume_mount(raw_volume: Any) -> Optional[tuple[str, str, bool]]:
    """Return ``(source, target, read_only)`` for named compose volumes."""
    if isinstance(raw_volume, dict):
        volume_type = raw_volume.get("type", "volume")
        source = raw_volume.get("source") or raw_volume.get("src")
        target = (
            raw_volume.get("target")
            or raw_volume.get("destination")
            or raw_volume.get("dst")
        )
        if volume_type != "volume" or not source or not target:
            return None
        source_str = str(source)
        target_str = str(target)
        if looks_like_host_path(source_str) or not target_str.startswith("/"):
            return None
        read_only = bool(raw_volume.get("read_only") or raw_volume.get("readOnly"))
        return source_str, target_str, read_only

    if not isinstance(raw_volume, str):
        return None

    parts = raw_volume.split(":")
    if len(parts) < 2:
        return None
    source, target = parts[0], parts[1]
    if not source or not target or not target.startswith("/"):
        return None
    if looks_like_host_path(source):
        return None

    modes = ",".join(parts[2:]).split(",") if len(parts) > 2 else []
    read_only = any(mode.strip().lower() == "ro" for mode in modes)
    return source, target, read_only


def _unique_k8s_volume_name(source: str, used_names: set[str]) -> str:
    base = _DNS_LABEL_RE.sub("-", source.lower()).strip("-")
    if not base:
        base = "volume"
    base = base[:63].strip("-") or "volume"
    name = base
    counter = 2
    while name in used_names:
        suffix = f"-{counter}"
        prefix_len = 63 - len(suffix)
        name = f"{base[:prefix_len].rstrip('-')}{suffix}"
        counter += 1
    used_names.add(name)
    return name


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
        volumes, service_volume_mounts = _build_volume_specs(transformed_compose)

        services = self._build_services(
            transformed_compose,
            app_path=app_path,
            verify_ssl=verify_ssl,
            extension_type=ext_type,
            metadata=metadata,
            service_volume_mounts=service_volume_mounts,
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
        sandbox = self._build_sandbox_spec(metadata, transformed_compose)
        if sandbox:
            kwargs["sandbox"] = sandbox
        if volumes:
            kwargs["volumes"] = volumes

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

        Format: ``{slug}-dev-{hash6}`` where *slug* is *extension_name*
        coerced to a valid DNS-1123 label and *hash6* is derived from the
        user ID (or ``"local"`` when no user context).

        The slug is sanitized because the platform rejects names that are not
        valid DNS-1123 labels: a display name like ``"Hello Web"`` would
        otherwise be sent verbatim and rejected by ``POST /api/extensions``
        with HTTP 422, so no KamiwazaExtension CR is ever created (ENG-6472).
        """
        seed = user_id or "local"
        h = hashlib.sha256(seed.encode()).hexdigest()[:6]
        suffix = f"-dev-{h}"
        # DNS-1123 label: lowercase alphanumerics or '-', start/end alphanumeric,
        # max 63 chars. Reserve room for the deterministic suffix.
        slug = re.sub(r"[^a-z0-9]+", "-", extension_name.lower()).strip("-")
        slug = slug[: 63 - len(suffix)].strip("-") or "ext"
        return f"{slug}{suffix}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_services(
        self,
        transformed: Dict[str, Any],
        app_path: str = "",
        verify_ssl: bool = True,
        extension_type: str = "app",
        metadata: Optional[Dict[str, Any]] = None,
        service_volume_mounts: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> List[ExtensionServiceSpec]:
        services_dict = transformed.get("services") or {}
        service_volume_mounts = service_volume_mounts or {}
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
                # K8s rule: explicit ``env`` wins over ``envFrom``
                # (ConfigMap injection). Inject BOTH conventional
                # variables explicitly so the deployed pod sees the
                # relaxed setting regardless of what the operator
                # writes into ``KAMIWAZA_TLS_REJECT_UNAUTHORIZED`` in
                # the configmap. Mirrors what the legacy ``make
                # kamiwaza-push`` flow did — that CR was the empirical
                # proof point that explicit env beats configmap-via-spec
                # round-trip and is the reliable mechanism.
                #
                # - ``KAMIWAZA_VERIFY_SSL=false`` for the Python SDK
                #   client (``_verify_ssl_disabled_from_env``).
                # - ``KAMIWAZA_TLS_REJECT_UNAUTHORIZED=0`` for code that
                #   reads the Node.js convention (frontend proxy + many
                #   backend HTTP clients that prefer this var).
                env.append({"name": "KAMIWAZA_VERIFY_SSL", "value": "false"})
                env.append({"name": "KAMIWAZA_TLS_REJECT_UNAUTHORIZED", "value": "0"})

            # Health-check precedence (ENG-4832):
            # 1. ``kamiwaza.json`` → ``services.<svc_name>.healthCheck`` —
            #    the user-facing escape hatch for any tool/service extension
            #    whose primary doesn't serve ``/sse`` (FastMCP feature-flagged
            #    off, REST-only, gRPC-only). Lives in the metadata file
            #    rather than compose so kamiwaza.json stays the single
            #    source of catalog truth.
            # 2. Compose ``x-kamiwaza.healthCheck`` — pre-existing override
            #    path for compose-authored extensions.
            # 3. ``_default_health_check`` heuristics — back-compat default
            #    when neither override is set.
            health_check = _metadata_service_field(metadata, svc_name, "healthCheck")
            if not health_check:
                health_check = _service_extension_field(svc, "healthCheck")
            if not health_check:
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
            automount = _service_extension_field(svc, "automountServiceAccountToken")
            if automount is not None:
                spec_kwargs["automountServiceAccountToken"] = automount
            container_security_context = _service_extension_field(
                svc, "containerSecurityContext"
            )
            if container_security_context is not None:
                spec_kwargs["containerSecurityContext"] = container_security_context
            volume_mounts = service_volume_mounts.get(svc_name)
            if volume_mounts:
                spec_kwargs["volumeMounts"] = volume_mounts

            specs.append(ExtensionServiceSpec(**spec_kwargs))

        return specs

    @staticmethod
    def _parse_ports(ports: List[Any]) -> List[ExtensionPort]:
        """Translate compose-spec ports into CR ExtensionPort entries.

        Accepts both short-form (``"19530"``, ``"19530/udp"``) and long-form
        compose-spec port entries (``{target, protocol, name, app_protocol}``).
        For short-form the port name defaults to ``"http"`` — matches the
        long-standing app/tool extension behavior. Long-form entries pass
        ``name`` and ``app_protocol`` through to the K8s Service, which
        istio reads for L7 protocol selection (ENG-5954).
        """
        result = []
        for p in ports:
            if isinstance(p, dict):
                parsed = PayloadBuilder._parse_port_dict(p)
                if parsed is not None:
                    result.append(parsed)
                continue

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
    def _parse_port_dict(port: Dict[str, Any]) -> Optional[ExtensionPort]:
        """Translate one compose-spec long-form port entry."""
        # Compose-spec long-form: ``target`` is the container port.
        # ``published`` (host port) is stripped upstream by the transformer's
        # ``_strip_host_ports`` and never reaches this function in the normal
        # pipeline — we don't fall back to it because doing so would silently
        # promote a host-port intent into a container port for any direct
        # caller that bypasses the transformer.
        target = port.get("target")
        if target is None:
            return None

        proto_raw = str(port.get("protocol", "tcp")).upper()
        proto = proto_raw if proto_raw in ("TCP", "UDP") else "TCP"

        # Long-form ports without an explicit name still default to "http"
        # so existing extensions that adopt long-form syntax for unrelated
        # reasons (e.g. adding ``protocol: tcp``) don't silently lose the
        # historical port name.
        name = port.get("name") or "http"

        # Prefer the compose-spec ``app_protocol`` key; fall back to the
        # k8s-shaped ``appProtocol`` only when the spec key is absent.
        # Explicit ``in`` rather than ``or`` so an explicitly-empty
        # ``app_protocol: ""`` is treated as "set to empty" rather than
        # silently falling through to ``appProtocol``.
        if "app_protocol" in port:
            app_protocol = port["app_protocol"]
        else:
            app_protocol = port.get("appProtocol")

        try:
            return ExtensionPort(
                container_port=int(target),
                protocol=proto,
                name=name,
                app_protocol=app_protocol,
            )
        except (ValueError, TypeError):
            return None

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
        # opaque 500 (ENG-4833), leaving httpGet as the only path. /sse is
        # preferable to /health (404 under FastMCP) — the headers come back
        # 200 even if the probe times out reading the body, which keeps
        # the pod restart-loop from firing. Net result: pod runs and serves
        # requests, but K8s may take longer to mark it Ready.
        #
        # Escape hatch (ENG-4832): tool extensions that DON'T serve /sse
        # (REST-only, MCP at /mcp via streamable-http, gRPC, feature-flagged
        # MCP off) should declare an explicit probe in ``kamiwaza.json``
        # under ``services.<svc_name>.healthCheck``. That override runs
        # ahead of the heuristics below — see ``_build_services``.
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
        # ENG-5426: the validator rejects `deploy.resources.requests` at
        # validate-time, but `kz-ext dev` (run_dev_remote) builds payloads
        # without invoking ComposeValidator. Raise here so any code path that
        # bypasses the validator still cannot silently drop the typo
        # (which is the ENG-5424 over-reservation failure class).
        if isinstance(res, dict) and "requests" in res:
            raise ValueError(INVALID_DEPLOY_REQUESTS_TEXT)
        limits = res.get("limits")
        requests = res.get("reservations")
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

    @staticmethod
    def _build_sandbox_spec(
        metadata: Dict[str, Any],
        transformed: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        declared = metadata.get("sandbox")
        if isinstance(declared, dict):
            return declared

        env_defaults = metadata.get("env_defaults") or {}
        services = transformed.get("services") or {}
        # Prefer the conventional `sandbox-controller` service; fall back to
        # any other service that explicitly declares SANDBOX_BACKEND=kubernetes.
        ordered = sorted(
            services.items(),
            key=lambda item: 0 if item[0] == "sandbox-controller" else 1,
        )
        for svc_name, svc in ordered:
            env = _service_env_map(svc)
            if env.get("SANDBOX_BACKEND") != "kubernetes":
                continue

            spec: Dict[str, Any] = {
                "enabled": True,
                "service_name": svc_name,
            }
            namespace = env.get("SANDBOX_NAMESPACE")
            if namespace:
                spec["namespace"] = namespace

            allowed_images = [
                item.strip()
                for item in env.get("SANDBOX_ALLOWED_IMAGE_PREFIXES", "").split(",")
                if item.strip()
            ]
            if allowed_images:
                spec["image_whitelist"] = allowed_images

            resources = _sandbox_resources_from_env(env)
            if resources:
                spec["resources"] = resources

            persistence = env.get("SANDBOX_PERSISTENCE") or env_defaults.get(
                "SANDBOX_PERSISTENCE"
            )
            if isinstance(persistence, str):
                spec["persistence"] = persistence.strip().lower() in {
                    "true",
                    "1",
                    "yes",
                    "on",
                }
            elif isinstance(persistence, bool):
                spec["persistence"] = persistence

            return spec

        return None


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
        container_port = extract_container_port(port)
        if container_port in {3000, 3001, 4173, 5173}:
            return True

    return False


def _service_extension_field(svc: Dict[str, Any], key: str) -> Optional[Any]:
    """Read a service override from ``x-kamiwaza``."""
    x_kamiwaza = svc.get("x-kamiwaza")
    if isinstance(x_kamiwaza, dict) and key in x_kamiwaza:
        return x_kamiwaza[key]
    return None


def _metadata_service_field(
    metadata: Optional[Dict[str, Any]],
    svc_name: str,
    key: str,
) -> Optional[Any]:
    """Read a per-service override from ``kamiwaza.json`` (ENG-4832).

    Shape::

        {
          "services": {
            "<svc_name>": { "<key>": <value> }
          }
        }
    """
    if not isinstance(metadata, dict):
        return None
    services = metadata.get("services")
    if not isinstance(services, dict):
        return None
    svc_block = services.get(svc_name)
    if not isinstance(svc_block, dict):
        return None
    return svc_block.get(key)


def _service_env_map(svc: Dict[str, Any]) -> Dict[str, str]:
    env: Dict[str, str] = {}
    raw = svc.get("environment") or {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if value is not None:
                env[str(key)] = str(value)
        return env
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, str) and "=" in entry:
                key, value = entry.split("=", 1)
                env[key] = value
            elif isinstance(entry, dict):
                for key, value in entry.items():
                    if value is not None:
                        env[str(key)] = str(value)
    return env


def _sandbox_resources_from_env(env: Dict[str, str]) -> Optional[Dict[str, Dict[str, str]]]:
    resources: Dict[str, Dict[str, str]] = {}
    requests: Dict[str, str] = {}
    limits: Dict[str, str] = {}
    if env.get("SANDBOX_RESOURCE_CPU_REQUEST"):
        requests["cpu"] = env["SANDBOX_RESOURCE_CPU_REQUEST"]
    if env.get("SANDBOX_RESOURCE_MEMORY_REQUEST"):
        requests["memory"] = env["SANDBOX_RESOURCE_MEMORY_REQUEST"]
    if env.get("SANDBOX_RESOURCE_CPU_LIMIT"):
        limits["cpu"] = env["SANDBOX_RESOURCE_CPU_LIMIT"]
    if env.get("SANDBOX_RESOURCE_MEMORY_LIMIT"):
        limits["memory"] = env["SANDBOX_RESOURCE_MEMORY_LIMIT"]
    if requests:
        resources["requests"] = requests
    if limits:
        resources["limits"] = limits
    return resources or None
