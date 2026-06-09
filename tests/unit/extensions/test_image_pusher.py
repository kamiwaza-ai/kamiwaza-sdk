"""Tests for ImagePusher."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from kamiwaza_extensions.image_pusher import (
    ImagePusher,
    ImagePushError,
    validate_digest,
)


@pytest.fixture
def pusher():
    return ImagePusher()


class TestLoginRegistry:
    @patch("kamiwaza_extensions.image_pusher.subprocess.run")
    def test_docker_login(self, mock_run, pusher):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        pusher._login_registry("reg.test", "my-token")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "docker"
        assert "--tls-verify=false" not in cmd

    @patch("kamiwaza_extensions.image_pusher.subprocess.run")
    def test_podman_login_insecure(self, mock_run, pusher):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        pusher._login_registry(
            "reg.test",
            "my-token",
            use_podman=True,
            insecure=True,
        )
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "podman"
        assert "--tls-verify=false" in cmd

    @patch("kamiwaza_extensions.image_pusher.subprocess.run")
    def test_podman_login_secure_keeps_tls_verification(self, mock_run, pusher):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        pusher._login_registry("reg.test", "my-token", use_podman=True)
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "podman"
        assert "--tls-verify=false" not in cmd

    @patch("kamiwaza_extensions.image_pusher.subprocess.run")
    def test_login_failure_raises(self, mock_run, pusher):
        mock_run.return_value = MagicMock(returncode=1, stderr="auth failed")
        with pytest.raises(ImagePushError, match="login failed"):
            pusher._login_registry("reg.test", "bad-token")


class TestPush:
    @patch("kamiwaza_extensions.image_pusher.subprocess.run")
    def test_docker_push(self, mock_run, pusher):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        pusher._push("reg.test/app:v1")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "docker"
        assert "--tls-verify=false" not in cmd

    @patch("kamiwaza_extensions.image_pusher.subprocess.run")
    def test_podman_push_insecure(self, mock_run, pusher):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        pusher._push("reg.test/app:v1", use_podman=True, insecure=True)
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "podman"
        assert "--tls-verify=false" in cmd

    @patch("kamiwaza_extensions.image_pusher.subprocess.run")
    def test_podman_push_secure_keeps_tls_verification(self, mock_run, pusher):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        pusher._push("reg.test/app:v1", use_podman=True)
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "podman"
        assert "--tls-verify=false" not in cmd

    @patch("kamiwaza_extensions.image_pusher.subprocess.run")
    def test_push_failure_raises(self, mock_run, pusher):
        mock_run.return_value = MagicMock(returncode=1, stderr="denied")
        with pytest.raises(ImagePushError, match="Push failed"):
            pusher._push("reg.test/app:v1")

    @patch("kamiwaza_extensions.image_pusher.subprocess.run")
    def test_verbose_push_failure_raises_image_push_error(self, mock_run, pusher):
        mock_run.side_effect = subprocess.CalledProcessError(
            1, ["docker", "push", "reg.test/app:v1"], stderr="denied"
        )
        with pytest.raises(ImagePushError, match="Push failed"):
            pusher._push("reg.test/app:v1", verbose=True)


class TestTag:
    @patch("kamiwaza_extensions.image_pusher.subprocess.run")
    def test_verbose_tag_failure_raises_image_push_error(self, mock_run, pusher):
        mock_run.side_effect = subprocess.CalledProcessError(
            1,
            ["docker", "tag", "reg.test/app:v1", "push.test/app:v1"],
            stderr="missing image",
        )
        with pytest.raises(ImagePushError, match="Retag failed"):
            pusher._tag("reg.test/app:v1", "push.test/app:v1", verbose=True)


class TestPushOrchestration:
    @patch(
        "kamiwaza_extensions.registry_resolution.podman_push_available",
        return_value=True,
    )
    @patch.object(ImagePusher, "_login_registry")
    @patch.object(ImagePusher, "_tag")
    @patch.object(ImagePusher, "_push")
    def test_insecure_uses_podman(
        self, mock_push, mock_tag, mock_login, _mock_hp, pusher
    ):
        pusher.push(["reg/app:v1"], registry="reg", token="tok", insecure=True)
        mock_login.assert_called_once_with("reg", "tok", use_podman=True, insecure=True)
        mock_tag.assert_not_called()
        mock_push.assert_called_once_with(
            "reg/app:v1",
            use_podman=True,
            insecure=True,
            verbose=False,
        )

    @patch(
        "kamiwaza_extensions.registry_resolution.podman_push_available",
        return_value=True,
    )
    @patch.object(ImagePusher, "_login_registry")
    @patch.object(ImagePusher, "_tag")
    @patch.object(ImagePusher, "_push")
    def test_explicit_docker_engine_overrides_insecure_podman_auto_selection(
        self, mock_push, mock_tag, mock_login, _mock_hp, pusher
    ):
        pusher.push(
            ["reg/app:v1"],
            registry="reg",
            token="tok",
            insecure=True,
            engine="docker",
        )
        mock_login.assert_called_once_with(
            "reg", "tok", use_podman=False, insecure=True
        )
        mock_tag.assert_not_called()
        mock_push.assert_called_once_with(
            "reg/app:v1",
            use_podman=False,
            insecure=True,
            verbose=False,
        )

    @patch(
        "kamiwaza_extensions.registry_resolution.podman_push_available",
        return_value=False,
    )
    @patch.object(ImagePusher, "_login_registry")
    @patch.object(ImagePusher, "_tag")
    @patch.object(ImagePusher, "_push")
    def test_insecure_no_podman_falls_back_to_docker(
        self, mock_push, mock_tag, mock_login, _mock_hp, pusher
    ):
        pusher.push(["reg/app:v1"], registry="reg", token="tok", insecure=True)
        mock_login.assert_called_once_with(
            "reg", "tok", use_podman=False, insecure=True
        )
        mock_tag.assert_not_called()
        mock_push.assert_called_once_with(
            "reg/app:v1",
            use_podman=False,
            insecure=True,
            verbose=False,
        )

    @patch(
        "kamiwaza_extensions.registry_resolution.podman_push_available",
        return_value=True,
    )
    @patch.object(ImagePusher, "_login_registry")
    @patch.object(ImagePusher, "_tag")
    @patch.object(ImagePusher, "_push")
    def test_secure_uses_docker(
        self, mock_push, mock_tag, mock_login, _mock_hp, pusher
    ):
        pusher.push(["reg/app:v1"], registry="reg", token="tok", insecure=False)
        mock_login.assert_called_once_with(
            "reg", "tok", use_podman=False, insecure=False
        )
        mock_tag.assert_not_called()
        mock_push.assert_called_once_with(
            "reg/app:v1",
            use_podman=False,
            insecure=False,
            verbose=False,
        )

    @patch.object(ImagePusher, "_login_registry")
    @patch.object(ImagePusher, "_tag")
    @patch.object(ImagePusher, "_push")
    def test_explicit_podman_engine_secure_keeps_tls_verification(
        self, mock_push, mock_tag, mock_login, pusher
    ):
        pusher.push(
            ["reg/app:v1"],
            registry="reg",
            token="tok",
            insecure=False,
            engine="podman",
        )
        mock_login.assert_called_once_with(
            "reg", "tok", use_podman=True, insecure=False
        )
        mock_tag.assert_not_called()
        mock_push.assert_called_once_with(
            "reg/app:v1",
            use_podman=True,
            insecure=False,
            verbose=False,
        )

    @patch(
        "kamiwaza_extensions.registry_resolution.podman_push_available",
        return_value=True,
    )
    @patch.object(ImagePusher, "_login_registry")
    @patch.object(ImagePusher, "_tag")
    @patch.object(ImagePusher, "_push")
    def test_target_refs_are_retagged_and_pushed(
        self, mock_push, mock_tag, mock_login, _mock_hp, pusher
    ):
        pusher.push(
            ["127.0.0.1:30010/app:v1"],
            registry="host.containers.internal:30010",
            token="tok",
            insecure=True,
            target_refs={
                "127.0.0.1:30010/app:v1": "host.containers.internal:30010/app:v1",
            },
        )

        mock_login.assert_called_once_with(
            "host.containers.internal:30010",
            "tok",
            use_podman=True,
            insecure=True,
        )
        mock_tag.assert_called_once_with(
            "127.0.0.1:30010/app:v1",
            "host.containers.internal:30010/app:v1",
            use_podman=True,
            verbose=False,
        )
        mock_push.assert_called_once_with(
            "host.containers.internal:30010/app:v1",
            use_podman=True,
            insecure=True,
            verbose=False,
        )

    @patch(
        "kamiwaza_extensions.registry_resolution.podman_push_available",
        return_value=True,
    )
    @patch.object(ImagePusher, "_login_registry")
    @patch.object(ImagePusher, "_tag")
    @patch.object(ImagePusher, "_push")
    def test_no_token_skips_login_still_pushes(
        self, mock_push, mock_tag, mock_login, _mock_hp, pusher
    ):
        # ENG-5719: the anonymous local dev registry needs no login. Passing
        # token=None must skip the (spurious, macOS-podman-broken) login while
        # still retagging+pushing to the build-VM alias.
        pusher.push(
            ["127.0.0.1:30010/app:v1"],
            registry="host.containers.internal:30010",
            token=None,
            insecure=True,
            target_refs={
                "127.0.0.1:30010/app:v1": "host.containers.internal:30010/app:v1",
            },
        )
        mock_login.assert_not_called()
        mock_tag.assert_called_once_with(
            "127.0.0.1:30010/app:v1",
            "host.containers.internal:30010/app:v1",
            use_podman=True,
            verbose=False,
        )
        mock_push.assert_called_once_with(
            "host.containers.internal:30010/app:v1",
            use_podman=True,
            insecure=True,
            verbose=False,
        )


# ENG-4370 — digest-pinning catalog references


_VALID_DIGEST = "sha256:" + "a" * 64


class TestValidateDigest:
    @pytest.mark.parametrize(
        "good",
        [
            "sha256:" + "0" * 64,
            "sha256:" + "f" * 64,
            "sha256:" + "abcdef0123456789" * 4,
        ],
    )
    def test_accepts_well_formed(self, good):
        validate_digest(good)  # no raise

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "abc",
            "sha512:" + "a" * 64,  # wrong algorithm
            "sha256:" + "a" * 63,  # too short
            "sha256:" + "a" * 65,  # too long
            "sha256:" + "A" * 64,  # uppercase hex
            "sha256:" + "g" * 64,  # non-hex char
            "sha256:" + "a" * 32 + " " + "a" * 31,  # embedded space
            "Sha256:" + "a" * 64,  # uppercase prefix
        ],
    )
    def test_rejects_malformed(self, bad):
        with pytest.raises(ValueError, match="Invalid digest"):
            validate_digest(bad)


class TestResolveDigest:
    @patch("kamiwaza_extensions.image_pusher.subprocess.run")
    def test_returns_digest_from_imagetools(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"digest":"'
            + _VALID_DIGEST
            + '","mediaType":"application/vnd.oci.image.index.v1+json"}',
            stderr="",
        )
        digest = ImagePusher.resolve_digest("reg.test/app:v1")
        assert digest == _VALID_DIGEST
        cmd = mock_run.call_args[0][0]
        assert cmd[:4] == ["docker", "buildx", "imagetools", "inspect"]
        assert "reg.test/app:v1" in cmd
        assert "{{json .Manifest}}" in cmd

    @patch("kamiwaza_extensions.image_pusher.subprocess.run")
    def test_inspect_failure_raises(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="manifest unknown"
        )
        with pytest.raises(ImagePushError, match="Digest resolution failed"):
            ImagePusher.resolve_digest("reg.test/app:v1")

    @patch("kamiwaza_extensions.image_pusher.subprocess.run")
    def test_non_dict_json_raises(self, mock_run):
        # Valid JSON but not a dict — bare .get would AttributeError
        # without an isinstance guard.
        mock_run.return_value = MagicMock(returncode=0, stdout="[]", stderr="")
        with pytest.raises(ImagePushError, match="expected an object"):
            ImagePusher.resolve_digest("reg.test/app:v1")

    @patch("kamiwaza_extensions.image_pusher.subprocess.run")
    def test_invalid_json_raises(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="not-json-at-all", stderr=""
        )
        with pytest.raises(ImagePushError, match="parse imagetools output"):
            ImagePusher.resolve_digest("reg.test/app:v1")

    @patch("kamiwaza_extensions.image_pusher.subprocess.run")
    def test_missing_digest_field_raises(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"mediaType":"foo"}', stderr=""
        )
        with pytest.raises(ImagePushError, match="Unexpected digest field"):
            ImagePusher.resolve_digest("reg.test/app:v1")

    @patch("kamiwaza_extensions.image_pusher.subprocess.run")
    def test_malformed_digest_field_raises(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"digest":"not-a-digest"}', stderr=""
        )
        with pytest.raises(ImagePushError, match="Unexpected digest field"):
            ImagePusher.resolve_digest("reg.test/app:v1")

    @patch(
        "kamiwaza_extensions.image_pusher.subprocess.run",
        side_effect=FileNotFoundError(),
    )
    def test_missing_docker_raises(self, _mock_run):
        with pytest.raises(ImagePushError, match="docker not found"):
            ImagePusher.resolve_digest("reg.test/app:v1")

    @patch(
        "kamiwaza_extensions.image_pusher.subprocess.run",
        side_effect=__import__("subprocess").TimeoutExpired(cmd="docker", timeout=60),
    )
    def test_timeout_raises_image_push_error(self, _mock_run):
        # Wedged registry must surface as ImagePushError, not an
        # unhandled TimeoutExpired.
        with pytest.raises(ImagePushError, match="timed out"):
            ImagePusher.resolve_digest("reg.test/app:v1")


class TestCheckBuildxAvailable:
    @patch("kamiwaza_extensions.image_pusher.subprocess.run")
    def test_passes_when_buildx_present(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        ImagePusher.check_buildx_available()  # no raise
        cmd = mock_run.call_args[0][0]
        assert cmd == ["docker", "buildx", "imagetools", "--help"]

    @patch(
        "kamiwaza_extensions.image_pusher.subprocess.run",
        side_effect=FileNotFoundError(),
    )
    def test_raises_when_docker_missing(self, _mock_run):
        with pytest.raises(ImagePushError, match="docker not found"):
            ImagePusher.check_buildx_available()

    @patch("kamiwaza_extensions.image_pusher.subprocess.run")
    def test_raises_when_buildx_subcommand_missing(self, mock_run):
        # docker is present but buildx plugin isn't — docker exits non-zero
        # with stderr like "docker: 'buildx' is not a docker command".
        mock_run.return_value = MagicMock(
            returncode=1, stderr=b"docker: 'buildx' is not a docker command."
        )
        with pytest.raises(ImagePushError, match="buildx imagetools is not available"):
            ImagePusher.check_buildx_available()

    @patch(
        "kamiwaza_extensions.image_pusher.subprocess.run",
        side_effect=__import__("subprocess").TimeoutExpired(cmd="docker", timeout=5),
    )
    def test_raises_on_timeout(self, _mock_run):
        with pytest.raises(ImagePushError, match="availability check timed out"):
            ImagePusher.check_buildx_available()


class TestPushTimeout:
    @patch(
        "kamiwaza_extensions.image_pusher.subprocess.run",
        side_effect=__import__("subprocess").TimeoutExpired(cmd="docker", timeout=600),
    )
    def test_push_timeout_raises_image_push_error(self, _mock_run, pusher):
        with pytest.raises(ImagePushError, match="Push timed out"):
            pusher._push("reg.test/app:v1")

    @patch(
        "kamiwaza_extensions.image_pusher.subprocess.run",
        side_effect=__import__("subprocess").TimeoutExpired(cmd="docker", timeout=30),
    )
    def test_login_timeout_raises_image_push_error(self, _mock_run, pusher):
        with pytest.raises(ImagePushError, match="login.*timed out"):
            pusher._login_registry("reg.test", "tok")
