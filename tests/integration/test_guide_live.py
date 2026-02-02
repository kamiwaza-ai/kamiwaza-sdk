"""Integration tests for TS7 GUIDE endpoints.

Tests cover:
- TS7.001: GET /guide/ - List all model guides
- TS7.002: POST /guide/import - Import model guides
- TS7.003: POST /guide/refresh - Refresh model guides
"""
from __future__ import annotations

import pytest

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.guide import ModelGuide

pytestmark = [pytest.mark.integration, pytest.mark.withoutresponses]


class TestGuideReadOperations:
    """Tests for read-only guide operations."""

    def test_list_guides(self, live_kamiwaza_client) -> None:
        """TS7.001: GET /guide/ - List all model guides."""
        guides = live_kamiwaza_client.models.list_guides()
        assert isinstance(guides, list)
        # Guides may be empty initially but should return a list
        for guide in guides:
            assert isinstance(guide, ModelGuide)
            assert guide.base_model_id is not None
            assert guide.name is not None
            assert guide.producer is not None

    def test_list_guides_structure(self, live_kamiwaza_client) -> None:
        """TS7.001: Verify guide structure has expected fields."""
        guides = live_kamiwaza_client.models.list_guides()

        if not guides:
            pytest.skip("No guides available to verify structure")

        guide = guides[0]
        # Check required fields
        assert hasattr(guide, "base_model_id")
        assert hasattr(guide, "name")
        assert hasattr(guide, "producer")
        assert hasattr(guide, "context_length")
        assert hasattr(guide, "use_case")
        assert hasattr(guide, "size_category")
        assert hasattr(guide, "quality_overall")
        assert hasattr(guide, "variants")

        # Check variants if present
        if guide.variants:
            variant = guide.variants[0]
            assert hasattr(variant, "platform")
            assert hasattr(variant, "variant_repo")
            assert hasattr(variant, "variant_type")
            assert hasattr(variant, "minimum_vram")


class TestGuideImportOperations:
    """Tests for guide import operations."""

    def test_import_guides(self, live_kamiwaza_client) -> None:
        """TS7.002: POST /guide/import - Import model guides."""
        try:
            result = live_kamiwaza_client.models.import_guides(replace=False)
            # Import should return some acknowledgment
            assert result is not None
            # Result is typically a dict with status information
            if isinstance(result, dict):
                # May contain count of imported guides or status
                pass
        except APIError as exc:
            if exc.status_code in (403, 401):
                pytest.skip("Insufficient permissions for guide import")
            if exc.status_code == 404:
                pytest.skip("Guide import endpoint not available")
            raise

    def test_import_guides_with_replace(self, live_kamiwaza_client) -> None:
        """TS7.002: POST /guide/import with replace=True."""
        try:
            result = live_kamiwaza_client.models.import_guides(replace=True)
            assert result is not None
        except APIError as exc:
            if exc.status_code in (403, 401):
                pytest.skip("Insufficient permissions for guide import")
            if exc.status_code == 404:
                pytest.skip("Guide import endpoint not available")
            raise


class TestGuideRefreshOperations:
    """Tests for guide refresh operations."""

    def test_refresh_guides(self, live_kamiwaza_client) -> None:
        """TS7.003: POST /guide/refresh - Refresh model guides."""
        try:
            result = live_kamiwaza_client.models.refresh_guides()
            # Refresh should return some acknowledgment
            assert result is not None
        except APIError as exc:
            if exc.status_code in (403, 401):
                pytest.skip("Insufficient permissions for guide refresh")
            if exc.status_code == 404:
                pytest.skip("Guide refresh endpoint not available")
            raise


class TestGuideHelpers:
    """Tests for guide helper methods."""

    def test_normalized_use_cases(self, live_kamiwaza_client) -> None:
        """Test that use_case normalization works correctly."""
        guides = live_kamiwaza_client.models.list_guides()

        if not guides:
            pytest.skip("No guides available to test normalization")

        guide = guides[0]
        normalized = guide.normalized_use_cases()
        assert isinstance(normalized, list)
        # All items should be lowercase strings
        for item in normalized:
            assert isinstance(item, str)
            assert item == item.lower()


class TestModelAutoSelector:
    """Tests for the ModelAutoSelector helper."""

    def test_auto_selector_creation(self, live_kamiwaza_client) -> None:
        """Test that auto_selector can be instantiated."""
        selector = live_kamiwaza_client.models.auto_selector()
        assert selector is not None
        # Selector should have access to guide data
        # Note: Detailed selector tests depend on implementation
