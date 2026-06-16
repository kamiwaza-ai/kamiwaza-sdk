"""Tests for the shared compose-port parser."""

import pytest

from kamiwaza_extensions.compose_ports import (
    default_service_port_name,
    extract_container_port,
)


@pytest.mark.unit
class TestExtractContainerPort:
    def test_bare_short_form(self):
        assert extract_container_port("3000") == 3000

    def test_host_mapped_short_form(self):
        assert extract_container_port("8080:3000") == 3000

    def test_protocol_suffix_stripped(self):
        assert extract_container_port("53/udp") == 53
        assert extract_container_port("8080:3000/tcp") == 3000

    def test_long_form_dict(self):
        assert extract_container_port({"target": 19530, "name": "grpc"}) == 19530

    def test_long_form_string_target_coerced(self):
        assert extract_container_port({"target": "19530"}) == 19530

    def test_long_form_missing_target(self):
        assert extract_container_port({"name": "grpc"}) is None

    def test_long_form_invalid_target(self):
        assert extract_container_port({"target": "not-a-number"}) is None

    def test_malformed_short_form(self):
        assert extract_container_port("not-a-port") is None

    def test_bare_range_returns_lower_bound(self):
        """Compose-spec ranges: lower bound is the representative port."""
        assert extract_container_port("3000-3005") == 3000

    def test_mapped_range_returns_container_lower_bound(self):
        assert extract_container_port("9090-9091:3000-3001") == 3000


@pytest.mark.unit
class TestDefaultServicePortName:
    """The Istio port-name heuristic shared with platform core."""

    @pytest.mark.parametrize(
        "port,expected",
        [
            (5432, "tcp-postgres"),
            (3306, "tcp-mysql"),
            (6379, "tcp-redis"),
            (27017, "tcp-mongo"),
            (5672, "tcp-amqp"),
            (9092, "tcp-kafka"),
            (2379, "tcp-etcd"),
            (2380, "tcp-etcd-peer"),
            (19530, "tcp-milvus"),
        ],
    )
    def test_known_tcp_backends_win_over_primary(self, port, expected):
        # Known non-HTTP backends must map to their tcp-* name even when they
        # are the primary (first) port — the case that broke postgres/milvus on
        # istio. is_primary must not promote them to "http".
        assert default_service_port_name(port, is_primary=True) == expected
        assert default_service_port_name(port, is_primary=False) == expected

    @pytest.mark.parametrize(
        "port", [80, 443, 3000, 5000, 8000, 8080, 8443, 9090, 9200]
    )
    def test_known_http_ports_are_http(self, port):
        # 9200 is Elasticsearch/OpenSearch's HTTP REST port — it must stay HTTP
        # so istio keeps applying the HTTP codec (the binary transport is 9300).
        assert default_service_port_name(port, is_primary=False) == "http"

    def test_unknown_primary_defaults_to_http(self):
        assert default_service_port_name(7000, is_primary=True) == "http"

    def test_unknown_non_primary_is_opaque_tcp(self):
        assert default_service_port_name(7000, is_primary=False) == "tcp-port-7000"

    def test_generated_name_within_k8s_15_char_limit(self):
        # K8s Service port names are capped at 15 chars; tcp-port-<max> = 14.
        assert len(default_service_port_name(65535, is_primary=False)) <= 15
