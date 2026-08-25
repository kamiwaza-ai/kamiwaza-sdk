from types import SimpleNamespace

from kamiwaza_sdk.utils.quant_manager import QuantizationManager


def test_specific_k_quantization_variant_is_detected_in_full() -> None:
    manager = QuantizationManager()

    assert manager.detect_quantization("model-Q4_K_M.gguf") == "q4_k_m"


def test_specific_k_quantization_variant_selects_only_exact_variant() -> None:
    manager = QuantizationManager()
    files = [
        SimpleNamespace(name="model-Q4_K_M.gguf"),
        SimpleNamespace(name="model-Q4_K_S.gguf"),
    ]

    selected = manager.filter_files_by_quantization(
        files,
        "q4_k_m",
        apply_fallback=False,
    )

    assert [item.name for item in selected] == ["model-Q4_K_M.gguf"]
