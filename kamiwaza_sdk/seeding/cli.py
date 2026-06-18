# kamiwaza_sdk/seeding/cli.py

"""``kamiwaza-seed`` — a thin, generic seeding CLI over the SDK.

Each subcommand is a deterministic, parameterized wrapper around an SDK method:
explicit args in, created-resource ids out (as JSON). No random generation and
no environment-specific data — the UAT/nightly profile lives in the caller.

Example::

    export KAMIWAZA_API_KEY="$(kamiwaza-seed login --password-env ADMIN_PW --raw)"
    kamiwaza-seed create-workroom --name uat
    kamiwaza-seed install-extension --name kaizen --workroom-id <wid>
    URL="$(kamiwaza-seed resolve-kaizen-url --workroom-id <wid> --raw)"
    kamiwaza-seed register-external-model --protocol aws_bedrock \\
        --name claude --region us-east-1 \\
        --model-id anthropic.claude-3-sonnet-20240229-v1:0 \\
        --credential-env AWS_BEDROCK_CREDENTIAL_JSON
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Callable, Dict, Optional, Union

from ..exceptions import DeploymentFailedError
from ..schemas.kaizen import LLMConfig
from ..schemas.models.external_endpoint import (
    AWSBedrockChatEndpoint,
    AWSTranscribeEndpoint,
)
from ..services.kaizen import (
    AmbiguousExtensionError,
    ConversationError,
    wait_for_base_url,
)
from .client import build_client_from_env, scoped_client_for_workroom


def _non_negative_float(value: str) -> float:
    """argparse type for a seconds value that must be zero or positive."""
    try:
        parsed = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid float value: '{value}'")
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or a positive number of seconds")
    return parsed


def _parse_env(pairs: Optional[list[str]]) -> Optional[Dict[str, str]]:
    """Parse repeated ``KEY=VALUE`` args into an env dict."""
    if not pairs:
        return None
    env: Dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"--env expects KEY=VALUE, got '{pair}'")
        env[key] = value
    return env


def _read_env_secret(env_var: Optional[str], *, what: str) -> Optional[str]:
    """Read a secret from a named env var; exit if the var is named but empty."""
    if not env_var:
        return None
    value = os.environ.get(env_var)
    if not value:
        raise SystemExit(f"Env var '{env_var}' (for {what}) is unset or empty.")
    return value


def _read_credential(args: argparse.Namespace) -> str:
    """Resolve the inline credential JSON from an env var.

    Credentials are read from an env var only — never argv — so they don't leak
    into shell history or process listings.
    """
    value = _read_env_secret(args.credential_env, what="model credential")
    if value is None:
        raise SystemExit(
            "Provide credentials via --credential-env (env var holding the credential JSON)."
        )
    return value


def _client_for_workroom(client, workroom_id: Optional[str]):
    """Scope the client to a workroom (via enter) when one is requested."""
    if not workroom_id:
        return client
    return scoped_client_for_workroom(client, workroom_id)


# --- subcommand handlers ---------------------------------------------------


def cmd_login(args: argparse.Namespace, *, client) -> Optional[dict]:
    """Mint an access token via the password grant.

    The password is read from a named env var only — never argv — so it doesn't
    leak into shell history or process listings. ``--raw`` prints just the bare
    token so a caller can do ``export KAMIWAZA_API_KEY="$(... --raw)"``.
    """
    password = _read_env_secret(args.password_env, what="login password")
    if password is None:
        raise SystemExit(
            "Provide the password via --password-env (env var holding the password)."
        )
    token = client.auth.login_with_password(args.username, password).access_token
    if args.raw:
        print(token)
        return None
    return {"access_token": token}


def cmd_create_workroom(args: argparse.Namespace, *, client) -> dict:
    ids = []
    for i in range(args.count):
        name = args.name if args.count == 1 else f"{args.name}-{i + 1}"
        workroom = client.workrooms.create(
            name=name, workroom_type=args.type, description=args.description
        )
        ids.append(str(workroom.id))
    return {"workroom_ids": ids}


def cmd_register_external_model(args: argparse.Namespace, *, client) -> dict:
    credential = _read_credential(args)
    endpoint: Union[AWSBedrockChatEndpoint, AWSTranscribeEndpoint]
    if args.protocol == "aws_bedrock":
        if not args.model_id:
            raise SystemExit("--model-id is required for aws_bedrock.")
        endpoint = AWSBedrockChatEndpoint(
            region=args.region, model_id=args.model_id, endpoint_url=args.endpoint_url
        )
    else:
        if not args.s3_bucket:
            raise SystemExit("--s3-bucket is required for aws_transcribe.")
        endpoint = AWSTranscribeEndpoint(region=args.region, s3_bucket=args.s3_bucket)
    model = client.models.register_external_model(
        name=args.name,
        endpoint=endpoint,
        credential=credential,
        force_replace_credentials=args.force_replace,
        description=args.description,
    )
    return {"model_id": str(model.id)}


def cmd_install_extension(args: argparse.Namespace, *, client) -> dict:
    client = _client_for_workroom(client, args.workroom_id)
    deployment = client.apps.install_by_name(
        args.name,
        version=args.version,
        deployment_name=args.deployment_name,
        env_vars=_parse_env(args.env),
        workroom_id=args.workroom_id,
        sync_if_missing=not args.no_sync,
    )
    return {"deployment_id": str(deployment.id), "name": deployment.name}


def _resolve_model_id(client, model_id: Optional[str], name: Optional[str]) -> str:
    """Return the model id to deploy — given directly, or resolved by name."""
    if model_id:
        return model_id
    matches = [
        m for m in client.models.list_models() if (getattr(m, "name", None) or "") == name
    ]
    if not matches:
        raise SystemExit(f"No registered model named '{name}'.")
    if len(matches) > 1:
        # Deterministic: don't silently deploy an arbitrary one of a name collision.
        raise SystemExit(
            f"Multiple registered models named '{name}'; pass --model-id to disambiguate."
        )
    return str(matches[0].id)


def _deployment_endpoint(client, deployment_id) -> Optional[str]:
    """The OpenAI-compatible endpoint of a deployment, or None if not listed.

    Lists all active deployments to find the one we just created: only
    ``list_active_deployments`` computes the ``endpoint`` string (``get_deployment``
    does not), so don't "optimize" this into a by-id fetch.
    """
    for deployment in client.serving.list_active_deployments():
        if str(getattr(deployment, "id", "")) == str(deployment_id):
            return getattr(deployment, "endpoint", None)
    return None


def cmd_deploy_model(args: argparse.Namespace, *, client) -> dict:
    """Deploy a registered model so it's callable.

    External models (Bedrock/Transcribe) are only callable once deployed, so an
    agent can't bind to one until this runs. Returns the deployment id and its
    OpenAI-compatible endpoint (``…/runtime/models/<dep>/v1``) for the caller to
    pass to ``create-agent --llm-base-url``.
    """
    model_id = _resolve_model_id(client, args.model_id, args.name)
    try:
        deployment_id = client.serving.deploy_model(
            model_id=model_id,
            engine_name=args.engine_name,
            wait=not args.no_wait,
            timeout_seconds=args.timeout,
            poll_interval_seconds=args.poll_interval,
        )
    except (DeploymentFailedError, TimeoutError) as exc:
        # Convert the documented wait=True failure modes to a clean message
        # (mirrors cmd_resolve_kaizen_url) — a traceback is poor diagnostics for
        # the nightly profile.
        raise SystemExit(str(exc))
    if not deployment_id:
        raise SystemExit(f"Deploy request for model {model_id} was refused by the server.")
    result = {"deployment_id": str(deployment_id)}
    endpoint = _deployment_endpoint(client, deployment_id)
    if endpoint:
        result["endpoint"] = endpoint
    return result


def cmd_resolve_kaizen_url(args: argparse.Namespace, *, client) -> Optional[dict]:
    """Resolve a workroom's Kaizen ingress root, waiting for it to come up.

    The operator names per-workroom CRs ``<name>-<hash>`` and stamps each with
    its ``workroom_id``, so we enter the workroom (only then is its Kaizen
    listed) and match on base name + workroom — never another workroom's Kaizen
    or the first one we happen to see. ``--raw`` prints just the bare URL for
    ``URL="$(... --raw)"``; a readiness timeout exits non-zero.
    """
    client = _client_for_workroom(client, args.workroom_id)
    try:
        url = wait_for_base_url(
            client,
            args.name,
            workroom_id=args.workroom_id,
            timeout_seconds=args.timeout,
            poll_interval_seconds=args.poll_interval,
        )
    except (TimeoutError, AmbiguousExtensionError) as exc:
        raise SystemExit(str(exc))
    if args.raw:
        print(url)
        return None
    return {"kaizen_base_url": url}


def cmd_create_agent(args: argparse.Namespace, *, client) -> dict:
    llm = LLMConfig(
        model=args.model,
        provider=args.provider,
        endpoint_path=args.endpoint_path,
        base_url=args.llm_base_url,
    )
    api_key = _read_env_secret(args.llm_api_key_env, what="custom-endpoint LLM key")
    client = _client_for_workroom(client, args.workroom_id)
    agent = client.agents.create(
        base_url=args.kaizen_base_url,
        name=args.name,
        llm=llm,
        description=args.description,
        custom_instructions=args.custom_instructions,
        llm_api_key=api_key,
        workroom_id=args.workroom_id,
    )
    return {"agent_id": agent.id}


def cmd_create_conversation(args: argparse.Namespace, *, client) -> dict:
    client = _client_for_workroom(client, args.workroom_id)
    conversation = client.conversations.create(
        base_url=args.kaizen_base_url,
        agent_id=args.agent_id,
        title=args.title,
        max_iterations=args.max_iterations,
        ephemeral=args.ephemeral,
        workroom_id=args.workroom_id,
    )
    return {"conversation_id": conversation.id}


def cmd_chat(args: argparse.Namespace, *, client) -> Optional[dict]:
    """Send a prompt to an agent and return its reply (exercises it end to end).

    Opens a fresh conversation against ``--agent-id``, sends ``--message``, and
    (with a positive ``--timeout``) waits for the agent's reply — proving the
    agent can actually respond, not just that it was created. ``--raw`` prints
    only the reply text. Exits non-zero on an agent error, an empty reply, or a
    wait timeout (mirrors cmd_deploy_model / cmd_resolve_kaizen_url).
    """
    client = _client_for_workroom(client, args.workroom_id)
    conversation = client.conversations.create(
        base_url=args.kaizen_base_url,
        agent_id=args.agent_id,
        title=args.title,
        workroom_id=args.workroom_id,
    )
    try:
        client.conversations.wait_until_ready(
            conversation.id,
            base_url=args.kaizen_base_url,
            workroom_id=args.workroom_id,
            timeout_seconds=args.sandbox_timeout,
            poll_interval_seconds=args.poll_interval,
        )
        reply = client.conversations.chat(
            conversation.id,
            args.message,
            base_url=args.kaizen_base_url,
            workroom_id=args.workroom_id,
            timeout_seconds=args.timeout,
            poll_interval_seconds=args.poll_interval,
        )
    except (TimeoutError, ConversationError) as exc:
        raise SystemExit(str(exc))
    # timeout=0 is fire-and-forget (no reply to assert); only fault an empty
    # reply when a wait was requested.
    if args.timeout and not reply:
        raise SystemExit(f"Agent '{args.agent_id}' returned an empty reply.")
    if args.raw:
        if reply:
            print(reply)
        return None
    return {"conversation_id": conversation.id, "reply": reply}


def cmd_configure_m365(args: argparse.Namespace, *, client) -> dict:
    conn = client.connectors.create_m365(
        tenant_id=args.tenant_id,
        client_id=args.client_id,
        name=args.name,
        scopes=args.scope or None,
    )
    return {"connector_id": str(conn.id), "name": conn.name}


def cmd_import_skill(args: argparse.Namespace, *, client) -> dict:
    path = Path(args.file)
    result = client.skills.import_skill_package(
        filename=path.name, file_content=path.read_bytes()
    )
    skill_id = getattr(result, "id", None)
    return {"skill_id": str(skill_id) if skill_id is not None else None}


# --- parser ----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kamiwaza-seed", description="Generic Kamiwaza seeding operations."
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Cluster API root; defaults to KAMIWAZA_BASE_URL.",
    )
    sub = parser.add_subparsers(dest="command")

    # allow_abbrev=False: without it argparse accepts `--password` as a prefix of
    # `--password-env`, silently treating a secret typed on argv as an env-var name.
    p = sub.add_parser(
        "login",
        help="Mint an access token via the password grant.",
        allow_abbrev=False,
    )
    p.add_argument("--username", default="admin")
    p.add_argument(
        "--password-env",
        required=True,
        help="Env var holding the password (read from env, never argv).",
    )
    p.add_argument(
        "--raw",
        action="store_true",
        help="Print only the bare token (for KAMIWAZA_API_KEY=\"$(... --raw)\").",
    )
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("create-workroom", help="Create one or more workrooms.")
    p.add_argument("--name", required=True)
    p.add_argument("--type", default="persistent", help="ephemeral or persistent")
    p.add_argument("--description", default=None)
    p.add_argument("--count", type=int, default=1, help="Create N suffixed workrooms.")
    p.set_defaults(func=cmd_create_workroom)

    # allow_abbrev=False so `--credential` can't be accepted as a prefix of
    # `--credential-env`, which would route a secret through argv.
    p = sub.add_parser(
        "register-external-model",
        help="Register an external model.",
        allow_abbrev=False,
    )
    p.add_argument(
        "--protocol", required=True, choices=["aws_bedrock", "aws_transcribe"]
    )
    p.add_argument("--name", required=True)
    p.add_argument("--region", required=True)
    p.add_argument("--model-id", default=None, help="Bedrock model id (aws_bedrock).")
    p.add_argument("--s3-bucket", default=None, help="S3 bucket (aws_transcribe).")
    p.add_argument("--endpoint-url", default=None)
    p.add_argument("--description", default=None)
    p.add_argument(
        "--credential-env",
        required=True,
        help="Env var holding the credential JSON (read from env, never argv).",
    )
    p.add_argument(
        "--force-replace", action="store_true", help="Rotate an existing credential."
    )
    p.set_defaults(func=cmd_register_external_model)

    p = sub.add_parser("install-extension", help="Install a catalog extension by name.")
    p.add_argument("--name", required=True)
    p.add_argument("--version", default=None)
    p.add_argument("--deployment-name", default=None)
    p.add_argument("--workroom-id", default=None)
    p.add_argument("--env", action="append", help="KEY=VALUE (repeatable).")
    p.add_argument(
        "--no-sync", action="store_true", help="Don't import the catalog if missing."
    )
    p.set_defaults(func=cmd_install_extension)

    p = sub.add_parser(
        "deploy-model",
        help="Deploy a registered model so it's callable (external models need this before an agent can bind).",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--model-id", default=None, help="ID of a registered model to deploy.")
    g.add_argument("--name", default=None, help="Name of a registered model to resolve, then deploy.")
    p.add_argument(
        "--engine-name",
        default="external_chat",
        help="Serving engine (default external_chat; external_transcribe for Transcribe).",
    )
    p.add_argument(
        "--no-wait",
        action="store_true",
        help=(
            "Return the deployment id immediately instead of waiting for DEPLOYED. "
            "The endpoint is omitted from the output until the deployment is DEPLOYED."
        ),
    )
    p.add_argument("--timeout", type=int, default=600, help="Max seconds to wait for DEPLOYED.")
    p.add_argument("--poll-interval", type=float, default=5.0, help="Seconds between readiness polls.")
    p.set_defaults(func=cmd_deploy_model)

    p = sub.add_parser(
        "resolve-kaizen-url",
        help="Resolve a workroom's Kaizen ingress root (waits for ready).",
    )
    p.add_argument(
        "--workroom-id",
        required=True,
        help="Workroom whose Kaizen instance to resolve.",
    )
    p.add_argument(
        "--name",
        default="kaizen",
        help="Extension catalog/base name (default: kaizen).",
    )
    p.add_argument(
        "--raw",
        action="store_true",
        help='Print only the bare URL (for URL="$(... --raw)").',
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Max seconds to wait for the ingress to resolve (default: 300).",
    )
    p.add_argument(
        "--poll-interval",
        type=float,
        default=5.0,
        help="Seconds between resolve attempts (default: 5).",
    )
    p.set_defaults(func=cmd_resolve_kaizen_url)

    # allow_abbrev=False so `--llm-api-key` can't be accepted as a prefix of
    # `--llm-api-key-env`, which would route a secret through argv.
    p = sub.add_parser(
        "create-agent", help="Create a Kaizen agent.", allow_abbrev=False
    )
    p.add_argument(
        "--kaizen-base-url",
        required=True,
        help="Kaizen instance API root (per-workroom).",
    )
    p.add_argument("--name", required=True)
    p.add_argument("--model", required=True)
    p.add_argument(
        "--provider", default=None, help="'kamiwaza' for a platform deployment."
    )
    p.add_argument("--endpoint-path", default=None)
    p.add_argument(
        "--llm-base-url", default=None, help="Custom OpenAI-compatible endpoint."
    )
    p.add_argument("--description", default=None)
    p.add_argument("--custom-instructions", default=None)
    p.add_argument(
        "--llm-api-key-env", default=None, help="Env var holding a custom-endpoint key."
    )
    p.add_argument("--workroom-id", default=None)
    p.set_defaults(func=cmd_create_agent)

    p = sub.add_parser("create-conversation", help="Start a Kaizen conversation.")
    p.add_argument(
        "--kaizen-base-url",
        required=True,
        help="Kaizen instance API root (per-workroom).",
    )
    p.add_argument("--agent-id", required=True)
    p.add_argument("--title", default=None)
    p.add_argument("--max-iterations", type=int, default=500)
    p.add_argument("--ephemeral", action="store_true")
    p.add_argument("--workroom-id", default=None)
    p.set_defaults(func=cmd_create_conversation)

    p = sub.add_parser(
        "chat",
        help="Send a prompt to an agent and return its reply (exercises the agent end to end).",
    )
    p.add_argument(
        "--kaizen-base-url",
        required=True,
        help="Kaizen instance API root (per-workroom).",
    )
    p.add_argument("--agent-id", required=True)
    p.add_argument("--message", required=True, help="Prompt to send to the agent.")
    p.add_argument("--workroom-id", default=None)
    p.add_argument("--title", default=None, help="Conversation title (optional).")
    p.add_argument(
        "--raw",
        action="store_true",
        help='Print only the bare reply text (for REPLY="$(... --raw)").',
    )
    p.add_argument(
        "--timeout",
        type=_non_negative_float,
        default=60.0,
        help="Max seconds to wait for the reply (0 = fire-and-forget, don't wait).",
    )
    p.add_argument(
        "--sandbox-timeout",
        type=_non_negative_float,
        default=120.0,
        help="Max seconds to wait for the agent sandbox before sending the message.",
    )
    p.add_argument(
        "--poll-interval",
        type=float,
        default=3.0,
        help="Seconds between event polls while waiting (default: 3).",
    )
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("import-skill", help="Import a skill package (.zip).")
    p.add_argument("--file", required=True)
    p.set_defaults(func=cmd_import_skill)

    p = sub.add_parser(
        "configure-m365",
        help="Register the cluster-wide M365 connector (tenant + client id).",
    )
    p.add_argument("--tenant-id", required=True, help="Azure AD tenant ID (not secret).")
    p.add_argument("--client-id", required=True, help="App-registration client ID (not secret).")
    p.add_argument("--name", default="Microsoft 365")
    p.add_argument("--scope", action="append", help="Graph scope (repeatable; defaults to the standard set).")
    p.set_defaults(func=cmd_configure_m365)

    return parser


def main(
    argv: Optional[list[str]] = None,
    *,
    client_factory: Callable[..., object] = build_client_from_env,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.error("a subcommand is required")
    # The client targets the platform API root (global --base-url or env). The
    # Kaizen commands carry their own per-workroom --kaizen-base-url, passed to
    # the agent/conversation calls as a base_url override.
    client = client_factory(base_url=args.base_url)
    result = args.func(args, client=client)
    # Handlers that emit their own output (e.g. `login --raw`) return None.
    if result is not None:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
