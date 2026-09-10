"""Regression coverage for SDK override runtime classification.

These pin the classification precedence that was previously only exercised
indirectly through the override tests: a readable Dockerfile base image wins,
and anything else falls back to the name/port heuristic.
"""

import pytest

from kamiwaza_extensions.sdk_override.classification import (
    _detect_build_service_runtime,
    detect_service_runtime,
    detect_service_type,
)


def _service_with_dockerfile(tmp_path, subdir, contents):
    extension_dir = tmp_path / "extension"
    service_dir = extension_dir / subdir
    service_dir.mkdir(parents=True)
    (service_dir / "Dockerfile").write_text(contents)
    return extension_dir, {"build": {"context": f"./{subdir}"}}


def test_python_runtime_base_wins_over_frontend_name(tmp_path):
    extension_dir, service = _service_with_dockerfile(
        tmp_path, "web", "FROM python:3.11\n"
    )

    runtime = detect_service_runtime("web", service, extension_dir=extension_dir)

    assert runtime == "backend"


def test_static_runtime_base_wins_over_backend_name(tmp_path):
    extension_dir, service = _service_with_dockerfile(
        tmp_path, "api", "FROM node:22 AS build\nFROM nginx:1.27\n"
    )

    runtime = detect_service_runtime("api", service, extension_dir=extension_dir)

    assert runtime == "static"


def test_unrecognized_runtime_base_falls_back_to_service_heuristics(tmp_path):
    """A base image matching no known runtime token is not decisive, so
    classification falls back to the name/port heuristic."""
    extension_dir, service = _service_with_dockerfile(
        tmp_path, "database", "FROM debian:12\n"
    )

    runtime = detect_service_runtime("postgres", service, extension_dir=extension_dir)

    assert runtime == "backend"


def test_missing_dockerfile_falls_back_to_service_heuristics(tmp_path):
    runtime = detect_service_runtime(
        "backend",
        {"build": "./backend", "ports": ["8000:8000"]},
        extension_dir=tmp_path,
    )

    assert runtime == "backend"


def test_service_heuristics_classify_by_name_then_port():
    assert detect_service_type("frontend", {}) == "frontend"
    assert detect_service_type("admin-ui", {}) == "frontend"
    assert detect_service_type("api", {"ports": ["3000:3000"]}) == "frontend"
    assert detect_service_type("api", {"ports": ["8000:8000"]}) == "backend"
    assert (
        detect_service_type("worker", {"build": {"context": "./frontend"}})
        == "frontend"
    )


def test_detect_service_type_stays_publicly_exported():
    """It is a documented public symbol; dropping it would break importers."""
    import kamiwaza_extensions.sdk_override as sdk_override

    assert "detect_service_type" in sdk_override.__all__
    assert sdk_override.detect_service_type("frontend", {}) == "frontend"


class TestBuildTimeClassification:
    """``_detect_build_service_runtime`` drives build-time overlay insertion.
    Its unrecognised-runtime fallback was previously untested: deleting it left
    every test green while a `FROM debian:12` service silently lost its overlay."""

    @staticmethod
    def _service(tmp_path, dockerfile: str):
        extension_dir = tmp_path / "extension"
        service_dir = extension_dir / "svc"
        service_dir.mkdir(parents=True)
        (service_dir / "Dockerfile").write_text(dockerfile)
        return extension_dir, {"build": {"context": "./svc"}}

    @pytest.mark.parametrize(
        ("base", "service_name", "expected"),
        (
            ("FROM debian:12\n", "backend", "backend"),
            ("FROM golang:1.22\n", "backend", "backend"),
            ("FROM debian:12\n", "frontend", "frontend"),
        ),
    )
    def test_unrecognized_final_base_falls_back_to_heuristics(
        self, tmp_path, base: str, service_name: str, expected: str
    ) -> None:
        extension_dir, service = self._service(tmp_path, base)

        assert (
            _detect_build_service_runtime(
                service_name, service, extension_dir=extension_dir
            )
            == expected
        )

    def test_node_build_stage_shipping_from_nginx_is_a_frontend(self, tmp_path) -> None:
        extension_dir, service = self._service(
            tmp_path, "FROM node:22 AS build\nFROM nginx:1.27\n"
        )

        assert (
            _detect_build_service_runtime("api", service, extension_dir=extension_dir)
            == "frontend"
        )

    def test_python_final_base_is_a_backend(self, tmp_path) -> None:
        extension_dir, service = self._service(tmp_path, "FROM python:3.12\n")

        assert (
            _detect_build_service_runtime("web", service, extension_dir=extension_dir)
            == "backend"
        )

    def test_missing_dockerfile_falls_back_to_heuristics(self, tmp_path) -> None:
        assert (
            _detect_build_service_runtime(
                "backend", {"build": "./nope"}, extension_dir=tmp_path
            )
            == "backend"
        )
