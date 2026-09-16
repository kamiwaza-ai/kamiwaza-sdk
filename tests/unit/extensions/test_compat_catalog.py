"""History and concurrent publication guarantees for the opt-in generation."""

import io
import json
from itertools import permutations
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError

from kamiwaza_extensions.catalog_publisher import CatalogPublisher, CatalogPublishError
from kamiwaza_extensions.compat_catalog import merge_release, validate_entry
from kamiwaza_extensions.profile_manager import PublishProfile

pytestmark = pytest.mark.unit


def row(version, minimum=None):
    result = {"name": "demo", "version": version, "opaque": {"keep": True}}
    if minimum is not None:
        result["kamiwaza_version"] = minimum
    return result


@pytest.mark.parametrize(
    "order",
    list(
        permutations(
            [row("0.3.0", ">=1.3.0"), row("0.4.0", ">=1.3.1"), row("0.5.0", ">=1.4.0")]
        )
    ),
)
def test_publication_order_preserves_all_releases(order):
    catalog = [row("0.1.0")]
    for entry in order:
        catalog, action = merge_release(entry, catalog)
        assert action == "insert"
    assert [entry["version"] for entry in catalog] == [
        "0.1.0",
        "0.3.0",
        "0.4.0",
        "0.5.0",
    ]
    assert all(entry["opaque"] == {"keep": True} for entry in catalog)


def test_force_is_exact_normalized_release_only():
    catalog = [row("1.0"), row("2.0.0", ">=1.4.0")]
    with pytest.raises(ValueError, match="already exists"):
        merge_release(row("1.0.0", ">=1.3.1"), catalog)
    merged, action = merge_release(row("1.0.0", ">=1.3.1"), catalog, force=True)
    assert action == "replace"
    assert merged == [row("1.0.0", ">=1.3.1"), catalog[1]]


@pytest.mark.parametrize(
    "minimum", [">=1.3,", ">= 1.3", ">=1", ">=1.3rc1", "~=1.3", "==1.*", 13]
)
def test_invalid_minimum_fails_closed(minimum):
    with pytest.raises(ValueError):
        validate_entry(row("1.0.0", minimum))


@pytest.mark.parametrize("version", ["", "bad", None, 3])
def test_invalid_extension_version(version):
    with pytest.raises(ValueError):
        validate_entry(row(version))


class ConditionalStore:
    """Small S3 conditional-write model with controllable interleavings."""

    def __init__(self):
        self.body = None
        self.etag = None
        self.revision = 0
        self.before_put = None
        self.puts = []

    def get_object(self, **kwargs):
        if self.body is None:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.body), "ETag": self.etag}

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        callback, self.before_put = self.before_put, None
        if callback:
            callback()
        condition = kwargs.get("IfMatch") == self.etag
        if "IfNoneMatch" in kwargs:
            condition = self.body is None
        if not condition:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.revision += 1
        self.etag = f'"{self.revision}"'
        self.body = kwargs["Body"]
        return {"ETag": self.etag}


@pytest.fixture
def publisher():
    profile = PublishProfile(
        "local", "localhost:5000", "http://localhost:9000", "test", "env"
    )
    with patch("boto3.Session"):
        publisher = CatalogPublisher(profile, catalog_schema="compat-v1")
    publisher._s3 = ConditionalStore()
    return publisher


def test_concurrent_first_creation_remerges_without_losing_winner(publisher):
    store = publisher._s3
    store.before_put = lambda: publisher.publish(row("0.5.0", ">=1.4.0"), "app")
    result = publisher.publish(row("0.3.0", ">=1.3.0"), "app")
    assert result.catalog_file == "garden/compat-v1/apps.json"
    assert [r["version"] for r in json.loads(store.body)] == ["0.3.0", "0.5.0"]
    assert store.puts[-1]["IfMatch"] == '"1"'


def test_existing_object_conflict_preserves_concurrent_maintenance_release(publisher):
    publisher.publish(row("0.5.0", ">=1.4.0"), "tool")
    store = publisher._s3
    store.before_put = lambda: publisher.publish(row("0.3.1", ">=1.3.0"), "tool")
    publisher.publish(row("0.6.0", ">=1.3.1"), "tool")
    assert [r["version"] for r in json.loads(store.body)] == ["0.3.1", "0.5.0", "0.6.0"]


def test_concurrent_same_release_rejects_loser(publisher):
    store = publisher._s3
    store.before_put = lambda: publisher.publish(row("0.5.0", ">=1.4.0"), "app")
    with pytest.raises(ValueError, match="already exists"):
        publisher.publish(row("0.5.0", ">=1.3.0"), "app")
    assert json.loads(store.body) == [row("0.5.0", ">=1.4.0")]


def test_dry_run_does_not_write(publisher):
    result = publisher.publish(row("0.3.0", ">=1.3.0"), "app", dry_run=True)
    assert result.dry_run
    assert publisher._s3.puts == []


def test_preview_failure_cannot_rollback_catalog(publisher, tmp_path):
    publisher.publish(row("0.3.0"), "app")
    before = publisher._s3.body
    with pytest.raises(FileNotFoundError):
        publisher.publish(
            row("0.4.0"), "app", preview_image_path=tmp_path / "missing.png"
        )
    assert publisher._s3.body == before


def test_repeated_conflict_has_bounded_retries(publisher):
    def conflict(**kwargs):
        raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")

    publisher._s3.put_object = conflict
    with pytest.raises(CatalogPublishError, match="5 commit attempts"):
        publisher.publish(row("0.3.0"), "app")


def test_connectors_are_not_silently_migrated(publisher):
    with pytest.raises(ValueError, match="apps, services, and tools"):
        publisher.publish(row("0.3.0"), "connector")


@pytest.mark.parametrize(
    "minimum", [None, "", "  ", "*", " * ", "1.3", ">=1.3,!=1.3.2"]
)
def test_legacy_unrestricted_and_numeric_constraints(minimum):
    validate_entry(row("1.0.0", minimum))


def test_prerelease_and_revision_order_matches_core():
    catalog = []
    for version in ["1.0.0", "1.0.0rc1", "1.0.0.dev1", "1.0.0.post1"]:
        catalog, _ = merge_release(row(version), catalog)
    assert [item["version"] for item in catalog] == [
        "1.0.0.dev1",
        "1.0.0rc1",
        "1.0.0",
        "1.0.0.post1",
    ]


@pytest.mark.parametrize("generation", ["2", "3", "compat-v1"])
def test_cli_accepts_only_explicit_generations(generation):
    from typer.testing import CliRunner

    from kamiwaza_extensions.cli import app

    with patch("kamiwaza_extensions.commands.publish.run_publish") as run:
        result = CliRunner().invoke(
            app, ["publish", "--stage", "dev", "--catalog-schema", generation]
        )
    assert result.exit_code == 0, result.output
    expected = int(generation) if generation.isdigit() else generation
    assert run.call_args.kwargs["catalog_schema"] == expected


def test_cli_rejects_path_like_generation():
    from typer.testing import CliRunner

    from kamiwaza_extensions.cli import app

    result = CliRunner().invoke(
        app, ["publish", "--stage", "dev", "--catalog-schema", "../../v3"]
    )
    assert result.exit_code != 0


def test_capability_command_is_machine_readable():
    from typer.testing import CliRunner

    from kamiwaza_extensions.cli import app

    result = CliRunner().invoke(app, ["catalog-capabilities"])
    assert result.exit_code == 0
    assert "compat-v1-cas" in json.loads(result.output)["capabilities"]


@pytest.mark.parametrize("constraint", ["*", "", "  ", "1.3", ">=1.3,!=1.3.2"])
def test_metadata_authoring_accepts_core_grammar(constraint):
    from kamiwaza_extensions.validators.metadata import _is_valid_platform_constraint

    assert _is_valid_platform_constraint(constraint)


@pytest.mark.parametrize(
    "constraint", ["~=1.3", "==1.*", ">= 1.3", ">=1.3,", ">=1.3rc1"]
)
def test_metadata_authoring_rejects_unsupported_pep440(constraint):
    from kamiwaza_extensions.validators.metadata import _is_valid_platform_constraint

    assert not _is_valid_platform_constraint(constraint)


def test_missing_etag_refuses_write(publisher):
    publisher._s3.get_object = lambda **kw: {"Body": io.BytesIO(b"[]")}
    with pytest.raises(ValueError, match="ETag"):
        publisher.publish(row("0.3.0"), "app")
    assert publisher._s3.puts == []


def test_preview_failure_preserves_a_concurrent_writer(publisher, tmp_path):
    image = tmp_path / "preview.png"
    image.write_bytes(b"fixture")

    def fail_after_another_commit(*args):
        publisher.publish(row("0.5.0", ">=1.4.0"), "app")
        raise RuntimeError("preview unavailable")

    publisher._upload_preview_image = fail_after_another_commit
    with pytest.raises(RuntimeError, match="preview unavailable"):
        publisher.publish(row("0.4.0"), "app", preview_image_path=image)
    assert json.loads(publisher._s3.body) == [row("0.5.0", ">=1.4.0")]


def test_preview_is_content_addressed_before_catalog_commit(publisher, tmp_path):
    image = tmp_path / "preview.png"
    image.write_bytes(b"fixture")
    names = []
    publisher._upload_preview_image = lambda path, name: names.append(name)
    result = publisher.publish(row("0.4.0"), "app", preview_image_path=image)
    assert result.images_pushed == names
    assert len(names[0]) == 68
    assert json.loads(publisher._s3.body)[0]["preview_image"] == "images/" + names[0]


def test_revision_grammar_is_still_checked(publisher):
    entry = row("0.4.0")
    entry["revision"] = "not valid"
    with pytest.raises(ValueError, match="Invalid revision"):
        publisher.publish(entry, "app")
    assert publisher._s3.puts == []


@pytest.mark.parametrize("name", [None, "", "  ", 42])
def test_invalid_identity_fails_before_storage(name, publisher):
    entry = row("1.0.0")
    entry["name"] = name
    with pytest.raises(ValueError, match="nonempty name"):
        publisher.publish(entry, "app")
    assert publisher._s3.puts == []


def test_storage_read_failure_is_not_treated_as_empty(publisher):
    def denied(**kwargs):
        raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")

    publisher._s3.get_object = denied
    with pytest.raises(ClientError, match="AccessDenied"):
        publisher.publish(row("1.0.0"), "app")
    assert publisher._s3.puts == []


def test_malformed_catalog_is_not_overwritten(publisher):
    publisher._s3.body = b"{}"
    publisher._s3.etag = '"first"'
    with pytest.raises(ValueError, match="JSON array"):
        publisher.publish(row("1.0.0"), "app")
    assert publisher._s3.puts == []


def test_storage_write_failure_does_not_restore_or_retry(publisher):
    attempts = []

    def denied(**kwargs):
        attempts.append(kwargs)
        raise ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")

    publisher._s3.put_object = denied
    with pytest.raises(ClientError, match="AccessDenied"):
        publisher.publish(row("1.0.0"), "app")
    assert len(attempts) == 1


def test_old_botocore_model_does_not_advertise_cas():
    from typer.testing import CliRunner

    from kamiwaza_extensions.cli import app

    with patch("botocore.session.Session") as session:
        model = (
            session.return_value.get_service_model.return_value.operation_model.return_value
        )
        model.input_shape.members = {"Bucket": {}, "Key": {}, "IfNoneMatch": {}}
        result = CliRunner().invoke(app, ["catalog-capabilities"])
    assert result.exit_code == 0
    assert json.loads(result.output) == {"generations": [2, 3], "capabilities": []}


def test_old_botocore_model_blocks_before_preview_or_catalog(publisher, tmp_path):
    image = tmp_path / "preview.png"
    image.write_bytes(b"fixture")
    with patch(
        "kamiwaza_extensions.compat_catalog.supports_conditional_writes",
        return_value=False,
    ):
        with patch.object(publisher, "_upload_preview_image") as preview:
            with pytest.raises(ValueError, match="1.35.70"):
                publisher.publish(row("1.0.0"), "app", preview_image_path=image)
    preview.assert_not_called()
    assert publisher._s3.puts == []
