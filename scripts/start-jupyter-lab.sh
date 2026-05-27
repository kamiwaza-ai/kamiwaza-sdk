#!/bin/bash
# Launch a local JupyterLab pointed at the SDK example notebooks.
#
# Prerequisites:
#   pip install kamiwaza-sdk[notebook]    # installs jupyter + ipykernel + friends
#
# Usage:
#   ./scripts/start-jupyter-lab.sh
#
# By default, points at the cluster reachable on https://localhost/api (the
# kamiwaza dev k0s-lima default). Override via KAMIWAZA_BASE_URL env var:
#   KAMIWAZA_BASE_URL=https://my-cluster.example.com/api ./scripts/start-jupyter-lab.sh
#
# See docs/jupyter-quickstart.md for the full local-dev workflow.

set -ex

# Ensure protobuf uses pure Python (avoids native-binary version skew across envs).
export PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION='python'

# Resolve repo root regardless of where the script is invoked from.
script_dir=$(dirname "${BASH_SOURCE[0]}")
repo_dir=$(realpath "${script_dir}/..")
examples_dir="${repo_dir}/examples"

# Restart any running JupyterLab to avoid port collisions on rerun.
jupyter_pids=$(pgrep -f "jupyter-lab" || true)
if [ -n "$jupyter_pids" ]; then
    for pid in $jupyter_pids; do
        echo "Killing existing JupyterLab process with PID: $pid"
        kill "$pid"
        while kill -0 "$pid" 2> /dev/null; do
            echo "Waiting for JupyterLab process PID $pid to terminate..."
            sleep 1
        done
    done
else
    echo "No existing JupyterLab process found."
fi

# Use the tracked Jupyter configuration directory (examples/.jupyter/).
# This config is version-controlled and is the source of truth.
jupyter_config_dir="${examples_dir}/.jupyter"
jupyter_config_file="${jupyter_config_dir}/jupyter_lab_config.py"

if [ ! -f "${jupyter_config_file}" ]; then
    echo "ERROR: Jupyter configuration file not found at ${jupyter_config_file}"
    echo "This file should be tracked in git. Please restore it from version control."
    exit 1
fi

echo "Jupyter configuration file content:"
cat "${jupyter_config_file}"

# Surface the configured kamiwaza endpoint so notebook code can read it via
# os.environ['KAMIWAZA_BASE_URL'].
export KAMIWAZA_BASE_URL="${KAMIWAZA_BASE_URL:-https://localhost/api}"
echo "KAMIWAZA_BASE_URL=${KAMIWAZA_BASE_URL}"

# Launch JupyterLab from the examples directory so notebook paths resolve naturally.
cd "${examples_dir}"

export JUPYTER_CONFIG_DIR="${jupyter_config_dir}"
echo "Starting JupyterLab..."
nohup jupyter lab --ip=0.0.0.0 --port=8890 --log-level=ERROR --no-browser > "${repo_dir}/jupyter_lab.log" 2>&1 &
sleep 4 # Give it a moment to start and write to the log

echo ""
echo "JupyterLab started; tail ${repo_dir}/jupyter_lab.log for status."
echo "Open: http://localhost:8890/lab"
