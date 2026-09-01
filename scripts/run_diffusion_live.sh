#!/usr/bin/env bash
# Prepare, run, and clean up source-based DiffusionEngine SDK acceptance.

set -uo pipefail

sdk_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || exit

_cleanup_diffusion_live_on_exit() {
    if type cleanup_diffusion_live >/dev/null 2>&1; then
        cleanup_diffusion_live || {
            echo "Failed to restore the diffusion validation environment." >&2
            return 1
        }
    fi
}

trap _cleanup_diffusion_live_on_exit EXIT
source "${sdk_root}/scripts/prepare_diffusion_live.sh" || exit

run_stamp="$(date +%Y%m%d-%H%M%S)" || exit
export KAMIWAZA_TEST_DIFFUSION_ARTIFACT_DIR="${KAMIWAZA_TEST_DIFFUSION_ARTIFACT_DIR:-/tmp/kzsdk-diffusion-evidence-${run_stamp}}"
echo "Diffusion evidence directory: ${KAMIWAZA_TEST_DIFFUSION_ARTIFACT_DIR}"

if [[ $# -eq 0 ]]; then
    junit_path="${KAMIWAZA_DIFFUSION_JUNIT:-/tmp/kzsdk-diffusion-live-${run_stamp}.xml}"
    pytest_args=(
        -m "integration and live and diffusion"
        tests/integration/test_diffusion_live.py
        tests/integration/test_diffusion_qwen_live.py
        tests/integration/test_diffusion_qwen_split_live.py
        -v
        --tb=short
        --junitxml="$junit_path"
    )
    echo "Diffusion JUnit: ${junit_path}"
else
    pytest_args=("$@")
fi

(
    cd "$sdk_root" || exit
    uv run pytest "${pytest_args[@]}"
)
pytest_rc=$?

cleanup_rc=0
cleanup_diffusion_live || cleanup_rc=$?
trap - EXIT
unset -f _cleanup_diffusion_live_on_exit

if [[ "$pytest_rc" -ne 0 ]]; then
    exit "$pytest_rc"
fi
exit "$cleanup_rc"
