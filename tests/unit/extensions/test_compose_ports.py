"""Tests for the shared compose-port parser."""

import pytest

from kamiwaza_extensions.compose_ports import extract_container_port


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
