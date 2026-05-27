# ruff: noqa: F821, E402
# type: ignore
# Jupyter config file - 'c' is a magic variable provided by Jupyter at runtime

# Base URL for Jupyter Server (all routes start with /lab)
# This keeps Jupyter API at /lab/api/* avoiding conflicts with Kamiwaza's /api/*
# Traefik must NOT strip the /lab prefix (lab-strip middleware removed)
c.ServerApp.base_url = "/lab"

import os

os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"

# Authentication delegated to Traefik
c.ServerApp.token = ""
c.ServerApp.password = ""
