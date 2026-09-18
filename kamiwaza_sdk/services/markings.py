"""Optional configured markings; interpretation always remains server-side."""

from ..schemas.markings import ComposedMarkings, Marking, MarkingsConfig, ParsedMarking
from .base_service import BaseService


class MarkingsService(BaseService):
    """Read configured choices and request provider normalization/presentation."""

    def config(self) -> MarkingsConfig:
        return MarkingsConfig.model_validate(
            self.client.get("/security/markings/config")
        )

    def parse(self, text: str, *, default_for: str | None = None) -> ParsedMarking:
        payload = {"text": text}
        if default_for is not None:
            payload["default_for"] = default_for
        return ParsedMarking.model_validate(
            self.client.post("/security/markings/parse", json=payload)
        )

    def compose(
        self, markings: list[Marking], *, surface: str = "banner"
    ) -> ComposedMarkings:
        return ComposedMarkings.model_validate(
            self.client.post(
                "/security/markings/compose",
                json={
                    "markings": [
                        marking.model_dump(mode="json") for marking in markings
                    ],
                    "surface": surface,
                },
            )
        )
