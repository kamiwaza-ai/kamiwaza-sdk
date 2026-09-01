from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from uuid import uuid4

from tests.integration.diffusion_live_support import (
    OTTER_AIRPLANE_PROMPT,
    LiveDiffusionDeployment,
    generated_png_payloads,
    masked_edit_fixture,
    png_dimensions,
    save_diffusion_evidence,
    unmasked_pixel_change_fraction,
)
from tests.integration.diffusion_targets import DiffusionTarget


def _target() -> DiffusionTarget:
    return DiffusionTarget(
        case="qwen-image-edit-mask",
        repo_id="Qwen/Qwen-Image-Edit-2509",
        family="qwen-image-edit",
        backend="mps",
        image=None,
        fake=False,
        size="64x64",
        steps=4,
        guidance_scale=1.0,
        timeout_seconds=3600,
    )


def test_mollick_prompt_is_the_canonical_simple_benchmark() -> None:
    assert OTTER_AIRPLANE_PROMPT == "otter on a plane using wifi"


def test_masked_edit_fixture_is_valid_size_matched_png_pair() -> None:
    source, mask = masked_edit_fixture("128x96")

    assert png_dimensions(source) == (128, 96)
    assert png_dimensions(mask) == (128, 96)
    assert source != mask


def test_generated_payload_validation_accepts_expected_dimensions() -> None:
    source, _mask = masked_edit_fixture("64x64")
    response = SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(source).decode("ascii"))]
    )

    assert generated_png_payloads(response, "64x64") == [source]


def test_unmasked_pixel_change_fraction_measures_actual_png_pixels() -> None:
    source, mask = masked_edit_fixture("64x64")

    assert unmasked_pixel_change_fraction(source, mask, source) == 0.0
    assert unmasked_pixel_change_fraction(source, mask, mask) > 0.9


def test_evidence_writer_persists_pngs_and_redacted_manifest(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("KAMIWAZA_TEST_DIFFUSION_ARTIFACT_DIR", str(tmp_path))
    source, mask = masked_edit_fixture("64x64")
    encoded = base64.b64encode(source).decode("ascii")
    response = SimpleNamespace(
        created=1729,
        data=[SimpleNamespace(b64_json=encoded)],
        model_extra={
            "engine": "diffusion",
            "family": "qwen-image-edit",
            "mask_applied": True,
            "images": [
                {
                    "seed": 1729,
                    "width": 64,
                    "height": 64,
                    "b64_json": encoded,
                    "url": f"data:image/png;base64,{encoded}",
                }
            ],
        },
    )
    live = LiveDiffusionDeployment(
        client=SimpleNamespace(),
        target=_target(),
        deployment_id=uuid4(),
        served_model_id="Qwen/Qwen-Image-Edit-2509",
        openai_client=SimpleNamespace(),
    )

    manifest_path = save_diffusion_evidence(
        live,
        case="qwen-image-edit-mask",
        prompt="edit only the mask",
        response=response,
        generated_payloads=[source],
        request_controls={"seed": 1729},
        source_payload=source,
        mask_payload=mask,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "kamiwaza-sdk-diffusion-evidence.v1"
    assert manifest["target"]["repo_id"] == "Qwen/Qwen-Image-Edit-2509"
    assert manifest["response"]["metadata"]["mask_applied"] is True
    assert manifest["response"]["metadata"]["images"] == [
        {"height": 64, "seed": 1729, "width": 64}
    ]
    assert {artifact["role"] for artifact in manifest["artifacts"]} == {
        "generated",
        "source",
        "mask",
    }
    assert (manifest_path.parent / "generated-00.png").read_bytes() == source
    assert (manifest_path.parent / "source.png").read_bytes() == source
    assert (manifest_path.parent / "mask.png").read_bytes() == mask
    assert encoded not in manifest_path.read_text(encoding="utf-8")
