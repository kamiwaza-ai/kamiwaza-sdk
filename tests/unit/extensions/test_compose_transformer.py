"""Tests for ComposeTransformer."""

import pytest

from kamiwaza_extensions.compose_transformer import ComposeTransformer


@pytest.fixture
def transformer():
    return ComposeTransformer()


@pytest.fixture
def multi_service_compose():
    """A realistic multi-service compose dict."""
    return {
        "services": {
            "frontend": {
                "build": {"context": ".", "dockerfile": "frontend/Dockerfile"},
                "ports": ["3000:3000"],
                "environment": ["NEXT_PUBLIC_API_URL=http://backend:8000"],
                "networks": ["default"],
            },
            "backend": {
                "build": "./backend",
                "ports": ["8000:8000"],
                "volumes": [
                    "./data:/app/data",
                    "backend_data:/app/persist",
                ],
                "environment": {
                    "OPENAI_BASE_URL": "${KAMIWAZA_ENDPOINT:-http://host.docker.internal:8080}",
                },
                "extra_hosts": ["host.docker.internal:host-gateway"],
                "container_name": "my-backend",
            },
            "db": {
                "image": "postgres:15",
                "ports": ["5432:5432"],
                "volumes": ["pgdata:/var/lib/postgresql/data"],
                "environment": {"POSTGRES_PASSWORD": "secret"},
            },
        },
        "volumes": {
            "backend_data": None,
            "pgdata": None,
        },
        "networks": {
            "default": None,
        },
    }


class TestStripHostPorts:
    def test_strips_host_port(self, transformer):
        compose = {"services": {"web": {"ports": ["3000:3000"]}}}
        result = transformer.transform(compose, "test", "v1", "reg")
        assert result["services"]["web"]["ports"] == ["3000"]

    def test_strips_with_protocol(self, transformer):
        compose = {"services": {"web": {"ports": ["8080:3000/tcp"]}}}
        result = transformer.transform(compose, "test", "v1", "reg")
        assert result["services"]["web"]["ports"] == ["3000/tcp"]

    def test_container_only_port_unchanged(self, transformer):
        compose = {"services": {"web": {"ports": ["8000"]}}}
        result = transformer.transform(compose, "test", "v1", "reg")
        assert result["services"]["web"]["ports"] == ["8000"]


class TestStripBindMounts:
    def test_strips_relative_bind_mount(self, transformer):
        compose = {"services": {"web": {"volumes": ["./data:/app/data", "named:/app/persist"]}}}
        result = transformer.transform(compose, "test", "v1", "reg")
        assert result["services"]["web"]["volumes"] == ["named:/app/persist"]

    def test_strips_absolute_bind_mount(self, transformer):
        compose = {"services": {"web": {"volumes": ["/host/path:/container"]}}}
        result = transformer.transform(compose, "test", "v1", "reg")
        assert "volumes" not in result["services"]["web"]

    def test_keeps_named_volumes(self, transformer):
        compose = {"services": {"web": {"volumes": ["data:/app/data"]}}}
        result = transformer.transform(compose, "test", "v1", "reg")
        assert result["services"]["web"]["volumes"] == ["data:/app/data"]


class TestBuildContextRemoval:
    def test_removes_build_adds_image(self, transformer):
        compose = {"services": {"api": {"build": "./backend"}}}
        result = transformer.transform(compose, "my-app", "1.0.0-dev", "registry.test")
        svc = result["services"]["api"]
        assert "build" not in svc
        assert svc["image"] == "registry.test/my-app-api:1.0.0-dev"

    def test_preserves_declared_namespace_rewrites_only_tag(self, transformer):
        # When a service has both `build:` and `image:`, the declared
        # namespace is canonical — only the tag is rewritten to the
        # publish's revision_tag. Without this, extensions whose GHCR
        # path doesn't follow the legacy {ext}-{svc} convention (e.g.
        # omniparse, which ships at `images/omniparse`) silently get
        # the wrong namespace in the catalog. (ENG-4909.)
        compose = {
            "services": {
                "api": {
                    "build": "./backend",
                    "image": "kamiwazaai/my-app-api:old-tag",
                }
            }
        }
        result = transformer.transform(compose, "my-app", "1.0.0-dev", "registry.test")
        assert result["services"]["api"]["image"] == (
            "kamiwazaai/my-app-api:1.0.0-dev"
        )

    def test_transform_preserves_divergent_namespace(self, transformer):
        # The omniparse-style case: declared image namespace
        # (`images/omniparse`) does not follow the {ext-name}-{svc-name}
        # convention (`my-app-api`). The declared form wins.
        compose = {
            "services": {
                "omniparse-server": {
                    "build": "./tool-omniparse",
                    "image": "ghcr.io/kamiwaza-internal/foo/images/omniparse:2.0.14",
                }
            }
        }
        result = transformer.transform(
            compose, "tool-omniparse", "2.0.14-dev", "ghcr.io/kamiwaza-internal/foo/images",
        )
        assert result["services"]["omniparse-server"]["image"] == (
            "ghcr.io/kamiwaza-internal/foo/images/omniparse:2.0.14-dev"
        )

    def test_dict_build_config(self, transformer):
        compose = {
            "services": {
                "web": {
                    "build": {"context": ".", "dockerfile": "frontend/Dockerfile"}
                }
            }
        }
        result = transformer.transform(compose, "my-app", "v1", "reg")
        assert "build" not in result["services"]["web"]
        assert result["services"]["web"]["image"] == "reg/my-app-web:v1"


class TestExternalImages:
    def test_postgres_preserved(self, transformer):
        compose = {"services": {"db": {"image": "postgres:15"}}}
        result = transformer.transform(compose, "my-app", "v1", "reg")
        assert result["services"]["db"]["image"] == "postgres:15"

    def test_redis_preserved(self, transformer):
        compose = {"services": {"cache": {"image": "redis:7-alpine"}}}
        result = transformer.transform(compose, "my-app", "v1", "reg")
        assert result["services"]["cache"]["image"] == "redis:7-alpine"


class TestNonBuildableInternalImages:
    """Pattern C — services with ``image:`` but no ``build:`` whose image
    name happens to contain the extension name (e.g. a helper image
    published from a separate repo). publish does not own these images;
    their declared tag must pass through verbatim. (ENG-3591: prior to the
    fix, the override tag was applied here too, pointing the catalog at a
    tag publish never built or pushed.)"""

    def test_internal_named_helper_preserved(self, transformer):
        compose = {
            "services": {
                "helper": {"image": "kamiwazaai/my-app-helper:0.5.0"},
            },
        }
        result = transformer.transform(
            compose, "my-app", "1.0.0-dev-abc1234", "kamiwazaai",
        )
        # Tag preserved verbatim despite the SHA-pinned revision_tag.
        assert (
            result["services"]["helper"]["image"]
            == "kamiwazaai/my-app-helper:0.5.0"
        )

    def test_mixed_buildable_and_pattern_c(self, transformer):
        compose = {
            "services": {
                "backend": {"build": "./backend"},
                "helper": {"image": "kamiwazaai/my-app-helper:0.5.0"},
                "db": {"image": "postgres:15"},
            },
        }
        result = transformer.transform(
            compose, "my-app", "1.0.0-dev-abc1234", "kamiwazaai",
        )
        # Buildable: rewritten with the revision tag.
        assert (
            result["services"]["backend"]["image"]
            == "kamiwazaai/my-app-backend:1.0.0-dev-abc1234"
        )
        # Pattern C: preserved verbatim.
        assert (
            result["services"]["helper"]["image"]
            == "kamiwazaai/my-app-helper:0.5.0"
        )
        # External: preserved verbatim.
        assert result["services"]["db"]["image"] == "postgres:15"


class TestResourceLimits:
    def test_adds_default_limits(self, transformer):
        compose = {"services": {"api": {"image": "my-app/api:1"}}}
        result = transformer.transform(compose, "my-app", "v1", "reg")
        limits = result["services"]["api"]["deploy"]["resources"]["limits"]
        assert limits["cpus"] == "1.0"
        assert limits["memory"] == "1G"

    def test_preserves_existing_limits(self, transformer):
        compose = {
            "services": {
                "api": {
                    "image": "my-app/api:1",
                    "deploy": {"resources": {"limits": {"cpus": "2.0", "memory": "4G"}}},
                }
            }
        }
        result = transformer.transform(compose, "my-app", "v1", "reg")
        limits = result["services"]["api"]["deploy"]["resources"]["limits"]
        assert limits["cpus"] == "2.0"

    def test_postgres_gets_smaller_limits(self, transformer):
        compose = {"services": {"db": {"image": "postgres:15"}}}
        result = transformer.transform(compose, "my-app", "v1", "reg")
        limits = result["services"]["db"]["deploy"]["resources"]["limits"]
        assert limits["cpus"] == "0.5"
        assert limits["memory"] == "512M"


class TestCleanup:
    def test_removes_extra_hosts(self, transformer):
        compose = {"services": {"api": {"extra_hosts": ["host.docker.internal:host-gateway"]}}}
        result = transformer.transform(compose, "test", "v1", "reg")
        assert "extra_hosts" not in result["services"]["api"]

    def test_removes_container_name(self, transformer):
        compose = {"services": {"api": {"container_name": "my-api"}}}
        result = transformer.transform(compose, "test", "v1", "reg")
        assert "container_name" not in result["services"]["api"]

    def test_removes_networks(self, transformer):
        compose = {"services": {"api": {"networks": ["default"]}}, "networks": {"default": None}}
        result = transformer.transform(compose, "test", "v1", "reg")
        assert "networks" not in result["services"]["api"]
        assert "networks" not in result

    def test_preserves_x_kamiwaza_overrides(self, transformer):
        compose = {
            "services": {
                "postgres": {
                    "image": "postgres:15",
                    "x-kamiwaza": {
                        "containerSecurityContext": {"runAsNonRoot": False},
                        "healthCheck": {"tcpSocket": {"port": 5432}},
                    },
                }
            }
        }
        result = transformer.transform(compose, "test", "v1", "reg")
        assert result["services"]["postgres"]["x-kamiwaza"] == {
            "containerSecurityContext": {"runAsNonRoot": False},
            "healthCheck": {"tcpSocket": {"port": 5432}},
        }


class TestFullTransform:
    def test_multi_service(self, transformer, multi_service_compose):
        result = transformer.transform(
            multi_service_compose, "my-app", "1.0.0-dev-abc.123", "registry.test"
        )
        # Frontend
        fe = result["services"]["frontend"]
        assert "build" not in fe
        assert fe["image"] == "registry.test/my-app-frontend:1.0.0-dev-abc.123"
        assert fe["ports"] == ["3000"]
        assert "networks" not in fe

        # Backend
        be = result["services"]["backend"]
        assert "build" not in be
        assert "extra_hosts" not in be
        assert "container_name" not in be
        assert be["volumes"] == ["backend_data:/app/persist"]  # bind mount stripped

        # DB (external)
        db = result["services"]["db"]
        assert db["image"] == "postgres:15"

        # Top-level
        assert "networks" not in result
        assert "volumes" in result  # named volumes preserved

    def test_does_not_mutate_input(self, transformer):
        compose = {"services": {"api": {"build": ".", "ports": ["8000:8000"]}}}
        original_ports = list(compose["services"]["api"]["ports"])
        transformer.transform(compose, "test", "v1", "reg")
        assert compose["services"]["api"]["ports"] == original_ports
        assert "build" in compose["services"]["api"]


class TestResolveEnvPlaceholders:
    """``resolve_env_placeholders`` collapses ``${VAR:-default}`` env
    placeholders to their default, drops unresolvable forms, and drops
    ``KAMIWAZA_*`` keys (so the operator's ConfigMap envFrom wins)."""

    def test_resolves_default_substitution_dict_form(self, transformer):
        compose = {
            "services": {
                "frontend": {
                    "environment": {
                        "BACKEND_URL": "${BACKEND_URL:-http://backend:8000}",
                    },
                },
            },
        }
        result = transformer.resolve_env_placeholders(compose)
        assert result["services"]["frontend"]["environment"] == {
            "BACKEND_URL": "http://backend:8000",
        }

    def test_resolves_default_substitution_list_form(self, transformer):
        compose = {
            "services": {
                "frontend": {
                    "environment": [
                        "BACKEND_URL=${BACKEND_URL:-http://backend:8000}",
                        "PLAIN_VAR=plain-value",
                    ],
                },
            },
        }
        result = transformer.resolve_env_placeholders(compose)
        assert result["services"]["frontend"]["environment"] == [
            "BACKEND_URL=http://backend:8000",
            "PLAIN_VAR=plain-value",
        ]

    def test_drops_kamiwaza_platform_vars(self, transformer):
        """``KAMIWAZA_*`` vars are platform-injected via ConfigMap
        envFrom; an explicit env entry would shadow the cluster-internal
        value with the laptop-only default."""
        compose = {
            "services": {
                "backend": {
                    "environment": {
                        "KAMIWAZA_API_URL": "${KAMIWAZA_API_URL:-http://host.docker.internal:7777/api}",
                        "BACKEND_URL": "${BACKEND_URL:-http://backend:8000}",
                    },
                },
            },
        }
        result = transformer.resolve_env_placeholders(compose)
        # KAMIWAZA_* dropped; BACKEND_URL resolved to default.
        assert result["services"]["backend"]["environment"] == {
            "BACKEND_URL": "http://backend:8000",
        }

    def test_drops_unresolvable_substitutions(self, transformer):
        """``${VAR}`` without a default and ``${VAR:?error}`` (required)
        have no safe value to ship — drop them."""
        compose = {
            "services": {
                "backend": {
                    "environment": {
                        "OPENAI_API_KEY": "${OPENAI_API_KEY}",
                        "REQUIRED_VAR": "${REQUIRED_VAR:?missing}",
                        "PLAIN": "kept",
                    },
                },
            },
        }
        result = transformer.resolve_env_placeholders(compose)
        assert result["services"]["backend"]["environment"] == {"PLAIN": "kept"}

    def test_alternate_default_form_dash_only(self, transformer):
        """Compose accepts both ``${VAR:-default}`` (unset OR empty)
        and ``${VAR-default}`` (unset only). Both collapse to default."""
        compose = {
            "services": {
                "backend": {
                    "environment": {"X": "${UNSET-fallback}"},
                },
            },
        }
        result = transformer.resolve_env_placeholders(compose)
        assert result["services"]["backend"]["environment"] == {"X": "fallback"}

    def test_plain_values_pass_through(self, transformer):
        compose = {
            "services": {
                "backend": {
                    "environment": {"FOO": "bar", "PORT": "8000"},
                },
            },
        }
        result = transformer.resolve_env_placeholders(compose)
        assert result["services"]["backend"]["environment"] == {
            "FOO": "bar",
            "PORT": "8000",
        }

    def test_does_not_mutate_input(self, transformer):
        compose = {
            "services": {
                "backend": {
                    "environment": {
                        "BACKEND_URL": "${BACKEND_URL:-http://backend:8000}",
                        "REQUIRED_VAR": "${REQUIRED_VAR:?missing}",
                    },
                },
            },
        }
        original = {
            "BACKEND_URL": "${BACKEND_URL:-http://backend:8000}",
            "REQUIRED_VAR": "${REQUIRED_VAR:?missing}",
        }
        transformer.resolve_env_placeholders(compose)
        assert compose["services"]["backend"]["environment"] == original


class TestTransformPreservesEnvPlaceholders:
    """``transform()`` leaves env-value ``${...}`` placeholders verbatim
    so callers whose destination performs its own substitution (e.g. the
    platform installer reading a catalog template) receive an
    unresolved compose."""

    def test_required_placeholder_preserved(self, transformer):
        """``${VAR:?error}`` survives verbatim so a downstream consumer
        sees the required-var marker."""
        compose = {
            "services": {
                "neo4j": {
                    "image": "neo4j:5",
                    "environment": {
                        "NEO4J_AUTH": "neo4j/${NEO4J_PASSWORD:?NEO4J_PASSWORD must be set}",
                        "KZ_NEO4J_PASSWORD": "${NEO4J_PASSWORD:?NEO4J_PASSWORD must be set}",
                    },
                },
            },
        }
        result = transformer.transform(compose, "test", "v1", "reg")
        assert result["services"]["neo4j"]["environment"] == {
            "NEO4J_AUTH": "neo4j/${NEO4J_PASSWORD:?NEO4J_PASSWORD must be set}",
            "KZ_NEO4J_PASSWORD": "${NEO4J_PASSWORD:?NEO4J_PASSWORD must be set}",
        }

    def test_default_placeholder_preserved(self, transformer):
        """``${VAR:-default}`` survives verbatim so a downstream consumer
        decides whether to substitute or fall through to the default."""
        compose = {
            "services": {
                "graphiti": {
                    "image": "graphiti:0.28",
                    "environment": {
                        "KAMIWAZA_ENDPOINT": "${KAMIWAZA_ENDPOINT:-http://host.docker.internal:8080}",
                        "OPENAI_API_KEY": "${OPENAI_API_KEY:-not-needed-kamiwaza}",
                        "NEO4J_HOST": "${NEO4J_HOST:-neo4j}",
                    },
                },
            },
        }
        result = transformer.transform(compose, "test", "v1", "reg")
        assert result["services"]["graphiti"]["environment"] == {
            "KAMIWAZA_ENDPOINT": "${KAMIWAZA_ENDPOINT:-http://host.docker.internal:8080}",
            "OPENAI_API_KEY": "${OPENAI_API_KEY:-not-needed-kamiwaza}",
            "NEO4J_HOST": "${NEO4J_HOST:-neo4j}",
        }

    def test_bare_placeholder_preserved(self, transformer):
        """``${VAR}`` with no default survives verbatim — extensions use
        this when the caller must supply a value with no fallback."""
        compose = {
            "services": {
                "backend": {
                    "image": "backend:1",
                    "environment": {"OPENAI_BASE_URL": "${OPENAI_BASE_URL}"},
                },
            },
        }
        result = transformer.transform(compose, "test", "v1", "reg")
        assert result["services"]["backend"]["environment"] == {
            "OPENAI_BASE_URL": "${OPENAI_BASE_URL}",
        }

    def test_list_form_placeholders_preserved(self, transformer):
        """Compose env can be ``KEY=value`` list form too. Placeholders
        in list-form entries must also survive verbatim."""
        compose = {
            "services": {
                "backend": {
                    "image": "backend:1",
                    "environment": [
                        "NEO4J_PASSWORD=${NEO4J_PASSWORD:?required}",
                        "BACKEND_URL=${BACKEND_URL:-http://backend:8000}",
                        "OPENAI_API_KEY=${OPENAI_API_KEY}",
                        "PLAIN=kept",
                    ],
                },
            },
        }
        result = transformer.transform(compose, "test", "v1", "reg")
        assert result["services"]["backend"]["environment"] == [
            "NEO4J_PASSWORD=${NEO4J_PASSWORD:?required}",
            "BACKEND_URL=${BACKEND_URL:-http://backend:8000}",
            "OPENAI_API_KEY=${OPENAI_API_KEY}",
            "PLAIN=kept",
        ]


class TestDetectServiceUrlRewrites:
    """Cross-service URL rewriting: when one service's env references
    a sibling by compose short name (``http://backend:8000``), emit a
    rewrite map for the operator to translate to the
    deployment-prefixed K8s service name (``http://my-app-backend:8000``).
    Without this, bare ``backend`` doesn't resolve in K8s DNS — the
    Next.js API proxy fails with ENOTFOUND."""

    def test_rewrites_sibling_url(self):
        from kamiwaza_extensions.compose_transformer import detect_service_url_rewrites

        services = {
            "frontend": {
                "environment": {"BACKEND_URL": "http://backend:8000"},
            },
            "backend": {
                "environment": {"PORT": "8000"},
            },
        }
        rewrites = detect_service_url_rewrites(services, "my-app-dev-abc")
        assert rewrites == {
            "frontend": {
                "BACKEND_URL": {
                    "from": "http://backend:8000",
                    "to": "http://my-app-dev-abc-backend:8000",
                }
            }
        }

    def test_handles_https_and_path(self):
        from kamiwaza_extensions.compose_transformer import detect_service_url_rewrites

        services = {
            "frontend": {
                "environment": {"API": "https://api/v1/health"},
            },
            "api": {"environment": {}},
        }
        rewrites = detect_service_url_rewrites(services, "ext")
        assert rewrites["frontend"]["API"]["to"] == "https://ext-api/v1/health"

    def test_handles_list_form_environment(self):
        from kamiwaza_extensions.compose_transformer import detect_service_url_rewrites

        services = {
            "frontend": {
                "environment": ["BACKEND_URL=http://backend:8000"],
            },
            "backend": {"environment": []},
        }
        rewrites = detect_service_url_rewrites(services, "ext")
        assert rewrites == {
            "frontend": {
                "BACKEND_URL": {
                    "from": "http://backend:8000",
                    "to": "http://ext-backend:8000",
                }
            }
        }

    def test_ignores_self_reference(self):
        from kamiwaza_extensions.compose_transformer import detect_service_url_rewrites

        services = {
            "backend": {
                "environment": {"SELF_URL": "http://backend:8000"},
            },
        }
        rewrites = detect_service_url_rewrites(services, "ext")
        assert rewrites == {}

    def test_ignores_external_hostnames(self):
        from kamiwaza_extensions.compose_transformer import detect_service_url_rewrites

        services = {
            "frontend": {
                "environment": {"EXT": "https://api.openai.com/v1"},
            },
            "backend": {"environment": {}},
        }
        rewrites = detect_service_url_rewrites(services, "ext")
        assert rewrites == {}

    def test_word_boundary_avoids_prefix_match(self):
        """``http://backend2`` must NOT match a sibling named ``backend``."""
        from kamiwaza_extensions.compose_transformer import detect_service_url_rewrites

        services = {
            "frontend": {
                "environment": {"URL": "http://backend2:8000"},
            },
            "backend": {"environment": {}},
        }
        rewrites = detect_service_url_rewrites(services, "ext")
        assert rewrites == {}

    def test_subdomain_does_not_match_sibling(self):
        """Iter-8 review finding: a sibling named ``api`` must NOT
        hijack ``https://api.openai.com/v1`` (the ``.`` after ``api`` is
        a subdomain separator, not a host terminator). Earlier ``\\b``
        boundary treated ``.`` as a word boundary and falsely rewrote
        external URLs sharing a leading subdomain with sibling names
        like ``api``, ``auth``, ``app``, ``web``."""
        from kamiwaza_extensions.compose_transformer import detect_service_url_rewrites

        services = {
            "api": {"environment": {}},
            "auth": {"environment": {}},
            "frontend": {
                "environment": {
                    "OPENAI_BASE_URL": "https://api.openai.com/v1",
                    "AUTH0_URL": "https://auth.example.com/oauth",
                    # Real sibling reference (no subdomain) — must
                    # still be rewritten so we don't regress the
                    # primary use case.
                    "API_URL": "http://api:8000",
                },
            },
        }
        rewrites = detect_service_url_rewrites(services, "ext")
        # Only the bare-host sibling reference gets rewritten; the
        # external subdomain URLs pass through untouched.
        assert rewrites == {
            "frontend": {
                "API_URL": {
                    "from": "http://api:8000",
                    "to": "http://ext-api:8000",
                }
            }
        }

    def test_empty_for_no_environment(self):
        from kamiwaza_extensions.compose_transformer import detect_service_url_rewrites

        services = {"backend": {}, "frontend": {}}
        assert detect_service_url_rewrites(services, "ext") == {}
