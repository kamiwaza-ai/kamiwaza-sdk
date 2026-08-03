"""Direct coverage for Compose named-volume translation.

The module decides whether a Compose volume becomes a pod emptyDir or is left
to the operator's PVC. Getting that wrong is silent data loss, so the parsing
branches are pinned here rather than only through the payload builder.
"""

from __future__ import annotations

import pytest

from kamiwaza_extensions.compose_volumes import build_service_volume_specs


def _spec(service: dict) -> object:
    return build_service_volume_specs({"services": {"svc": service}}).get("svc")


def test_named_volume_becomes_emptydir_with_mount() -> None:
    spec = _spec({"volumes": ["cache:/var/cache"]})

    assert spec.volumes == [{"name": "cache", "emptyDir": {}}]
    assert spec.mounts == [{"name": "cache", "mountPath": "/var/cache"}]


def test_repeated_source_reuses_one_volume() -> None:
    spec = _spec({"volumes": ["shared:/a", "shared:/b"]})

    assert spec.volumes == [{"name": "shared", "emptyDir": {}}]
    assert [mount["mountPath"] for mount in spec.mounts] == ["/a", "/b"]


@pytest.mark.parametrize(
    "volume",
    [
        "./host/path:/in/container",
        "/abs/host:/in/container",
        "../up:/in/container",
        {"type": "bind", "source": "./host", "target": "/in/container"},
    ],
)
def test_host_paths_and_binds_are_not_translated(volume) -> None:
    assert _spec({"volumes": [volume]}) is None


@pytest.mark.parametrize(
    "volume",
    [
        "cache:/data:ro",
        {"source": "cache", "target": "/data", "read_only": True},
        {"source": "cache", "target": "/data", "readOnly": True},
    ],
)
def test_read_only_is_preserved_in_every_spelling(volume) -> None:
    assert _spec({"volumes": [volume]}).mounts == [
        {"name": "cache", "mountPath": "/data", "readOnly": True}
    ]


@pytest.mark.parametrize(
    ("source_key", "target_key"),
    [("source", "target"), ("src", "destination"), ("src", "dst")],
)
def test_long_form_key_aliases(source_key: str, target_key: str) -> None:
    volume = {source_key: "cache", target_key: "/data"}

    assert _spec({"volumes": [volume]}).mounts == [
        {"name": "cache", "mountPath": "/data"}
    ]


@pytest.mark.parametrize("volume", [None, 42, ["nested"], "no-colon", "cache:rel"])
def test_malformed_entries_are_skipped(volume) -> None:
    assert _spec({"volumes": [volume]}) is None


def test_operator_names_are_reserved_with_collision_counter() -> None:
    spec = _spec({"volumes": ["tmp:/one", "data:/two"]})

    assert [volume["name"] for volume in spec.volumes] == ["tmp-2", "data-2"]


def test_long_source_name_is_truncated_to_a_dns_label() -> None:
    spec = _spec({"volumes": [f"{'v' * 80}:/data"]})

    name = spec.volumes[0]["name"]
    assert len(name) == 63
    assert not name.endswith("-")


class TestPersistenceInteraction:
    """A service with a PVC must never get an emptyDir over that subtree."""

    @staticmethod
    def _service(mount_path: str, volumes: list) -> dict:
        return {
            "volumes": volumes,
            "x-kamiwaza": {
                "persistence": {"enabled": True, "mountPath": mount_path}
            },
        }

    def test_exact_persistence_target_is_left_to_the_pvc(self) -> None:
        service = self._service("/var/lib/postgresql/data", ["pg:/var/lib/postgresql/data"])

        assert _spec(service) is None

    def test_nested_persistence_target_is_left_to_the_pvc(self) -> None:
        """The stock Postgres layout: PGDATA nested under the mountPath."""
        service = self._service("/var/lib/postgresql", ["pg:/var/lib/postgresql/data"])

        assert _spec(service) is None

    def test_trailing_slash_does_not_defeat_the_guard(self) -> None:
        service = self._service("/var/lib/postgresql", ["pg:/var/lib/postgresql/"])

        assert _spec(service) is None

    def test_sibling_prefix_is_still_translated(self) -> None:
        """``/var/lib/postgresql-backup`` is not inside ``/var/lib/postgresql``."""
        service = self._service(
            "/var/lib/postgresql", ["backup:/var/lib/postgresql-backup"]
        )

        assert _spec(service).mounts == [
            {"name": "backup", "mountPath": "/var/lib/postgresql-backup"}
        ]

    def test_unrelated_volume_is_still_translated(self) -> None:
        service = self._service("/var/lib/postgresql", ["cache:/var/cache"])

        assert _spec(service).mounts == [{"name": "cache", "mountPath": "/var/cache"}]

    def test_disabled_persistence_does_not_suppress_translation(self) -> None:
        service = {
            "volumes": ["pg:/data"],
            "x-kamiwaza": {"persistence": {"enabled": False, "mountPath": "/data"}},
        }

        assert _spec(service).mounts == [{"name": "pg", "mountPath": "/data"}]

    def test_enabled_without_mount_path_is_rejected(self) -> None:
        """The operator would provision the PVC and mount it nowhere."""
        service = {
            "volumes": ["cache:/data"],
            "x-kamiwaza": {"persistence": {"enabled": True, "size": "5Gi"}},
        }

        with pytest.raises(ValueError, match="mountPath is missing"):
            _spec(service)
