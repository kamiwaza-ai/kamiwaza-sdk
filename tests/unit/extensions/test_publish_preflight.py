"""Catalog capability failures must precede any publish side effects."""

from pathlib import Path
from unittest.mock import patch

import pytest
import typer

from kamiwaza_extensions.commands.publish import run_publish
from kamiwaza_extensions.connector_publisher import publish_connector
from kamiwaza_extensions.exit_codes import ExitCode
from kamiwaza_extensions.extension_detector import ExtensionInfo

pytestmark = pytest.mark.unit


def _info(kind):
    return ExtensionInfo(
        path=Path(f"/tmp/{kind}"),
        name=kind,
        version="1.0.0",
        metadata={"name": kind, "version": "1.0.0", "type": kind},
    )


@pytest.mark.parametrize("all_extensions", [False, True])
def test_unsupported_connector_rejects_entire_batch_before_handlers(all_extensions):
    with (
        patch("kamiwaza_extensions.extension_detector.ExtensionDetector") as detector,
        patch(
            "kamiwaza_extensions.commands.publish.enforce_cli_contracts"
        ) as contracts,
        patch("kamiwaza_extensions.commands.publish._publish_one") as app_handler,
        patch(
            "kamiwaza_extensions.connector_publisher.publish_connector"
        ) as connector_handler,
    ):
        detector.return_value.detect.return_value = _info("connector")
        detector.return_value.detect_all.return_value = [
            _info("app"),
            _info("connector"),
        ]
        with pytest.raises(typer.Exit) as exc:
            run_publish(
                stage="isolated", catalog_schema="compat-v1", publish_all=all_extensions
            )
        assert exc.value.exit_code == int(ExitCode.VALIDATION)
        contracts.assert_not_called()
        app_handler.assert_not_called()
        connector_handler.assert_not_called()


def test_direct_connector_rejects_before_profile_or_digest_resolution():
    with (
        patch("kamiwaza_extensions.profile_manager.ProfileManager") as profiles,
        patch("kamiwaza_extensions.image_pusher.ImagePusher.resolve_digest") as resolve,
        patch("kamiwaza_extensions.connector_publisher._validate_manifest") as manifest,
    ):
        with pytest.raises(typer.Exit) as exc:
            publish_connector(
                _info("connector"), stage="isolated", catalog_schema="compat-v1"
            )
        assert exc.value.exit_code == int(ExitCode.VALIDATION)
        manifest.assert_not_called()
        profiles.assert_not_called()
        resolve.assert_not_called()


@pytest.mark.parametrize("schema", [2, 3, "compat-v1"])
def test_supported_types_reach_handlers_unchanged(schema):
    infos = [_info(kind) for kind in ["app", "tool", "service"]]
    if schema != "compat-v1":
        infos.append(_info("connector"))
    with (
        patch("kamiwaza_extensions.extension_detector.ExtensionDetector") as detector,
        patch("kamiwaza_extensions.commands.publish.enforce_cli_contracts"),
        patch("kamiwaza_extensions.commands.publish._publish_one") as app_handler,
        patch(
            "kamiwaza_extensions.connector_publisher.publish_connector"
        ) as connector_handler,
    ):
        detector.return_value.detect_all.return_value = infos
        run_publish(stage="isolated", catalog_schema=schema, publish_all=True)
        assert [call.args[0] for call in app_handler.call_args_list] == infos[:3]
        assert all(
            call.kwargs["catalog_schema"] == schema
            for call in app_handler.call_args_list
        )
        assert connector_handler.call_count == (schema != "compat-v1")


def test_invalid_generation_rejects_before_handler():
    with (
        patch("kamiwaza_extensions.extension_detector.ExtensionDetector") as detector,
        patch("kamiwaza_extensions.commands.publish._publish_one") as handler,
    ):
        detector.return_value.detect.return_value = _info("app")
        with pytest.raises(typer.Exit) as exc:
            run_publish(stage="isolated", catalog_schema="unsupported")
        assert exc.value.exit_code == int(ExitCode.VALIDATION)
        handler.assert_not_called()
