#!/usr/bin/env bash
# Source-build the runtime used by live DiffusionEngine SDK acceptance.

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    echo "Source this script so its KAMIWAZA_TEST_DIFFUSION_* exports persist:" >&2
    echo "  source scripts/prepare_diffusion_live.sh" >&2
    exit 2
fi

cleanup_diffusion_live() {
    local current_images current_normalized expected_normalized patch_json

    if [[ "${_KAMIWAZA_DIFFUSION_CATALOG_PATCHED:-0}" != "1" ]]; then
        return 0
    fi

    current_images="$(
        kubectl -n "${_KAMIWAZA_DIFFUSION_CATALOG_NAMESPACE}" \
            get configmap "${_KAMIWAZA_DIFFUSION_CATALOG_CONFIGMAP}" \
            -o jsonpath='{.data.KAMIWAZA_INFERENCE_IMAGES}'
    )" || return
    current_normalized="$(jq -cS . <<<"$current_images")" || return
    expected_normalized="$(
        jq -cS . <<<"${_KAMIWAZA_DIFFUSION_CATALOG_EXPECTED}"
    )" || return
    if [[ "$current_normalized" != "$expected_normalized" ]]; then
        echo "Refusing to restore the diffusion image catalog because it changed after preparation." >&2
        echo "Restore KAMIWAZA_INFERENCE_IMAGES on ${_KAMIWAZA_DIFFUSION_CATALOG_CONFIGMAP} manually." >&2
        return 1
    fi

    patch_json="$(
        jq -cn --arg images "${_KAMIWAZA_DIFFUSION_CATALOG_ORIGINAL}" \
            '{data:{KAMIWAZA_INFERENCE_IMAGES:$images}}'
    )" || return
    kubectl -n "${_KAMIWAZA_DIFFUSION_CATALOG_NAMESPACE}" \
        patch configmap "${_KAMIWAZA_DIFFUSION_CATALOG_CONFIGMAP}" \
        --type merge -p "$patch_json" || return
    kubectl -n "${_KAMIWAZA_DIFFUSION_CATALOG_NAMESPACE}" \
        rollout restart "deployment/${_KAMIWAZA_DIFFUSION_CATALOG_SCHEDULER}" || return
    kubectl -n "${_KAMIWAZA_DIFFUSION_CATALOG_NAMESPACE}" \
        rollout status "deployment/${_KAMIWAZA_DIFFUSION_CATALOG_SCHEDULER}" \
        --timeout="${KAMIWAZA_DIFFUSION_ROLLOUT_TIMEOUT:-180s}" || return

    unset _KAMIWAZA_DIFFUSION_CATALOG_PATCHED
    unset _KAMIWAZA_DIFFUSION_CATALOG_NAMESPACE
    unset _KAMIWAZA_DIFFUSION_CATALOG_CONFIGMAP
    unset _KAMIWAZA_DIFFUSION_CATALOG_SCHEDULER
    unset _KAMIWAZA_DIFFUSION_CATALOG_ORIGINAL
    unset _KAMIWAZA_DIFFUSION_CATALOG_EXPECTED
    echo "Restored the cluster diffusion image catalog."
}

_prepare_diffusion_live() {
    local sdk_root platform_root host_os backend
    local runtime_dir venv_python
    local image_target host_arch image_arch git_sha image_version push_image
    local push_registry deploy_registry registry_api docker_config
    local configure_cluster namespace configmap scheduler original_images updated_images
    local original_normalized updated_normalized patch_json

    if [[ "${_KAMIWAZA_DIFFUSION_CATALOG_PATCHED:-0}" == "1" ]]; then
        echo "A diffusion image catalog override is already active in this shell." >&2
        echo "Run cleanup_diffusion_live before preparing another runtime." >&2
        return 1
    fi

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
        if [[ "$backend" == "auto" || "$backend" == "mlx" || "$backend" == "mps" ]]; then
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
    elif [[ "$host_os" == "Linux" ]]; then
        backend="${backend:-cpu}"
    else
        echo "Unsupported live diffusion host OS: ${host_os}." >&2
        return 1
    fi

    export KAMIWAZA_TEST_DIFFUSION_BACKEND="$backend"
    if [[ -n "${KAMIWAZA_TEST_DIFFUSION_IMAGE:-}" ]]; then
        echo "Using operator-supplied diffusion image: ${KAMIWAZA_TEST_DIFFUSION_IMAGE}"
    else
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
            echo "Docker with buildx is required for container-runtime acceptance." >&2
            return 1
        }
        registry_api="${KAMIWAZA_DIFFUSION_REGISTRY_API:-http://localhost:5001}"
        curl -fsS "${registry_api%/}/v2/" >/dev/null || {
            echo "KZUAT registry is not reachable at ${registry_api%/}/v2/." >&2
            return 1
        }

        docker_config="${KAMIWAZA_DIFFUSION_DOCKER_CONFIG:-}"
        if [[ -z "$docker_config" && -f "${HOME}/.config/kamiwaza/docker-chainguard/config.json" ]]; then
            docker_config="${HOME}/.config/kamiwaza/docker-chainguard"
        fi
        if [[ -n "$docker_config" ]]; then
            [[ -f "${docker_config}/config.json" ]] || {
                echo "Diffusion Docker config has no config.json: ${docker_config}." >&2
                return 1
            }
            DOCKER_CONFIG="$docker_config" docker manifest inspect \
                cgr.dev/kamiwaza/python:3.12-dev >/dev/null || {
                echo "Chainguard pull auth failed via ${docker_config}." >&2
                echo "Refresh it with deploy/scripts/chainlogin.sh before retrying." >&2
                return 1
            }
        else
            docker manifest inspect cgr.dev/kamiwaza/python:3.12-dev >/dev/null || {
                echo "Chainguard pull auth failed and no dedicated Docker config was found." >&2
                echo "Run deploy/scripts/chainlogin.sh or set KAMIWAZA_DIFFUSION_DOCKER_CONFIG." >&2
                return 1
            }
        fi

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
        push_image="${push_registry}/diffusion-engine:${image_target}-${image_version}"

        (
            cd "${platform_root}/engine-images/diffusion" || exit
            if [[ -n "$docker_config" ]]; then
                export DOCKER_CONFIG="$docker_config"
            fi
            REGISTRY="$push_registry" \
            DIFFUSION_ENGINE_VERSION="$image_version" \
            ./build.sh "$image_target" --platform "linux/${image_arch}" || exit
            docker push "$push_image"
        ) || return

        export KAMIWAZA_TEST_DIFFUSION_IMAGE="${deploy_registry}/diffusion-engine:${image_target}-${image_version}"
    fi

    configure_cluster="$(
        printf '%s' "${KAMIWAZA_DIFFUSION_CONFIGURE_CLUSTER:-true}" \
            | tr '[:upper:]' '[:lower:]'
    )" || return
    case "$configure_cluster" in
        0|false|no|off) ;;
        1|true|yes|on)
            command -v kubectl >/dev/null 2>&1 || {
                echo "kubectl is required to install the trusted diffusion image catalog entry." >&2
                return 1
            }
            command -v jq >/dev/null 2>&1 || {
                echo "jq is required to install the trusted diffusion image catalog entry." >&2
                return 1
            }
            namespace="${KAMIWAZA_DIFFUSION_K8S_NAMESPACE:-kamiwaza}"
            configmap="${KAMIWAZA_DIFFUSION_K8S_CONFIGMAP:-core-config}"
            scheduler="${KAMIWAZA_DIFFUSION_K8S_SCHEDULER:-core-scheduler}"
            original_images="$(
                kubectl -n "$namespace" get configmap "$configmap" \
                    -o jsonpath='{.data.KAMIWAZA_INFERENCE_IMAGES}'
            )" || return
            original_normalized="$(jq -cS . <<<"$original_images")" || {
                echo "${configmap} has invalid KAMIWAZA_INFERENCE_IMAGES JSON." >&2
                return 1
            }
            updated_images="$(
                jq -c --arg image "${KAMIWAZA_TEST_DIFFUSION_IMAGE}" \
                    '.diffusion = {"default": $image}' <<<"$original_images"
            )" || return
            updated_normalized="$(jq -cS . <<<"$updated_images")" || return
            if [[ "$original_normalized" != "$updated_normalized" ]]; then
                patch_json="$(
                    jq -cn --arg images "$updated_images" \
                        '{data:{KAMIWAZA_INFERENCE_IMAGES:$images}}'
                )" || return
                kubectl -n "$namespace" patch configmap "$configmap" \
                    --type merge -p "$patch_json" || return

                _KAMIWAZA_DIFFUSION_CATALOG_PATCHED=1
                _KAMIWAZA_DIFFUSION_CATALOG_NAMESPACE="$namespace"
                _KAMIWAZA_DIFFUSION_CATALOG_CONFIGMAP="$configmap"
                _KAMIWAZA_DIFFUSION_CATALOG_SCHEDULER="$scheduler"
                _KAMIWAZA_DIFFUSION_CATALOG_ORIGINAL="$original_images"
                _KAMIWAZA_DIFFUSION_CATALOG_EXPECTED="$updated_images"

                kubectl -n "$namespace" rollout restart "deployment/${scheduler}" || return
                kubectl -n "$namespace" rollout status "deployment/${scheduler}" \
                    --timeout="${KAMIWAZA_DIFFUSION_ROLLOUT_TIMEOUT:-180s}" || return
                echo "Installed temporary trusted diffusion image catalog entry."
            fi
            ;;
        *)
            echo "KAMIWAZA_DIFFUSION_CONFIGURE_CLUSTER must be true or false." >&2
            return 1
            ;;
    esac

    echo "Prepared diffusion image: ${KAMIWAZA_TEST_DIFFUSION_IMAGE} (${backend})"
}

_prepare_diffusion_live
_prepare_diffusion_live_rc=$?
unset -f _prepare_diffusion_live
return "$_prepare_diffusion_live_rc"
