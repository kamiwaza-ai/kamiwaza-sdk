"""Federation trust lifecycle operations exposed through ``client.cluster``."""

from __future__ import annotations

from typing import Any, Dict


class FederationTrustLifecycleMixin:
    """Peer-proven PSK rotation, CA refresh, and reconnect operations."""

    client: Any

    def rotate_preshared_key(self, federation_id: Any) -> Dict[str, Any]:
        """Stage K2 while K1 remains active and return K2 exactly once.

        Carry ``preshared_key`` and ``fingerprint`` to the peer operator out of
        band. If the one-time response is lost, inspect status and abort the
        still-STAGED generation before retrying; never mint over an unknown
        in-flight key.
        """
        result: Dict[str, Any] = self.client._request(
            "POST", f"/cluster/federations/{federation_id}/rotate-preshared-key"
        )
        return result

    def adopt_preshared_key_rotation(
        self,
        federation_id: Any,
        *,
        preshared_key: str,
        fingerprint: str,
    ) -> Dict[str, Any]:
        """Stage the peer's out-of-band K2 without changing the active signer."""
        result: Dict[str, Any] = self.client._request(
            "POST",
            f"/cluster/federations/{federation_id}/adopt-preshared-key-rotation",
            json={"preshared_key": preshared_key, "fingerprint": fingerprint},
        )
        return result

    def get_key_rotation_status(self, federation_id: Any) -> Dict[str, Any]:
        """Return the rotation phase, generation, and non-secret fingerprints."""
        result: Dict[str, Any] = self.client._request(
            "GET", f"/cluster/federations/{federation_id}/key-rotation-status"
        )
        return result

    def activate_key_rotation(
        self, federation_id: Any, *, fingerprint: str
    ) -> Dict[str, Any]:
        """Activate K2 only after the server obtains a peer-signed K2 proof."""
        result: Dict[str, Any] = self.client._request(
            "POST",
            f"/cluster/federations/{federation_id}/activate-key-rotation",
            json={"fingerprint": fingerprint},
        )
        return result

    def complete_key_rotation(
        self, federation_id: Any, *, fingerprint: str
    ) -> Dict[str, Any]:
        """Retire K1 peer-first using the stage-time K2 fingerprint.

        A caller acknowledgement is intentionally insufficient. Core proves
        that the peer activated K2 and retired K1 before narrowing the local
        acceptance window.
        """
        result: Dict[str, Any] = self.client._request(
            "POST",
            f"/cluster/federations/{federation_id}/complete-key-rotation",
            json={"fingerprint": fingerprint},
        )
        return result

    def abort_key_rotation(
        self,
        federation_id: Any,
        *,
        fingerprint: str,
        generation: str,
    ) -> Dict[str, Any]:
        """Discard exactly one still-STAGED K2 generation, leaving K1 active."""
        result: Dict[str, Any] = self.client._request(
            "POST",
            f"/cluster/federations/{federation_id}/abort-key-rotation",
            json={"fingerprint": fingerprint, "generation": generation},
        )
        return result

    def refresh_peer_ca(
        self,
        federation_id: Any,
        *,
        ca_pem: str,
        acknowledged_fingerprint: str,
    ) -> Dict[str, Any]:
        """Replace peer CA material after an out-of-band fingerprint check."""
        result: Dict[str, Any] = self.client._request(
            "POST",
            f"/cluster/federations/{federation_id}/refresh-peer-ca",
            json={
                "ca_pem": ca_pem,
                "acknowledged_fingerprint": acknowledged_fingerprint,
            },
        )
        return result

    def reconnect_federation(self, federation_id: Any) -> Dict[str, Any]:
        """Re-admit a locally disconnected federation without re-pairing."""
        result: Dict[str, Any] = self.client._request(
            "POST", f"/cluster/federations/{federation_id}/reconnect"
        )
        return result
