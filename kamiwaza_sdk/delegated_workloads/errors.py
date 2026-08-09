"""Safe delegated-workload protocol failures."""

from kamiwaza_sdk.exceptions import KamiwazaError


class DPoPNonceRequired(KamiwazaError):
    """A nonce challenge that was not eligible for another safe retry."""

    def __init__(self, nonce: str) -> None:
        super().__init__(
            "a fresh DPoP nonce is required",
            status_code=401,
            body={
                "code": "dpop_nonce_required",
                "retry_classification": "nonce_required",
            },
        )
        self.nonce = nonce
