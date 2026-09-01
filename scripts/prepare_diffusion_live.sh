#!/usr/bin/env bash
# Source-build the runtime used by live DiffusionEngine SDK acceptance.

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    echo "Source this script so its KAMIWAZA_TEST_DIFFUSION_* exports persist:" >&2
    echo "  source scripts/prepare_diffusion_live.sh" >&2
    exit 2
fi

_prepare_diffusion_live() {
    local sdk_root platform_root host_os backend
    local runtime_dir venv_python
    local image_target host_arch image_arch git_sha image_version
    local push_registry deploy_registry registry_api

    sdk_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || return
    platform_root="${KAMIWAZA_PLATFORM_ROOT:-${sdk_root}/../kamiwaza}"
    runtime_dir="${platform_root}/engine-images/diffusion/runtime"

    if [[ ! -x "${platform_root}/engine-images/diffusion/build.sh" ]]; then
        echo "Kamiwaza diffusion source not found at ${platform_root}." >&2
        echo "Set KAMIWAZA_PLATFORM_ROOT to the current kamiwaza checkout." >&2
        return 1
    fi

    host_os="$(uname -s)"
    backend="${KAMIWAZA_TEST_DIFFUSION_BACKEND:-}"

    if [[ "$host_os" == "Darwin" ]]; then
        backend="${backend:-auto}"
        case "$backend" in
            auto|mlx|mps) ;;
            *)
                echo "macOS source acceptance requires backend auto, mlx, or mps; got ${backend}." >&2
                return 1
                ;;
        esac

        command -v uv >/dev/null 2>&1 || {
            echo "uv is required to build the host diffusion environment." >&2
            return 1
        }
        venv_python="${platform_root}/diffusion-venv/bin/python"
        if [[ ! -x "$venv_python" ]]; then
            uv venv "${platform_root}/diffusion-venv" --python 3.12 || return
        fi
        uv pip install --python "$venv_python" \
            -r "${runtime_dir}/requirements-minimal.txt" \
            -r "${runtime_dir}/requirements.txt" || return
        "$venv_python" -c \
            'import diffusers, torch, transformers, uvicorn; assert torch.backends.mps.is_available(), "Metal/MPS is unavailable"' \
            || return

        export KAMIWAZA_TEST_DIFFUSION_BACKEND="$backend"
        echo "Prepared host diffusion runtime: ${venv_python} (${backend})"
        return 0
    fi

    if [[ "$host_os" != "Linux" ]]; then
        echo "Unsupported live diffusion host OS: ${host_os}." >&2
        return 1
    fi

    backend="${backend:-cpu}"
    export KAMIWAZA_TEST_DIFFUSION_BACKEND="$backend"
    if [[ -n "${KAMIWAZA_TEST_DIFFUSION_IMAGE:-}" ]]; then
        echo "Using operator-supplied diffusion image: ${KAMIWAZA_TEST_DIFFUSION_IMAGE}"
        return 0
    fi

    case "$backend" in
        auto|cpu) image_target="cpu" ;;
        cuda|nvidia) image_target="nvidia" ;;
        *)
            echo "Backend ${backend} needs an operator-built runtime image." >&2
            echo "Set KAMIWAZA_TEST_DIFFUSION_IMAGE to its cluster-pullable reference." >&2
            return 1
            ;;
    esac

    command -v docker >/dev/null 2>&1 || {
        echo "Docker with buildx is required for Linux source-runtime acceptance." >&2
        return 1
    }
    registry_api="${KAMIWAZA_DIFFUSION_REGISTRY_API:-http://localhost:5001}"
    curl -fsS "${registry_api%/}/v2/" >/dev/null || {
        echo "KZUAT registry is not reachable at ${registry_api%/}/v2/." >&2
        return 1
    }

    host_arch="$(uname -m)"
    case "$host_arch" in
        x86_64|amd64) image_arch="amd64" ;;
        aarch64|arm64) image_arch="arm64" ;;
        *)
            echo "Unsupported container architecture: ${host_arch}." >&2
            return 1
            ;;
    esac

    git_sha="$(git -C "$platform_root" rev-parse --short=12 HEAD)" || return
    image_version="uat-${git_sha}-${image_arch}"
    push_registry="${KAMIWAZA_DIFFUSION_PUSH_REGISTRY:-localhost:5001/kamiwaza-uat}"
    deploy_registry="${KAMIWAZA_DIFFUSION_DEPLOY_REGISTRY:-host.docker.internal:5001/kamiwaza-uat}"

    (
        cd "${platform_root}/engine-images/diffusion" || exit
        REGISTRY="$push_registry" \
        DIFFUSION_ENGINE_VERSION="$image_version" \
        ./build.sh "$image_target" --push --platform "linux/${image_arch}"
    ) || return

    export KAMIWAZA_TEST_DIFFUSION_IMAGE="${deploy_registry}/diffusion-engine:${image_target}-${image_version}"
    echo "Prepared source diffusion image: ${KAMIWAZA_TEST_DIFFUSION_IMAGE} (${backend})"
}

_prepare_diffusion_live
_prepare_diffusion_live_rc=$?
unset -f _prepare_diffusion_live
return "$_prepare_diffusion_live_rc"
