"""Tests for ImagePusher."""

from unittest.mock import MagicMock, patch

import pytest

from kamiwaza_extensions.image_pusher import ImagePusher, ImagePushError


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
