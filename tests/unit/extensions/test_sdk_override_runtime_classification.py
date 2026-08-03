"""Regression coverage for SDK override runtime classification."""

from kamiwaza_extensions.sdk_override import (
    SdkOverrideSpec,
    detect_service_runtime,
    generate_build_overrides,
    generate_compose_override,
)


def _postgres_service(tmp_path):
    extension_dir = tmp_path / "extension"
    database_dir = extension_dir / "database"
    database_dir.mkdir(parents=True)
    (database_dir / "Dockerfile").write_text("FROM postgres:17\n")
    service = {
        "build": {"context": "./database"},
        "ports": ["5432"],
    }
    return extension_dir, service


def test_readable_non_sdk_runtime_does_not_use_service_heuristics(tmp_path):
    extension_dir, service = _postgres_service(tmp_path)

    runtime = detect_service_runtime(
        "postgres",
        service,
        extension_dir=extension_dir,
    )

    assert runtime == "other"


def test_missing_dockerfile_does_not_use_service_heuristics(tmp_path):
    runtime = detect_service_runtime(
        "backend",
        {"build": "./backend", "ports": ["8000:8000"]},
        extension_dir=tmp_path,
    )

    assert runtime == "other"


def test_postgres_build_skips_local_and_remote_sdk_overrides(tmp_path):
    extension_dir, service = _postgres_service(tmp_path)
    spec = SdkOverrideSpec(sdk_repo=tmp_path / "sdk")
    compose = {"services": {"postgres": service}}

    local_override = generate_compose_override(
        spec,
        compose,
        extension_dir=extension_dir,
    )
    remote_overrides = generate_build_overrides(
        spec,
        compose,
        extension_dir=extension_dir,
    )

    assert local_override == {"services": {}}
    assert remote_overrides == []
