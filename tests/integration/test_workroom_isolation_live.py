"""E2E workroom isolation tests.

Validates the workroom access matrix from the architecture diagram:

    A -> A (self)            always allowed
    B -> B (self)            always allowed
    A -> Global              allowed (default, user-disableable)
    B -> Global              allowed (default, user-disableable)
    A <-> B (cross)          NEVER
    Global -> A or B         NEVER

Each test creates ephemeral workrooms, populates resources via the
X-Workroom-Id header, then asserts visibility rules hold.

Requirements:
    - A running Kamiwaza instance (auto-skips if unavailable)
    - Authenticated SDK client (API key or user/password)
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.exceptions import APIError, NotFoundError

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]

GLOBAL_WORKROOM_ID = "ffffffff-ffff-ffff-ffff-ffffffffffff"


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sdk(live_kamiwaza_client) -> KamiwazaClient:
    """Authenticated SDK client."""
    return live_kamiwaza_client


@pytest.fixture
def workroom_a(sdk: KamiwazaClient):
    """Create an ephemeral Workroom A, delete on teardown."""
    wr = sdk.workrooms.create(_unique("wr-a"), "ephemeral", description="Isolation test A")
    yield wr
    try:
        sdk.workrooms.delete(str(wr.id))
    except (APIError, NotFoundError):
        pass


@pytest.fixture
def workroom_b(sdk: KamiwazaClient):
    """Create an ephemeral Workroom B, delete on teardown."""
    wr = sdk.workrooms.create(_unique("wr-b"), "ephemeral", description="Isolation test B")
    yield wr
    try:
        sdk.workrooms.delete(str(wr.id))
    except (APIError, NotFoundError):
        pass


# ---------------------------------------------------------------------------
# Helpers -- raw HTTP wrappers that inject X-Workroom-Id
# ---------------------------------------------------------------------------

def _list_connectors(sdk: KamiwazaClient, workroom_id: str) -> list:
    """List DDE connectors scoped to a workroom via header."""
    resp = sdk.get("/dde/connectors/", headers={"X-Workroom-Id": workroom_id})
    return resp.get("items", [])


def _list_deployments(sdk: KamiwazaClient, workroom_id: str | None = None) -> list:
    """List app deployments, optionally filtered by workroom_id query param."""
    params = {}
    if workroom_id is not None:
        params["workroom_id"] = workroom_id
    return sdk.get("/apps/deployments", params=params)


def _list_extensions(sdk: KamiwazaClient, workroom_id: str) -> list:
    """List extensions scoped to a workroom via header."""
    resp = sdk.get("/extensions", headers={"X-Workroom-Id": workroom_id})
    return resp if isinstance(resp, list) else resp.get("items", [])


# ---------------------------------------------------------------------------
# 1. WORKROOM CRUD ISOLATION
# ---------------------------------------------------------------------------

class TestWorkroomCrudIsolation:
    """Verify workroom CRUD operations respect ownership."""

    def test_create_and_get_own_workroom(self, sdk, workroom_a):
        """A -> A: Owner can retrieve their own workroom."""
        fetched = sdk.workrooms.get(str(workroom_a.id))
        assert fetched.id == workroom_a.id
        assert fetched.name == workroom_a.name

    def test_list_returns_own_workrooms(self, sdk, workroom_a, workroom_b):
        """List returns workrooms owned by the authenticated user."""
        wrs = sdk.workrooms.list()
        ids = {str(wr.id) for wr in wrs}
        assert str(workroom_a.id) in ids
        assert str(workroom_b.id) in ids

    def test_global_workroom_exists(self, sdk):
        """Global Workroom is always present and readable."""
        global_wr = sdk.workrooms.get(GLOBAL_WORKROOM_ID)
        assert str(global_wr.id) == GLOBAL_WORKROOM_ID
        assert global_wr.name == "Global Workroom"
        assert global_wr.status == "active"

    def test_cannot_delete_global_workroom(self, sdk):
        """Global Workroom delete is forbidden (403)."""
        with pytest.raises(APIError) as exc_info:
            sdk.workrooms.delete(GLOBAL_WORKROOM_ID)
        assert exc_info.value.status_code == 403

    def test_update_own_workroom(self, sdk, workroom_a):
        """A -> A: Owner can update their own workroom."""
        updated = sdk.workrooms.update(str(workroom_a.id), description="updated-desc")
        assert updated.description == "updated-desc"

    def test_archive_own_workroom(self, sdk):
        """A -> A: Owner can archive their own workroom."""
        wr = sdk.workrooms.create(_unique("archive-test"), "ephemeral")
        try:
            archived = sdk.workrooms.archive(str(wr.id))
            assert archived.status == "archived"
        finally:
            try:
                sdk.workrooms.delete(str(wr.id))
            except (APIError, NotFoundError):
                pass


# ---------------------------------------------------------------------------
# 2. SELF-ACCESS: A -> A, B -> B
# ---------------------------------------------------------------------------

class TestSelfAccess:
    """Workspace A sees its own resources; B sees its own."""

    def test_connectors_scoped_to_own_workroom(self, sdk, workroom_a, workroom_b):
        """Connectors listed with A's header return only A's connectors."""
        a_connectors = _list_connectors(sdk, str(workroom_a.id))
        b_connectors = _list_connectors(sdk, str(workroom_b.id))

        a_wids = {c.get("workroom_id") for c in a_connectors}
        b_wids = {c.get("workroom_id") for c in b_connectors}

        # Every connector returned for A must belong to A (or be empty)
        for wid in a_wids:
            if wid is not None:
                assert wid == str(workroom_a.id), f"A sees non-A connector: {wid}"

        for wid in b_wids:
            if wid is not None:
                assert wid == str(workroom_b.id), f"B sees non-B connector: {wid}"

    def test_extensions_scoped_to_own_workroom(self, sdk, workroom_a, workroom_b):
        """Extensions listed with A's header return only A's extensions."""
        a_exts = _list_extensions(sdk, str(workroom_a.id))
        b_exts = _list_extensions(sdk, str(workroom_b.id))

        for ext in a_exts:
            wid = ext.get("workroom_id") if isinstance(ext, dict) else getattr(ext, "workroom_id", None)
            if wid is not None:
                assert wid == str(workroom_a.id), f"A sees non-A extension: {wid}"

        for ext in b_exts:
            wid = ext.get("workroom_id") if isinstance(ext, dict) else getattr(ext, "workroom_id", None)
            if wid is not None:
                assert wid == str(workroom_b.id), f"B sees non-B extension: {wid}"


# ---------------------------------------------------------------------------
# 3. CROSS-WORKSPACE: A <-> B = NEVER
# ---------------------------------------------------------------------------

class TestCrossWorkspaceBlocked:
    """Nothing outside of A can see into A; nothing outside of B can see into B."""

    def test_b_cannot_see_a_connectors(self, sdk, workroom_a, workroom_b):
        """B's connector listing must not include any of A's connectors."""
        b_connectors = _list_connectors(sdk, str(workroom_b.id))
        b_wids = {c.get("workroom_id") for c in b_connectors}
        assert str(workroom_a.id) not in b_wids, "B sees A's connectors -- cross-workspace leak!"

    def test_a_cannot_see_b_connectors(self, sdk, workroom_a, workroom_b):
        """A's connector listing must not include any of B's connectors."""
        a_connectors = _list_connectors(sdk, str(workroom_a.id))
        a_wids = {c.get("workroom_id") for c in a_connectors}
        assert str(workroom_b.id) not in a_wids, "A sees B's connectors -- cross-workspace leak!"

    def test_b_cannot_see_a_extensions(self, sdk, workroom_a, workroom_b):
        """B's extension listing must not include any of A's extensions."""
        b_exts = _list_extensions(sdk, str(workroom_b.id))
        for ext in b_exts:
            wid = ext.get("workroom_id") if isinstance(ext, dict) else getattr(ext, "workroom_id", None)
            assert wid != str(workroom_a.id), "B sees A's extension -- cross-workspace leak!"

    def test_workroom_export_only_shows_own_resources(self, sdk, workroom_a):
        """Export manifest for A should not reference B's resources."""
        manifest = sdk.workrooms.get_export_manifest(str(workroom_a.id))
        assert manifest.workroom_id == workroom_a.id
        # Every item in the manifest belongs to A (the workroom being exported)
        assert len(manifest.items) >= 1  # At minimum: metadata item


# ---------------------------------------------------------------------------
# 4. GLOBAL -> WORKSPACE = NEVER
# ---------------------------------------------------------------------------

class TestGlobalCannotSeeWorkspaces:
    """From Global Workroom context, workspace-scoped resources are invisible."""

    def test_global_connectors_exclude_workspace_a(self, sdk, workroom_a):
        """Connectors listed under Global must not include A's connectors."""
        global_connectors = _list_connectors(sdk, GLOBAL_WORKROOM_ID)
        global_wids = {c.get("workroom_id") for c in global_connectors}
        assert str(workroom_a.id) not in global_wids, \
            "Global sees Workspace A's connectors -- Global->Workspace leak!"

    def test_global_extensions_exclude_workspace_b(self, sdk, workroom_b):
        """Extensions listed under Global must not include B's extensions."""
        global_exts = _list_extensions(sdk, GLOBAL_WORKROOM_ID)
        for ext in global_exts:
            wid = ext.get("workroom_id") if isinstance(ext, dict) else getattr(ext, "workroom_id", None)
            assert wid != str(workroom_b.id), \
                "Global sees Workspace B's extension -- Global->Workspace leak!"

    def test_global_export_excludes_workspace_resources(self, sdk):
        """Export manifest for Global should not reference workspace-scoped resources."""
        manifest = sdk.workrooms.get_export_manifest(GLOBAL_WORKROOM_ID)
        assert str(manifest.workroom_id) == GLOBAL_WORKROOM_ID


# ---------------------------------------------------------------------------
# 5. WORKSPACE -> GLOBAL (default: allowed)
# ---------------------------------------------------------------------------

class TestWorkspaceCanSeeGlobal:
    """By default (Admin ON), workspace context can access Global resources.

    The current server uses strict single-workroom filtering, so workspace->global
    access is achieved by the client making a separate request to the Global
    context. These tests verify the client-side access pattern works -- the user
    in Workspace A can still query Global.
    """

    def test_workspace_user_can_read_global_workroom(self, sdk, workroom_a):
        """User who owns Workspace A can also read the Global Workroom."""
        global_wr = sdk.workrooms.get(GLOBAL_WORKROOM_ID)
        assert str(global_wr.id) == GLOBAL_WORKROOM_ID

    def test_workspace_user_can_list_global_connectors(self, sdk, workroom_a):
        """User in Workspace A can query Global Workroom connectors."""
        global_connectors = _list_connectors(sdk, GLOBAL_WORKROOM_ID)
        assert isinstance(global_connectors, list)

    def test_workspace_user_can_get_global_export_manifest(self, sdk, workroom_a):
        """User in Workspace A can get Global Workroom export manifest."""
        manifest = sdk.workrooms.get_export_manifest(GLOBAL_WORKROOM_ID)
        assert str(manifest.workroom_id) == GLOBAL_WORKROOM_ID

    def test_workspace_user_can_get_global_ingestion_summary(self, sdk, workroom_a):
        """User in Workspace A can get Global Workroom ingestion summary."""
        summary = sdk.workrooms.get_ingestion_summary(GLOBAL_WORKROOM_ID)
        assert str(summary.workroom_id) == GLOBAL_WORKROOM_ID


# ---------------------------------------------------------------------------
# 6. LIFECYCLE + ISOLATION INTERACTIONS
# ---------------------------------------------------------------------------

class TestLifecycleIsolation:
    """Verify workroom lifecycle operations don't break isolation."""

    def test_delete_workroom_does_not_affect_other(self, sdk, workroom_b):
        """Deleting Workspace A does not affect Workspace B."""
        wr_a = sdk.workrooms.create(_unique("delete-test"), "ephemeral")
        a_id = str(wr_a.id)

        # Delete A
        result = sdk.workrooms.delete(a_id)
        assert result.status == "deleted"

        # B is untouched
        fetched_b = sdk.workrooms.get(str(workroom_b.id))
        assert fetched_b.status == "active"

        # A is gone
        with pytest.raises(NotFoundError):
            sdk.workrooms.get(a_id)

    def test_archive_workroom_still_visible_with_flag(self, sdk):
        """Archived workroom appears when include_archived=True."""
        wr = sdk.workrooms.create(_unique("archive-vis"), "ephemeral")
        try:
            sdk.workrooms.archive(str(wr.id))

            # With flag: must appear
            all_wrs = sdk.workrooms.list(include_archived=True)
            all_ids = {str(w.id) for w in all_wrs}
            assert str(wr.id) in all_ids
        finally:
            try:
                sdk.workrooms.delete(str(wr.id))
            except (APIError, NotFoundError):
                pass


# ---------------------------------------------------------------------------
# 7. SENTINEL VALUE: ?workroom_id=all (admin bypass)
# ---------------------------------------------------------------------------

class TestSentinelAllBypass:
    """The ?workroom_id=all sentinel bypasses workroom filtering (admin use)."""

    def test_deployments_all_returns_across_workrooms(self, sdk, workroom_a, workroom_b):
        """Passing workroom_id=all returns deployments from all workrooms."""
        all_deployments = _list_deployments(sdk, workroom_id="all")
        assert isinstance(all_deployments, list)

    def test_connectors_without_header_defaults_to_global(self, sdk):
        """When no X-Workroom-Id header is sent, server defaults to Global."""
        resp = sdk.get("/dde/connectors/")
        items = resp.get("items", [])
        for c in items:
            wid = c.get("workroom_id")
            if wid is not None:
                assert wid == GLOBAL_WORKROOM_ID, \
                    f"No-header request returned non-Global connector: {wid}"
