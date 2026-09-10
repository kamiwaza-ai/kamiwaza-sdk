"""Shared image-bearing env names for compose transforms."""

# Env vars whose value is a single container image reference (possibly
# wrapped in a ``${NAME:-default}`` shell default). Mirrors the platform's
# kamiwaza.serving.garden.extensions.image_rewrite.IMAGE_ENV_NAMES.
IMAGE_ENV_NAMES = frozenset({"AGENT_SERVER_IMAGE"})
# Env vars whose value is a comma-separated list of image *prefixes* (not
# full refs) — the sandbox controller's allowlist. Must be aligned in
# lockstep with AGENT_SERVER_IMAGE or the controller rejects the agent.
IMAGE_PREFIX_ENV_NAMES = frozenset({"SANDBOX_ALLOWED_IMAGE_PREFIXES"})
