"""Tests for ImagePusher."""

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
        pusher._login_registry("reg.test", "my-token", use_podman=True)
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "podman"
        assert "--tls-verify=false" in cmd

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
        pusher._push("reg.test/app:v1", use_podman=True)
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "podman"
        assert "--tls-verify=false" in cmd

    @patch("kamiwaza_extensions.image_pusher.subprocess.run")
    def test_push_failure_raises(self, mock_run, pusher):
        mock_run.return_value = MagicMock(returncode=1, stderr="denied")
        with pytest.raises(ImagePushError, match="Push failed"):
            pusher._push("reg.test/app:v1")


class TestPushOrchestration:
    @patch("kamiwaza_extensions.image_pusher._has_podman", return_value=True)
    @patch.object(ImagePusher, "_login_registry")
    @patch.object(ImagePusher, "_push")
    def test_insecure_uses_podman(self, mock_push, mock_login, _mock_hp, pusher):
        pusher.push(["reg/app:v1"], registry="reg", token="tok", insecure=True)
        mock_login.assert_called_once_with("reg", "tok", use_podman=True)
        mock_push.assert_called_once_with("reg/app:v1", use_podman=True, verbose=False)

    @patch("kamiwaza_extensions.image_pusher._has_podman", return_value=False)
    @patch.object(ImagePusher, "_login_registry")
    @patch.object(ImagePusher, "_push")
    def test_insecure_no_podman_falls_back_to_docker(self, mock_push, mock_login, _mock_hp, pusher):
        pusher.push(["reg/app:v1"], registry="reg", token="tok", insecure=True)
        mock_login.assert_called_once_with("reg", "tok", use_podman=False)
        mock_push.assert_called_once_with("reg/app:v1", use_podman=False, verbose=False)

    @patch("kamiwaza_extensions.image_pusher._has_podman", return_value=True)
    @patch.object(ImagePusher, "_login_registry")
    @patch.object(ImagePusher, "_push")
    def test_secure_uses_docker(self, mock_push, mock_login, _mock_hp, pusher):
        pusher.push(["reg/app:v1"], registry="reg", token="tok", insecure=False)
        mock_login.assert_called_once_with("reg", "tok", use_podman=False)
        mock_push.assert_called_once_with("reg/app:v1", use_podman=False, verbose=False)


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
            "sha512:" + "a" * 64,                    # wrong algorithm
            "sha256:" + "a" * 63,                    # too short
            "sha256:" + "a" * 65,                    # too long
            "sha256:" + "A" * 64,                    # uppercase hex
            "sha256:" + "g" * 64,                    # non-hex char
            "sha256:" + "a" * 32 + " " + "a" * 31,   # embedded space
            "Sha256:" + "a" * 64,                    # uppercase prefix
        ],
    )
    def test_rejects_malformed(self, bad):
        with pytest.raises(ValueError, match="Invalid digest"):
            validate_digest(bad)


class TestResolveDigest:
    @patch("kamiwaza_extensions.image_pusher.subprocess.run")
    def test_returns_digest_from_imagetools(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout=_VALID_DIGEST + "\n", stderr=""
        )
        digest = ImagePusher.resolve_digest("reg.test/app:v1")
        assert digest == _VALID_DIGEST
        cmd = mock_run.call_args[0][0]
        assert cmd[:4] == ["docker", "buildx", "imagetools", "inspect"]
        assert "reg.test/app:v1" in cmd
        assert "{{.Manifest.Digest}}" in cmd

    @patch("kamiwaza_extensions.image_pusher.subprocess.run")
    def test_inspect_failure_raises(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="manifest unknown"
        )
        with pytest.raises(ImagePushError, match="Digest resolution failed"):
            ImagePusher.resolve_digest("reg.test/app:v1")

    @patch("kamiwaza_extensions.image_pusher.subprocess.run")
    def test_unexpected_output_raises(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="not-a-digest\n", stderr=""
        )
        with pytest.raises(ImagePushError, match="Unexpected digest output"):
            ImagePusher.resolve_digest("reg.test/app:v1")

    @patch(
        "kamiwaza_extensions.image_pusher.subprocess.run",
        side_effect=FileNotFoundError(),
    )
    def test_missing_docker_raises(self, _mock_run):
        with pytest.raises(ImagePushError, match="docker not found"):
            ImagePusher.resolve_digest("reg.test/app:v1")
