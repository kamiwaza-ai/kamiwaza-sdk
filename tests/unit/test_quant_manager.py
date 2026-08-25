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


def test_base_k_quantization_selects_all_matching_variants_case_insensitively() -> None:
    manager = QuantizationManager()
    files = [
        SimpleNamespace(name="model-q4_k_m.gguf"),
        SimpleNamespace(name="MODEL-Q4_K_S.GGUF"),
        SimpleNamespace(name="model-Q5_K_M.gguf"),
    ]

    selected = manager.filter_files_by_quantization(
        files,
        "Q4_K",
        apply_fallback=False,
    )

    assert selected == files[:2]


def test_specific_variant_detection_preserves_sharded_filename_variant() -> None:
    manager = QuantizationManager()

    assert manager.detect_quantization("model-Q4_K_M-00001-of-00002.gguf") == "q4_k_m"


def test_multiple_quantizations_distinguishes_k_variants() -> None:
    manager = QuantizationManager()
    files = [
        SimpleNamespace(name="model-Q4_K_M.gguf"),
        SimpleNamespace(name="model-Q4_K_S.gguf"),
    ]

    assert manager.has_multiple_quantizations(files) is True


def test_base_quantization_fallback_is_preserved() -> None:
    manager = QuantizationManager()
    files = [SimpleNamespace(name="model-Q5_K_M.gguf")]

    selected = manager.filter_files_by_quantization(files, "q4_k")

    assert selected == files


def test_no_fallback_returns_no_nonmatching_files() -> None:
    manager = QuantizationManager()
    files = [SimpleNamespace(name="model-Q5_K_M.gguf")]

    assert (
        manager.filter_files_by_quantization(
            files,
            "q4_k",
            apply_fallback=False,
        )
        == []
    )


def test_specific_variant_does_not_fallback_to_another_variant() -> None:
    manager = QuantizationManager()
    files = [SimpleNamespace(name="model-Q4_K_S.gguf")]

    assert manager.filter_files_by_quantization(files, "q4_k_m") == []
