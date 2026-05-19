"""Tests for PayloadBuilder."""

import pytest

from kamiwaza_extensions.connections import ConnectionInfo
from kamiwaza_extensions.payload_builder import (
    ANNOTATION_BUILD_HOST,
    ANNOTATION_DEPLOYED_AT,
    ANNOTATION_DEPLOYER,
    ANNOTATION_REVISION,
    ANNOTATION_SERVICE_REF_REWRITES,
    PayloadBuilder,
)


@pytest.fixture
def builder():
    return PayloadBuilder()


@pytest.fixture
def connection():
    return ConnectionInfo(
        name="test",
        url="https://cluster.kamiwaza.test/api",
        active=True,
        created_at=0.0,
    )


@pytest.fixture
def metadata():
    return {
        "name": "my-app",
        "version": "1.0.0",
        "source_type": "kamiwaza",
        "risk_tier": 1,
        "verified": False,
    }


@pytest.fixture
def transformed_compose():
    return {
        "services": {
            "frontend": {
                "image": "registry.test/my-app-frontend:1.0.0-dev",
                "ports": ["3000"],
                "environment": ["NEXT_PUBLIC_API_URL=http://backend:8000"],
                "deploy": {"resources": {"limits": {"cpus": "0.5", "memory": "512M"}}},
            },
            "backend": {
                "image": "registry.test/my-app-backend:1.0.0-dev",
                "ports": ["8000"],
                "environment": {
                    "OPENAI_BASE_URL": "http://host.docker.internal:8080",
                },
                "deploy": {"resources": {"limits": {"cpus": "1.0", "memory": "1G"}}},
            },
        },
    }


class TestBuild:
    def test_produces_valid_payload(
        self, builder, metadata, transformed_compose, connection
    ):
        payload = builder.build(
            metadata, transformed_compose, connection, "my-app-dev-abc123"
        )
        assert payload.name == "my-app-dev-abc123"
        assert payload.type == "app"
        assert payload.version == "1.0.0"
        assert len(payload.services) == 2

    def test_primary_service_assigned(
        self, builder, metadata, transformed_compose, connection
    ):
        payload = builder.build(metadata, transformed_compose, connection, "test")
        primaries = [s for s in payload.services if s.primary]
        assert len(primaries) == 1
        assert primaries[0].name == "frontend"

    def test_ports_parsed(self, builder, metadata, transformed_compose, connection):
        payload = builder.build(metadata, transformed_compose, connection, "test")
        fe = next(s for s in payload.services if s.name == "frontend")
        assert len(fe.ports) == 1
        assert fe.ports[0].container_port == 3000

    def test_kamiwaza_integration(
        self, builder, metadata, transformed_compose, connection
    ):
        payload = builder.build(metadata, transformed_compose, connection, "test")
        assert payload.kamiwaza.api_url == "https://cluster.kamiwaza.test/api"
        assert payload.kamiwaza.use_auth == "true"

    def test_security_from_metadata(
        self, builder, metadata, transformed_compose, connection
    ):
        payload = builder.build(metadata, transformed_compose, connection, "test")
        assert payload.security.risk_tier == 1


class TestAnnotations:
    """ENG-3887 / §4.2.9 — DeployedImageAnnotation on the CRD payload."""

    def test_annotations_present_when_deployer_and_revision_supplied(
        self,
        builder,
        metadata,
        transformed_compose,
        connection,
    ):
        payload = builder.build(
            metadata,
            transformed_compose,
            connection,
            "my-app-dev-abc",
            deployer="jonathan@kamiwaza.ai",
            revision="1.0.0-dev-a1b2c3d.1714000000",
        )
        # `annotations` rides on `extra="allow"` — read via model_extra.
        annotations = (payload.model_extra or {}).get("annotations")
        assert annotations is not None
        assert annotations[ANNOTATION_DEPLOYER] == "jonathan@kamiwaza.ai"
        assert annotations[ANNOTATION_REVISION] == "1.0.0-dev-a1b2c3d.1714000000"
        assert ANNOTATION_BUILD_HOST in annotations
        # ISO-8601 UTC — sanity-check the format
        assert annotations[ANNOTATION_DEPLOYED_AT].endswith("+00:00")

    def test_no_annotations_when_neither_supplied(
        self,
        builder,
        metadata,
        transformed_compose,
        connection,
    ):
        # If we have nothing meaningful to attach, don't carry an empty dict
        # — but `deployed-at` is always present, since "when" is always known.
        payload = builder.build(
            metadata,
            transformed_compose,
            connection,
            "my-app-dev-abc",
        )
        annotations = (payload.model_extra or {}).get("annotations")
        # build_annotations always sets `deployed-at`, so the key exists even
        # without a deployer/revision. Verify deployer/revision are absent.
        assert annotations is not None
        assert ANNOTATION_DEPLOYER not in annotations
        assert ANNOTATION_REVISION not in annotations
        assert ANNOTATION_DEPLOYED_AT in annotations

    def test_build_annotations_drops_empty_values(self):
        out = PayloadBuilder.build_annotations(deployer=None, revision=None)
        assert ANNOTATION_DEPLOYER not in out
        assert ANNOTATION_REVISION not in out
        assert ANNOTATION_DEPLOYED_AT in out


class TestVerifySslPropagation:
    """The deployed extension's ``tlsRejectUnauthorized`` must reflect
    the developer's intent. Three independent inputs collapse here via
    ``ConnectionInfo.effective_verify_ssl``:
    1. ``KAMIWAZA_VERIFY_SSL`` env var (per-session override)
    2. URL hostname (dev TLDs auto-disable)
    3. Persisted ``connection.verify_ssl`` from ``kz-ext login``
    """

    def test_env_false_overrides_connection_verify_true(
        self,
        builder,
        metadata,
        transformed_compose,
        connection,
        monkeypatch,
    ):
        monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "false")
        assert connection.verify_ssl is True

        payload = builder.build(metadata, transformed_compose, connection, "ext")

        assert payload.kamiwaza.tls_reject_unauthorized == "0"
        primary_env = next(s for s in payload.services if s.primary).env or []
        # Both conventional vars injected so explicit pod env beats
        # whatever the operator writes into the configmap.
        assert {"name": "KAMIWAZA_VERIFY_SSL", "value": "false"} in primary_env
        assert (
            {"name": "KAMIWAZA_TLS_REJECT_UNAUTHORIZED", "value": "0"} in primary_env
        )

    def test_dev_tld_auto_disables_verify(
        self,
        builder,
        metadata,
        transformed_compose,
        monkeypatch,
    ):
        """User logged in normally (verify_ssl=True) against
        ``kamiwaza.test`` — should still ship ``tls_reject="0"`` because
        ``.test`` URLs always use self-signed certs."""
        monkeypatch.delenv("KAMIWAZA_VERIFY_SSL", raising=False)
        conn = ConnectionInfo(
            name="dev",
            url="https://kamiwaza.test/api",
            active=True,
            created_at=0.0,
            verify_ssl=True,  # persisted strict — auto-disable should win
        )

        payload = builder.build(metadata, transformed_compose, conn, "ext")
        assert payload.kamiwaza.tls_reject_unauthorized == "0"

    def test_env_true_re_enables_against_dev_tld(
        self,
        builder,
        metadata,
        transformed_compose,
        monkeypatch,
    ):
        """``KAMIWAZA_VERIFY_SSL=true`` explicitly opts back in even
        for dev TLDs (e.g., user has a trusted local root CA)."""
        monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "true")
        conn = ConnectionInfo(
            name="dev",
            url="https://kamiwaza.test/api",
            active=True,
            created_at=0.0,
            verify_ssl=True,
        )

        payload = builder.build(metadata, transformed_compose, conn, "ext")
        assert payload.kamiwaza.tls_reject_unauthorized == "1"

    def test_production_url_keeps_strict(
        self,
        builder,
        metadata,
        transformed_compose,
        monkeypatch,
    ):
        """Real public hostnames keep persisted strict setting."""
        monkeypatch.delenv("KAMIWAZA_VERIFY_SSL", raising=False)
        conn = ConnectionInfo(
            name="prod",
            url="https://api.kamiwaza.ai/api",
            active=True,
            created_at=0.0,
            verify_ssl=True,
        )

        payload = builder.build(metadata, transformed_compose, conn, "ext")
        assert payload.kamiwaza.tls_reject_unauthorized == "1"


class TestServiceRefRewritesAnnotation:
    """The frontend's compose ``http://backend:8000`` doesn't resolve in
    K8s DNS — bare ``backend`` only works in docker-compose. The
    operator reads ``extensions.kamiwaza.io/service-ref-rewrites`` to
    swap the env value to the deployment-prefixed K8s service name at
    deploy time."""

    def test_emitted_when_compose_has_cross_service_url(
        self,
        builder,
        metadata,
        connection,
    ):
        import json

        # Frontend references ``backend`` (a sibling service); the
        # transformer would have already resolved any ``${VAR:-default}``
        # syntax so we feed it the post-resolution form.
        transformed = {
            "services": {
                "frontend": {
                    "image": "reg/my-app-frontend:dev",
                    "ports": ["3000"],
                    "environment": {"BACKEND_URL": "http://backend:8000"},
                },
                "backend": {
                    "image": "reg/my-app-backend:dev",
                    "ports": ["8000"],
                },
            },
        }
        payload = builder.build(metadata, transformed, connection, "my-app-dev-abc")

        annotations = (payload.model_extra or {}).get("annotations") or {}
        raw = annotations.get(ANNOTATION_SERVICE_REF_REWRITES)
        assert raw is not None, "service-ref-rewrites annotation missing"
        rewrites = json.loads(raw)
        assert rewrites == {
            "frontend": {
                "BACKEND_URL": {
                    "from": "http://backend:8000",
                    "to": "http://my-app-dev-abc-backend:8000",
                }
            }
        }

    def test_omitted_when_no_cross_service_urls(
        self,
        builder,
        metadata,
        connection,
    ):
        transformed = {
            "services": {
                "frontend": {
                    "image": "reg/my-app-frontend:dev",
                    "ports": ["3000"],
                    "environment": {"FOO": "bar"},
                },
            },
        }
        payload = builder.build(metadata, transformed, connection, "my-app-dev-abc")
        annotations = (payload.model_extra or {}).get("annotations") or {}
        assert ANNOTATION_SERVICE_REF_REWRITES not in annotations


class TestComposeVolumes:
    """ENG-4834: named compose volumes must reach the kext payload."""

    def test_named_volume_becomes_empty_dir_and_volume_mount(
        self, builder, metadata, connection
    ):
        transformed = {
            "services": {
                "tool": {
                    "image": "registry.test/tool:dev",
                    "ports": ["8000"],
                    "volumes": ["omniparse-data:/data"],
                },
            },
            "volumes": {"omniparse-data": None},
        }

        payload = builder.build(metadata, transformed, connection, "tool-dev-abc")
        tool = payload.services[0].model_dump()

        assert (payload.model_extra or {})["volumes"] == [
            {"name": "omniparse-data", "emptyDir": {}}
        ]
        assert payload.model_dump()["volumes"] == [
            {"name": "omniparse-data", "emptyDir": {}}
        ]
        assert tool["volumeMounts"] == [
            {"name": "omniparse-data", "mountPath": "/data"}
        ]

    def test_shared_named_volume_is_declared_once(self, builder, metadata, connection):
        transformed = {
            "services": {
                "api": {
                    "image": "registry.test/api:dev",
                    "ports": ["8000"],
                    "volumes": ["shared-data:/cache"],
                },
                "worker": {
                    "image": "registry.test/worker:dev",
                    "volumes": ["shared-data:/cache"],
                },
            },
        }

        payload = builder.build(metadata, transformed, connection, "app-dev-abc")
        services = {svc.name: svc.model_dump() for svc in payload.services}

        assert (payload.model_extra or {})["volumes"] == [
            {"name": "shared-data", "emptyDir": {}}
        ]
        assert services["api"]["volumeMounts"] == [
            {"name": "shared-data", "mountPath": "/cache"}
        ]
        assert services["worker"]["volumeMounts"] == [
            {"name": "shared-data", "mountPath": "/cache"}
        ]

    def test_long_form_volume_is_supported_and_read_only(
        self, builder, metadata, connection
    ):
        transformed = {
            "services": {
                "backend": {
                    "image": "registry.test/backend:dev",
                    "ports": ["8000"],
                    "volumes": [
                        {
                            "type": "volume",
                            "source": "backend_data",
                            "target": "/app/persist",
                            "read_only": True,
                        }
                    ],
                },
            },
        }

        payload = builder.build(metadata, transformed, connection, "app-dev-abc")
        backend = payload.services[0].model_dump()

        assert (payload.model_extra or {})["volumes"] == [
            {"name": "backend-data", "emptyDir": {}}
        ]
        assert backend["volumeMounts"] == [
            {
                "name": "backend-data",
                "mountPath": "/app/persist",
                "readOnly": True,
            }
        ]

    def test_no_volumes_keeps_payload_unchanged(
        self, builder, metadata, transformed_compose, connection
    ):
        payload = builder.build(
            metadata, transformed_compose, connection, "app-dev-abc"
        )

        assert "volumes" not in (payload.model_extra or {})
        assert all(
            "volumeMounts" not in (svc.model_extra or {}) for svc in payload.services
        )

    def test_interpolated_host_path_is_not_emitted_as_empty_dir(
        self, builder, metadata, connection
    ):
        """PR-113 review High #1: a shell-interpolated bind source
        (``${PWD}/src``) must NOT be normalized into a named volume and
        emitted as an emptyDir over the image's baked files. It is a
        host path and the validator rejects it; the payload builder must
        agree and drop it."""
        transformed = {
            "services": {
                "tool": {
                    "image": "registry.test/tool:dev",
                    "ports": ["8000"],
                    "volumes": [
                        "${PWD}/src:/app/src",
                        "$HOME/.cache:/cache",
                    ],
                },
            },
        }

        payload = builder.build(metadata, transformed, connection, "tool-dev-abc")
        tool = payload.services[0].model_dump()

        assert "volumes" not in (payload.model_extra or {})
        assert "volumeMounts" not in tool

    def test_user_volume_named_tmp_avoids_operator_collision(
        self, builder, metadata, connection
    ):
        """PR-113 review High #2: the operator injects volumes named
        ``tmp`` and ``data``. A user compose volume that normalizes to
        either must be renamed so the reconciled Deployment has no
        duplicate volume names (K8s rejects duplicates)."""
        transformed = {
            "services": {
                "tool": {
                    "image": "registry.test/tool:dev",
                    "ports": ["8000"],
                    "volumes": ["tmp:/scratch", "data:/store"],
                },
            },
        }

        payload = builder.build(metadata, transformed, connection, "tool-dev-abc")
        tool = payload.services[0].model_dump()

        emitted = {v["name"] for v in (payload.model_extra or {})["volumes"]}
        assert emitted.isdisjoint({"tmp", "data"})
        mount_names = {m["name"] for m in tool["volumeMounts"]}
        # Mounts must reference the renamed volumes, not the reserved ones.
        assert mount_names == emitted
        assert mount_names.isdisjoint({"tmp", "data"})


class TestEnvParsing:
    def test_list_format(self, builder):
        result = builder._parse_env(["KEY=value", "BARE_KEY"])
        assert result == [
            {"name": "KEY", "value": "value"},
            {"name": "BARE_KEY"},
        ]

    def test_dict_format(self, builder):
        result = builder._parse_env({"KEY": "value", "NULL_KEY": None})
        assert {"name": "KEY", "value": "value"} in result
        assert {"name": "NULL_KEY"} in result

    def test_empty(self, builder):
        assert builder._parse_env([]) == []
        assert builder._parse_env({}) == []


class TestServiceOverrides:
    def test_x_kamiwaza_overrides_are_carried_into_service_spec(
        self, builder, metadata, connection
    ):
        transformed = {
            "services": {
                "postgres": {
                    "image": "postgres:15",
                    "ports": ["5432"],
                    "x-kamiwaza": {
                        "containerSecurityContext": {
                            "runAsNonRoot": False,
                            "runAsUser": 0,
                            "readOnlyRootFilesystem": False,
                        },
                        "healthCheck": {"tcpSocket": {"port": 5432}},
                    },
                },
                "sandbox-controller": {
                    "image": "reg/sandbox-controller:dev",
                    "ports": ["8085"],
                    "x-kamiwaza": {"automountServiceAccountToken": True},
                },
            },
        }

        payload = builder.build(metadata, transformed, connection, "my-app-dev-abc")
        services = {svc.name: svc.model_dump() for svc in payload.services}

        assert services["postgres"]["containerSecurityContext"] == {
            "runAsNonRoot": False,
            "runAsUser": 0,
            "readOnlyRootFilesystem": False,
        }
        assert services["postgres"]["healthCheck"] == {"tcpSocket": {"port": 5432}}
        assert services["sandbox-controller"]["automountServiceAccountToken"] is True

    def test_kubernetes_sandbox_controller_emits_sandbox_spec(
        self, builder, metadata, connection
    ):
        transformed = {
            "services": {
                "sandbox-controller": {
                    "image": "reg/sandbox-controller:dev",
                    "ports": ["8085"],
                    "environment": {
                        "SANDBOX_BACKEND": "kubernetes",
                        "SANDBOX_NAMESPACE": "kamiwaza-sandboxes",
                        "SANDBOX_ALLOWED_IMAGE_PREFIXES": "ghcr.io/openhands/,ghcr.io/acme/agent",
                        "SANDBOX_RESOURCE_CPU_REQUEST": "50m",
                        "SANDBOX_RESOURCE_CPU_LIMIT": "2",
                        "SANDBOX_RESOURCE_MEMORY_REQUEST": "512Mi",
                        "SANDBOX_RESOURCE_MEMORY_LIMIT": "4Gi",
                    },
                }
            },
        }

        payload = builder.build(metadata, transformed, connection, "my-app-dev-abc")

        assert (payload.model_extra or {})["sandbox"] == {
            "enabled": True,
            "service_name": "sandbox-controller",
            "namespace": "kamiwaza-sandboxes",
            "image_whitelist": ["ghcr.io/openhands/", "ghcr.io/acme/agent"],
            "resources": {
                "requests": {"cpu": "50m", "memory": "512Mi"},
                "limits": {"cpu": "2", "memory": "4Gi"},
            },
        }


    def test_non_kubernetes_backend_emits_no_sandbox_spec(
        self, builder, metadata, connection
    ):
        transformed = {
            "services": {
                "sandbox-controller": {
                    "image": "reg/sandbox-controller:dev",
                    "ports": ["8085"],
                    "environment": {"SANDBOX_BACKEND": "docker"},
                }
            },
        }
        payload = builder.build(metadata, transformed, connection, "my-app-dev-abc")
        assert "sandbox" not in (payload.model_extra or {})

    def test_no_sandbox_backend_emits_no_sandbox_spec(
        self, builder, metadata, connection
    ):
        transformed = {
            "services": {
                "worker": {"image": "reg/worker:dev", "ports": ["8000"]},
            },
        }
        payload = builder.build(metadata, transformed, connection, "my-app-dev-abc")
        assert "sandbox" not in (payload.model_extra or {})

    def test_declared_metadata_sandbox_overrides_env_inference(
        self, builder, connection
    ):
        meta = {
            "name": "my-app",
            "type": "app",
            "version": "1.0.0",
            "sandbox": {
                "enabled": True,
                "service_name": "explicit-controller",
                "namespace": "explicit-ns",
            },
        }
        transformed = {
            "services": {
                "sandbox-controller": {
                    "image": "reg/sandbox-controller:dev",
                    "ports": ["8085"],
                    "environment": {
                        "SANDBOX_BACKEND": "kubernetes",
                        "SANDBOX_NAMESPACE": "inferred-ns",
                    },
                }
            },
        }
        payload = builder.build(meta, transformed, connection, "my-app-dev-abc")
        assert (payload.model_extra or {})["sandbox"] == {
            "enabled": True,
            "service_name": "explicit-controller",
            "namespace": "explicit-ns",
        }

    def test_persistence_accepts_common_truthy_strings(
        self, builder, metadata, connection
    ):
        for truthy in ("true", "1", "yes", "on", "TRUE"):
            transformed = {
                "services": {
                    "sandbox-controller": {
                        "image": "reg/sandbox-controller:dev",
                        "ports": ["8085"],
                        "environment": {
                            "SANDBOX_BACKEND": "kubernetes",
                            "SANDBOX_PERSISTENCE": truthy,
                        },
                    }
                },
            }
            payload = builder.build(
                metadata, transformed, connection, "my-app-dev-abc"
            )
            sandbox = (payload.model_extra or {})["sandbox"]
            assert sandbox["persistence"] is True, f"failed for {truthy!r}"

    def test_automount_false_is_preserved(self, builder, metadata, connection):
        transformed = {
            "services": {
                "worker": {
                    "image": "reg/worker:dev",
                    "ports": ["8000"],
                    "x-kamiwaza": {"automountServiceAccountToken": False},
                }
            },
        }
        payload = builder.build(metadata, transformed, connection, "my-app-dev-abc")
        services = {svc.name: svc.model_dump() for svc in payload.services}
        assert services["worker"]["automountServiceAccountToken"] is False


class TestDevNaming:
    def test_format(self):
        name = PayloadBuilder.make_dev_name("my-app", user_id="user-123")
        assert name.startswith("my-app-dev-")
        assert len(name.split("-")[-1]) == 6  # 6 char hash

    def test_deterministic(self):
        a = PayloadBuilder.make_dev_name("my-app", user_id="user-123")
        b = PayloadBuilder.make_dev_name("my-app", user_id="user-123")
        assert a == b

    def test_different_users_different_names(self):
        a = PayloadBuilder.make_dev_name("my-app", user_id="user-a")
        b = PayloadBuilder.make_dev_name("my-app", user_id="user-b")
        assert a != b

    def test_no_user_uses_local(self):
        name = PayloadBuilder.make_dev_name("my-app")
        assert "dev-" in name


class TestResourceParsing:
    def test_cpus_converted_to_millicpu(self, builder):
        svc = {"deploy": {"resources": {"limits": {"cpus": "0.5", "memory": "512M"}}}}
        res = builder._parse_resources(svc)
        assert res.limits["cpu"] == "500m"
        assert "cpus" not in res.limits
        assert res.limits["memory"] == "512M"

    def test_whole_cpu_converted(self, builder):
        svc = {"deploy": {"resources": {"limits": {"cpus": "2.0", "memory": "1G"}}}}
        res = builder._parse_resources(svc)
        assert res.limits["cpu"] == "2000m"

    def test_no_resources_returns_none(self, builder):
        assert builder._parse_resources({}) is None

    def test_reservations_maps_to_requests(self, builder):
        # ENG-5426: Compose `reservations` is the term that maps to K8s
        # `requests`. The compose-side `requests` key is now rejected by
        # ComposeValidator, so the parser only reads `reservations`.
        svc = {
            "deploy": {
                "resources": {
                    "limits": {"cpus": "1.0", "memory": "1G"},
                    "reservations": {"cpus": "0.5", "memory": "512M"},
                }
            }
        }
        res = builder._parse_resources(svc)
        assert res.limits["cpu"] == "1000m"
        assert res.requests["cpu"] == "500m"
        assert res.requests["memory"] == "512M"

    def test_parse_resources_raises_on_requests_key(self, builder):
        # ENG-5426 (Codex H1): `run_dev_remote` builds payloads without
        # invoking ComposeValidator, so a parser that *silently* dropped
        # an unknown `requests` key would reproduce the ENG-5424
        # over-reservation incident on the `kz-ext dev` path. Pair the
        # validator's fail-fast at validate-time with the parser's
        # fail-fast at parse-time. Dev-path-level coverage is implicit:
        # `run_dev_remote → payload_builder.build → _parse_resources` is
        # a straight call chain (payload_builder.py:333), and the dev
        # tests mock `PayloadBuilder` wholesale, so the parser layer is
        # where this contract is most cleanly pinned.
        svc = {
            "deploy": {
                "resources": {
                    "limits": {"cpus": "1.0", "memory": "1G"},
                    "requests": {"cpus": "0.5", "memory": "512M"},
                }
            }
        }
        with pytest.raises(ValueError) as exc_info:
            builder._parse_resources(svc)
        assert (
            "deploy.resources.requests is not a valid Docker Compose key"
            in str(exc_info.value)
        )


class TestResolveType:
    def test_default_app(self, builder):
        assert builder._resolve_type({}) == "app"

    def test_template_type_takes_precedence(self, builder):
        assert builder._resolve_type({"template_type": "tool", "type": "app"}) == "tool"

    def test_service_type(self, builder):
        assert builder._resolve_type({"template_type": "service"}) == "service"


class TestHealthChecks:
    def test_frontend_uses_node_probe(
        self, builder, metadata, transformed_compose, connection
    ):
        payload = builder.build(metadata, transformed_compose, connection, "test")
        frontend = next(s for s in payload.services if s.name == "frontend")

        health_check = frontend.model_dump()["healthCheck"]
        assert health_check["exec"]["command"][0] == "node"

    def test_generic_frontend_without_node_hints_uses_root_http_probe(
        self, builder, metadata, connection
    ):
        transformed = {
            "services": {
                "frontend": {
                    "image": "registry.test/my-app-frontend:1.0.0-dev",
                    "ports": ["8080"],
                },
            },
        }

        payload = builder.build(metadata, transformed, connection, "test")
        frontend = payload.services[0]

        health_check = frontend.model_dump()["healthCheck"]
        assert frontend.primary is True
        assert health_check["httpGet"] == {"path": "/", "port": 8080}
        assert "exec" not in health_check

    def test_frontend_port_3000_without_explicit_hints_uses_node_probe(
        self, builder, metadata, connection
    ):
        transformed = {
            "services": {
                "frontend": {
                    "image": "registry.test/my-app-frontend:1.0.0-dev",
                    "ports": ["3000"],
                },
            },
        }

        payload = builder.build(metadata, transformed, connection, "test")
        frontend = payload.services[0]

        health_check = frontend.model_dump()["healthCheck"]
        assert frontend.primary is True
        assert health_check["exec"]["command"][0] == "node"

    def test_non_frontend_primary_uses_http_probe(self, builder, metadata, connection):
        transformed = {
            "services": {
                "backend": {
                    "image": "registry.test/my-app-backend:1.0.0-dev",
                    "ports": ["8000"],
                },
            },
        }

        payload = builder.build(metadata, transformed, connection, "test")
        backend = payload.services[0]

        health_check = backend.model_dump()["healthCheck"]
        assert backend.primary is True
        assert health_check["httpGet"] == {"path": "/health", "port": 8000}
        assert "exec" not in health_check

    def test_service_type_primary_uses_root_probe(self, builder, connection):
        """ENG-3901 / F-013: the service template ships a stub
        (``python -m http.server``) that returns 404 for /health. The
        K8s startup probe loops the pod into restarts. Service-type
        primary services must probe ``/`` instead — the stub answers it,
        and developers who add real /health code can override at the
        compose level."""
        metadata = {"name": "my-svc", "version": "1.0.0", "type": "service"}
        transformed = {
            "services": {
                "service": {
                    "image": "registry.test/my-svc:1.0.0",
                    "ports": ["8000"],
                },
            },
        }
        payload = builder.build(metadata, transformed, connection, "test")
        svc = payload.services[0]
        health_check = svc.model_dump()["healthCheck"]
        assert svc.primary is True
        assert health_check["httpGet"] == {
            "path": "/",
            "port": 8000,
        }, f"service-type primary must probe / not /health; got {health_check['httpGet']!r}"

    def test_tool_type_primary_probes_sse(self, builder, connection):
        """ENG-3901 / F-013 (final): tool primary probes ``/sse`` — the
        FastMCP SSE endpoint. ``/`` 404s (FastMCP doesn't mount root) and
        ``/health`` doesn't exist on the stub. The platform API rejects
        ``tcpSocket`` and ``exec`` probe shapes with opaque 500s, so
        httpGet ``/sse`` is the only viable choice today even though the
        K8s probe may time out reading the streamed response body."""
        metadata = {"name": "my-tool", "version": "1.0.0", "type": "tool"}
        transformed = {
            "services": {
                "tool": {
                    "image": "registry.test/my-tool:1.0.0",
                    "ports": ["8080"],
                },
            },
        }
        payload = builder.build(metadata, transformed, connection, "test")
        tool = payload.services[0]
        health_check = tool.model_dump()["healthCheck"]
        assert tool.primary is True
        assert health_check["httpGet"] == {"path": "/sse", "port": 8080}
        assert "tcpSocket" not in health_check
        assert "exec" not in health_check

    def test_app_type_backend_keeps_health_probe(self, builder, connection):
        """Regression guard: F-013's fix is scoped to service/tool primary.
        The app-type backend (non-primary in app extensions, ships a real
        FastAPI /health route) must still probe /health."""
        metadata = {"name": "my-app", "version": "1.0.0", "type": "app"}
        transformed = {
            "services": {
                "frontend": {
                    "image": "registry.test/my-app-frontend:1.0.0",
                    "ports": ["3000"],
                },
                "backend": {
                    "image": "registry.test/my-app-backend:1.0.0",
                    "ports": ["8000"],
                },
            },
        }
        payload = builder.build(metadata, transformed, connection, "test")
        backend = next(s for s in payload.services if s.name == "backend")
        health_check = backend.model_dump()["healthCheck"]
        assert health_check["httpGet"] == {"path": "/health", "port": 8000}


class TestHealthCheckOverride:
    """ENG-4832: per-service healthCheck override in ``kamiwaza.json``.

    Lets tool/service extensions whose primary doesn't serve ``/sse``
    (REST-only, MCP-at-/mcp, gRPC, FastMCP feature-flagged off) declare
    a probe that actually matches what the container exposes, instead
    of hitting the ``/sse``-on-every-tool default and CrashLoopBackOff.
    """

    def test_metadata_override_replaces_default_for_tool_primary(
        self, builder, connection
    ):
        """Omniparse-style REST-only tool: probe /v1/healthz, not /sse."""
        metadata = {
            "name": "my-tool",
            "version": "1.0.0",
            "type": "tool",
            "services": {
                "tool": {
                    "healthCheck": {
                        "httpGet": {"path": "/v1/healthz", "port": 8000},
                        "initialDelaySeconds": 10,
                        "periodSeconds": 10,
                    },
                },
            },
        }
        transformed = {
            "services": {
                "tool": {
                    "image": "registry.test/my-tool:1.0.0",
                    "ports": ["8000"],
                },
            },
        }
        payload = builder.build(metadata, transformed, connection, "test")
        tool = payload.services[0]
        health_check = tool.model_dump()["healthCheck"]
        assert tool.primary is True
        assert health_check["httpGet"] == {"path": "/v1/healthz", "port": 8000}
        assert health_check["initialDelaySeconds"] == 10
        assert health_check["periodSeconds"] == 10

    def test_metadata_override_wins_over_x_kamiwaza(self, builder, connection):
        """When both kamiwaza.json and compose declare a probe, metadata wins.

        Locks the precedence: catalog metadata is the single source of truth,
        compose ``x-kamiwaza`` is the legacy fallback.
        """
        metadata = {
            "name": "my-tool",
            "version": "1.0.0",
            "type": "tool",
            "services": {
                "tool": {
                    "healthCheck": {"httpGet": {"path": "/v1/healthz", "port": 8000}},
                },
            },
        }
        transformed = {
            "services": {
                "tool": {
                    "image": "registry.test/my-tool:1.0.0",
                    "ports": ["8000"],
                    "x-kamiwaza": {
                        "healthCheck": {"httpGet": {"path": "/old", "port": 8000}},
                    },
                },
            },
        }
        payload = builder.build(metadata, transformed, connection, "test")
        tool = payload.services[0]
        health_check = tool.model_dump()["healthCheck"]
        assert health_check["httpGet"] == {"path": "/v1/healthz", "port": 8000}

    def test_no_metadata_override_falls_back_to_x_kamiwaza(
        self, builder, connection
    ):
        """Existing ``x-kamiwaza.healthCheck`` path stays intact."""
        metadata = {"name": "my-tool", "version": "1.0.0", "type": "tool"}
        transformed = {
            "services": {
                "tool": {
                    "image": "registry.test/my-tool:1.0.0",
                    "ports": ["8000"],
                    "x-kamiwaza": {
                        "healthCheck": {"httpGet": {"path": "/compose", "port": 8000}},
                    },
                },
            },
        }
        payload = builder.build(metadata, transformed, connection, "test")
        tool = payload.services[0]
        health_check = tool.model_dump()["healthCheck"]
        assert health_check["httpGet"] == {"path": "/compose", "port": 8000}

    def test_no_overrides_keeps_default_sse_for_tool_primary(
        self, builder, connection
    ):
        """Regression guard: tool extensions with no override still get /sse."""
        metadata = {"name": "my-tool", "version": "1.0.0", "type": "tool"}
        transformed = {
            "services": {
                "tool": {
                    "image": "registry.test/my-tool:1.0.0",
                    "ports": ["8000"],
                },
            },
        }
        payload = builder.build(metadata, transformed, connection, "test")
        tool = payload.services[0]
        health_check = tool.model_dump()["healthCheck"]
        assert health_check["httpGet"] == {"path": "/sse", "port": 8000}

    def test_metadata_override_per_service_only_affects_named_service(
        self, builder, connection
    ):
        """Override on one service doesn't leak into siblings."""
        metadata = {
            "name": "my-app",
            "version": "1.0.0",
            "type": "app",
            "services": {
                "backend": {
                    "healthCheck": {"httpGet": {"path": "/v1/ready", "port": 8000}},
                },
            },
        }
        transformed = {
            "services": {
                "frontend": {
                    "image": "registry.test/my-app-frontend:1.0.0",
                    "ports": ["3000"],
                    "environment": ["NEXT_PUBLIC_API_URL=http://backend:8000"],
                },
                "backend": {
                    "image": "registry.test/my-app-backend:1.0.0",
                    "ports": ["8000"],
                },
            },
        }
        payload = builder.build(metadata, transformed, connection, "test")
        backend = next(s for s in payload.services if s.name == "backend")
        frontend = next(s for s in payload.services if s.name == "frontend")
        assert backend.model_dump()["healthCheck"]["httpGet"] == {
            "path": "/v1/ready",
            "port": 8000,
        }
        # Frontend keeps the Node-based default probe — untouched by the
        # backend-only override.
        assert frontend.model_dump()["healthCheck"]["exec"]["command"][0] == "node"

    def test_metadata_services_not_a_dict_is_ignored(self, builder, connection):
        """Malformed metadata.services falls back cleanly to defaults."""
        metadata = {
            "name": "my-tool",
            "version": "1.0.0",
            "type": "tool",
            "services": "not-a-dict",
        }
        transformed = {
            "services": {
                "tool": {
                    "image": "registry.test/my-tool:1.0.0",
                    "ports": ["8000"],
                },
            },
        }
        payload = builder.build(metadata, transformed, connection, "test")
        tool = payload.services[0]
        assert tool.model_dump()["healthCheck"]["httpGet"] == {
            "path": "/sse",
            "port": 8000,
        }
