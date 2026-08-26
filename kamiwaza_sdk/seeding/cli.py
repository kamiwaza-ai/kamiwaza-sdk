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
    kamiwaza-seed bind-chat-model --kaizen-base-url "$URL" \\
        --workroom-id <wid> --deployment-id <dep>
    kamiwaza-seed bind-embedding-model --kaizen-base-url "$URL" \\
        --workroom-id <wid>
    kamiwaza-seed create-agent --kaizen-base-url "$URL" --workroom-id <wid> \\
        --name uat-agent --persona "Answer UAT questions."

Canonical Kaizen binds a model per instance, so ``bind-chat-model`` — not
``create-agent`` — is what gives a seeded agent a backing model.

``bind-embedding-model`` is the same step for retrieval, and is just as
required: with no embedding deployment selected, semantic search falls back to
lexical matching and only says so in a log line, so a seed run that skips it
produces an instance that looks healthy and retrieves badly. It takes no
``--deployment-id`` above because nothing in a seed run deploys an embedding
model — the one to bind is whatever the environment already serves, so the
command selects by capability from the instance's own inventory rather than by
a model name that would differ between an offline box and a hosted one.

Legacy Kaizen still binds per agent. Selecting it means naming it in **both**
places — the resolve and the create — or the resolve finds one product while
the create speaks the other contract, which is exactly the server-side 422
this split exists to remove::

    URL="$(kamiwaza-seed resolve-kaizen-url --name kaizen-legacy \\
        --workroom-id <wid> --raw)"
    kamiwaza-seed create-agent --kaizen-base-url "$URL" --workroom-id <wid> \\
        --extension-name kaizen-legacy --name uat-agent --model openai/foo \\
        --provider kamiwaza --endpoint-path /runtime/models/<dep>/v1 \\
        --llm-api-key-env KAMIWAZA_API_KEY
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Callable, Dict, NamedTuple, Optional, Sequence, Tuple, Union

from ..exceptions import DeploymentFailedError, KamiwazaError
from ..schemas.kaizen import AgentDefinition, LLMConfig
from ..schemas.models.external_endpoint import (
    AWSBedrockChatEndpoint,
    AWSTranscribeEndpoint,
)
from ..services.kaizen import (
    AGENT_CONTRACT_CANONICAL,
    CANONICAL_EXTENSION_NAME,
    LEGACY_EXTENSION_NAME,
    AmbiguousExtensionError,
    ConversationError,
    agent_contract_for_extension,
    wait_for_base_url,
)
from . import kaizen_turns
from .client import build_client_from_env, scoped_client_for_workroom


def _non_negative_float(value: str) -> float:
    """argparse type for a seconds value that must be zero or positive."""
    try:
        parsed = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid float value: '{value}'")
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError(
            "must be a finite zero or positive number of seconds"
        )
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


# Flags that only mean something under the legacy contract. Canonical Kaizen
# binds a model per instance, not per agent, so accepting these there would
# silently drop the operator's model choice — the request would still succeed
# and the agent would answer on some other model.
_LEGACY_ONLY_AGENT_FLAGS = (
    ("--model", "model"),
    ("--provider", "provider"),
    ("--endpoint-path", "endpoint_path"),
    ("--llm-base-url", "llm_base_url"),
    ("--llm-api-key-env", "llm_api_key_env"),
    ("--custom-instructions", "custom_instructions"),
)

# The mirror set: fields that exist only in the canonical ``content`` body. The
# legacy create has nowhere to put them, so accepting them there would drop an
# operator's choice just as silently as the reverse.
_CANONICAL_ONLY_AGENT_FLAGS = (
    ("--persona", "persona"),
    ("--capability-ceiling", "capability_ceiling"),
)


def _reject_flags(
    args: argparse.Namespace,
    flags: Sequence[Tuple[str, str]],
    singular: str,
    plural: str,
) -> None:
    """Fail loudly when a create carries flags the chosen contract can't honor.

    Every flag this CLI accepts belongs to exactly one contract, and the two
    bodies have no overlap — so a flag aimed at the other contract can only be
    dropped. Dropping it silently is the failure mode the contract split exists
    to remove, so it is always an error, never a warning.
    """
    # `is not None`, not truthiness: an explicitly-passed empty value is still
    # an operator choice aimed at the wrong contract, and dropping it silently
    # is the behavior this guard exists to prevent.
    supplied = [flag for flag, dest in flags if getattr(args, dest) is not None]
    if supplied:
        message = singular if len(supplied) == 1 else plural
        raise SystemExit(f"{', '.join(supplied)} {message}")


_LEGACY_ONLY_TAIL = (
    f"canonical Kaizen ('{CANONICAL_EXTENSION_NAME}') does not support. Bind a "
    "model to the instance with 'kamiwaza-seed bind-chat-model', or pass "
    f"--extension-name {LEGACY_EXTENSION_NAME} to use the legacy contract."
)

_CANONICAL_ONLY_TAIL = (
    f"the canonical Kaizen ('{CANONICAL_EXTENSION_NAME}') agent definition, "
    f"which legacy Kaizen ('{LEGACY_EXTENSION_NAME}') has no field for. Drop "
    "them, or omit --extension-name to create a canonical agent."
)


def _reject_legacy_agent_flags(args: argparse.Namespace) -> None:
    """Reject legacy-only flags on a canonical create."""
    _reject_flags(
        args,
        _LEGACY_ONLY_AGENT_FLAGS,
        f"is a per-agent setting that {_LEGACY_ONLY_TAIL}",
        f"are per-agent settings that {_LEGACY_ONLY_TAIL}",
    )


def _reject_canonical_agent_flags(args: argparse.Namespace) -> None:
    """Reject canonical-only flags on a legacy create."""
    _reject_flags(
        args,
        _CANONICAL_ONLY_AGENT_FLAGS,
        f"belongs to {_CANONICAL_ONLY_TAIL}",
        f"belong to {_CANONICAL_ONLY_TAIL}",
    )


def _validate_agent_args(args: argparse.Namespace, contract: str) -> Optional[str]:
    """Check the flag set against the chosen contract, before any network call.

    Runs ahead of client scoping on purpose: scoping issues a ``workrooms.enter``
    session bind, so validating after it would make a local flag mistake cost a
    server round trip and leave a session bound for a command that then fails.

    Returns the legacy custom-endpoint key when one was requested, so the secret
    is read exactly once — here, with the other local checks — rather than again
    at send time.
    """
    if contract == AGENT_CONTRACT_CANONICAL:
        _reject_legacy_agent_flags(args)
        if not args.persona:
            raise SystemExit(
                "--persona is required for canonical Kaizen agents (it is the "
                "agent's system persona)."
            )
        return None
    _reject_canonical_agent_flags(args)
    if not args.model:
        raise SystemExit(
            f"--model is required for legacy Kaizen ('{LEGACY_EXTENSION_NAME}') agents."
        )
    return _read_env_secret(args.llm_api_key_env, what="custom-endpoint LLM key")


def _create_canonical_agent(args: argparse.Namespace, client) -> dict:
    """Create an agent through the canonical ``content`` contract."""
    definition = AgentDefinition(
        name=args.name,
        persona=args.persona,
        description=args.description,
        capability_ceiling=args.capability_ceiling,
    )
    agent = client.agents.create_canonical(
        definition,
        base_url=args.kaizen_base_url,
        workroom_id=args.workroom_id,
    )
    return {"agent_id": agent.id, "version": agent.version}


def _create_legacy_agent(
    args: argparse.Namespace, client, api_key: Optional[str]
) -> dict:
    """Create an agent through the legacy flat ``agent_config.llm`` contract."""
    llm = LLMConfig(
        model=args.model,
        provider=args.provider,
        endpoint_path=args.endpoint_path,
        base_url=args.llm_base_url,
    )
    agent = client.agents.create(
        base_url=args.kaizen_base_url,
        name=args.name,
        llm=llm,
        description=args.description,
        custom_instructions=args.custom_instructions,
        llm_api_key=api_key,
        workroom_id=args.workroom_id,
    )
    # `version` rides along as None so both contracts emit the same JSON keys;
    # legacy Kaizen has no content version to report.
    return {"agent_id": agent.id, "version": None}


def cmd_create_agent(args: argparse.Namespace, *, client) -> dict:
    """Create a Kaizen agent using the contract its catalog identity speaks.

    ``--extension-name`` selects the contract, so an operator never has to know
    which body shape a given Kaizen wants — and a mismatch is a local error with
    the fix in it, not an opaque HTTP 422 from the server.
    """
    try:
        contract = agent_contract_for_extension(args.extension_name)
    except ValueError as exc:
        raise SystemExit(str(exc))
    api_key = _validate_agent_args(args, contract)
    client = _client_for_workroom(client, args.workroom_id)
    if contract == AGENT_CONTRACT_CANONICAL:
        return _create_canonical_agent(args, client)
    return _create_legacy_agent(args, client, api_key)


def _bound_role_deployment_id(settings: object, role: str) -> Optional[str]:
    """Pull one role's currently-bound deployment id out of a model-settings view.

    Every model role reports through the same ``{role: {current: {id}}}`` shape,
    so the role name is the only thing that varies between callers.
    """
    if not isinstance(settings, dict):
        return None
    entry = settings.get(role)
    if not isinstance(entry, dict):
        return None
    current = entry.get("current")
    if not isinstance(current, dict):
        return None
    bound = current.get("id")
    return str(bound) if bound else None


def _role_inventory(settings: object, role: str) -> list:
    """List the deployments a Kaizen instance says it could bind to ``role``.

    The instance has already filtered this by model type, resolved each
    endpoint, and dropped anything it could not serve, so it is a better
    authority on what is bindable than anything the caller could reconstruct
    from the platform inventory itself.
    """
    if not isinstance(settings, dict):
        return []
    candidates = settings.get(f"{role}_models")
    if not isinstance(candidates, list):
        return []
    return [
        candidate
        for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("id")
    ]


def _advertises(candidate: dict, capability: str) -> bool:
    capabilities = candidate.get("capabilities")
    if not isinstance(capabilities, list):
        return False
    return capability in {str(item) for item in capabilities}


def discover_role_deployment(
    settings: object, *, role: str, capability: str
) -> Tuple[str, str]:
    """Pick a deployment to bind to ``role``, preferring an explicit capability.

    Selecting by capability rather than by model name is what keeps this
    working across environments that serve the same role from different models
    — a local offline serve on one box, a hosted endpoint on another.

    ``capabilities`` is preferred but not required: the instance already typed
    every candidate as this role, so an entry that simply does not advertise
    its capabilities is still a legitimate fallback rather than a reason to
    fail a seed run.

    Returns:
        The chosen ``(deployment_id, name)``.

    Raises:
        SystemExit: the instance offers nothing bindable for this role.
    """
    candidates = _role_inventory(settings, role)
    preferred = [c for c in candidates if _advertises(c, capability)] or candidates
    if not preferred:
        raise SystemExit(
            f"no {role} model available to bind: the instance reports no "
            f"{role} deployment it can serve. Deploy a model advertising the "
            f"'{capability}' capability and re-run — proceeding without a "
            f"{role} binding would leave the instance silently degraded."
        )
    chosen = preferred[0]
    return str(chosen["id"]), str(chosen.get("name") or chosen["id"])


class _ModelRole(NamedTuple):
    """One bindable Kaizen model role, and what binding the wrong one costs."""

    name: str
    contradiction_consequence: str


_CHAT_ROLE = _ModelRole(
    "chat",
    "an agent created now would answer on an unintended model.",
)
_EMBEDDING_ROLE = _ModelRole(
    "embedding",
    "documents indexed now would be embedded by an unintended model, and "
    "search would compare them against a different one.",
)


def _bind_role_model(
    args: argparse.Namespace,
    *,
    client,
    role: _ModelRole,
    bind: Callable[..., object],
) -> dict:
    """Bind one instance-wide model role, then confirm what the instance reports.

    Shared by every ``bind-<role>-model`` command: the write, the read-back a
    bodiless 2xx forces, and the contradiction check are identical across roles.
    Only the role and the ``bind`` call differ.
    """
    scoped = _client_for_workroom(client, args.workroom_id)
    bound = _bound_role_deployment_id(bind(scoped), role.name)
    if bound is None:
        # The write succeeded (a non-2xx would have raised), we just couldn't
        # read a confirmation out of it — a 204, or a body shaped differently
        # than we expect. Read the settings back rather than either trusting
        # our own request or failing a binding that probably worked.
        try:
            bound = _bound_role_deployment_id(
                scoped.kaizen_ops.get_model_settings(
                    base_url=args.kaizen_base_url,
                    workroom_id=args.workroom_id,
                ),
                role.name,
            )
        except KamiwazaError:
            bound = None
    if bound is not None and bound != args.deployment_id:
        # The instance contradicts us: it is bound to something else. This is
        # the state that must never exit 0 — the seed run would continue on an
        # unintended model and its verification would "pass" anyway.
        raise SystemExit(
            f"{role.name} model binding contradicted: asked for "
            f"'{args.deployment_id}', instance reports '{bound}'. Not "
            f"proceeding — {role.contradiction_consequence}"
        )
    # bound is None here only when neither the write nor the read-back carried
    # a binding. Report that honestly instead of implying confirmation.
    return {
        f"{role.name}_deployment_id": args.deployment_id,
        "confirmed": bound is not None,
    }


def cmd_bind_chat_model(args: argparse.Namespace, *, client) -> dict:
    """Point a canonical Kaizen instance's chat role at a model deployment.

    Canonical Kaizen resolves a model instance-wide rather than per agent, so
    this is what actually gives seeded agents a backing model.
    """
    return _bind_role_model(
        args,
        client=client,
        role=_CHAT_ROLE,
        bind=lambda scoped: scoped.kaizen_ops.set_chat_model(
            args.deployment_id,
            base_url=args.kaizen_base_url,
            workroom_id=args.workroom_id,
        ),
    )


def cmd_bind_embedding_model(args: argparse.Namespace, *, client) -> dict:
    """Point a canonical Kaizen instance's embedding role at a model deployment.

    Without this, Kaizen has no embedding endpoint and answers semantic search
    by silently falling back to lexical matching — a seeded instance looks
    healthy while retrieving materially worse.

    ``--deployment-id`` is optional here, unlike the chat role: nothing in a
    seed run deploys an embedding model, so the one to bind is whatever the
    environment already serves. Omitting it discovers a deployment advertising
    the ``embeddings`` capability from the instance's own inventory, which is
    what keeps this working on an offline box serving a local model and on one
    reaching a hosted endpoint without naming either.
    """
    discovered_name = None
    if not args.deployment_id:
        scoped = _client_for_workroom(client, args.workroom_id)
        try:
            settings = scoped.kaizen_ops.get_model_settings(
                base_url=args.kaizen_base_url,
                workroom_id=args.workroom_id,
            )
        except KamiwazaError as exc:
            # Unlike the post-write read-back, this one is load-bearing: with
            # no inventory there is nothing to bind, and continuing would leave
            # the instance unbound while reporting success.
            raise SystemExit(
                f"could not read the instance's model inventory to choose an "
                f"embedding deployment: {exc}"
            ) from exc
        args.deployment_id, discovered_name = discover_role_deployment(
            settings, role=_EMBEDDING_ROLE.name, capability="embeddings"
        )
    result = _bind_role_model(
        args,
        client=client,
        role=_EMBEDDING_ROLE,
        bind=lambda scoped: scoped.kaizen_ops.set_embedding_model(
            args.deployment_id,
            base_url=args.kaizen_base_url,
            workroom_id=args.workroom_id,
        ),
    )
    if discovered_name is not None:
        # Name what discovery picked: the caller passed no id, so this is the
        # only record of which model the instance now embeds with.
        result["discovered_model"] = discovered_name
    return result


def cmd_create_conversation(args: argparse.Namespace, *, client) -> dict:
    create = kaizen_turns.CREATE_CONVERSATION_BY_CONTRACT[
        kaizen_turns.conversation_contract(args)
    ]
    client = _client_for_workroom(client, args.workroom_id)
    return {"conversation_id": create(args, client).id}


def cmd_chat(args: argparse.Namespace, *, client) -> Optional[dict]:
    """Send a prompt to an agent and return its reply (exercises it end to end).

    Opens a fresh conversation, sends ``--message``, and (with a positive
    ``--timeout``) waits for the agent's reply — proving the agent can actually
    respond, not just that it was created. ``--extension-name`` selects the turn
    contract, which differs between the two Kaizen products across create, send,
    and event delivery alike. ``--raw`` prints only the reply text. Exits
    non-zero on an agent error, an empty reply, or a wait timeout (mirrors
    cmd_deploy_model / cmd_resolve_kaizen_url).
    """
    turn = kaizen_turns.CHAT_TURN_BY_CONTRACT[kaizen_turns.conversation_contract(args)]
    client = _client_for_workroom(client, args.workroom_id)
    try:
        conversation_id, reply = turn(args, client)
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
    return {"conversation_id": conversation_id, "reply": reply}


def cmd_configure_connector(args: argparse.Namespace, *, client) -> dict:
    # Connector-agnostic: the config is an opaque JSON object the platform
    # validates against the connector's published manifest config_schema, so the
    # seeder needs no per-connector shape.
    import json

    from ..schemas.connectors import ConnectorCreate

    try:
        config = json.loads(args.config_json) if args.config_json else {}
    except json.JSONDecodeError as e:
        raise SystemExit(f"--config-json is not valid JSON: {e}") from e
    if not isinstance(config, dict):
        raise SystemExit("--config-json must be a JSON object")

    request = ConnectorCreate(
        name=args.name,
        connector_type=args.type,
        config=config,
        scopes=args.scope or [],
        enabled=True,
    )
    conn = client.connectors.create(request)
    return {"connector_id": str(conn.id), "name": conn.name}


def cmd_import_skill(args: argparse.Namespace, *, client) -> dict:
    path = Path(args.file)
    result = client.skills.import_skill_package(
        filename=path.name, file_content=path.read_bytes()
    )
    skill_id = getattr(result, "id", None)
    return {"skill_id": str(skill_id) if skill_id is not None else None}


# --- parser ----------------------------------------------------------------


def _add_bind_model_parser(
    sub,
    role: str,
    func: Callable[..., dict],
    *,
    discoverable: bool = False,
) -> None:
    """Register one ``bind-<role>-model`` subcommand.

    Every model role takes the same three arguments — the instance root, the
    deployment to bind, and the workroom scope — so the role name is all that
    distinguishes the parsers. ``discoverable`` marks a role whose deployment
    the command can choose from the instance's own inventory, which makes
    ``--deployment-id`` an override rather than a requirement.
    """
    p = sub.add_parser(
        f"bind-{role}-model",
        help=(
            f"Bind a canonical Kaizen instance's {role} role to a model "
            "deployment."
        ),
    )
    p.add_argument(
        "--kaizen-base-url",
        required=True,
        help="Kaizen instance API root (per-workroom).",
    )
    deployment_help = f"Kamiwaza model deployment to serve Kaizen {role}."
    if discoverable:
        deployment_help += (
            " Defaults to one the instance already offers for this role."
        )
    p.add_argument(
        "--deployment-id",
        required=not discoverable,
        default=None,
        help=deployment_help,
    )
    p.add_argument("--workroom-id", default=None)
    p.set_defaults(func=func)


def _add_create_conversation_parser(sub) -> None:
    """Register the ``create-conversation`` subcommand."""
    p = sub.add_parser(
        "create-conversation",
        help="Start a Kaizen conversation.",
        allow_abbrev=False,
    )
    p.add_argument(
        "--kaizen-base-url",
        required=True,
        help="Kaizen instance API root (per-workroom).",
    )
    p.add_argument(
        "--extension-name",
        default=CANONICAL_EXTENSION_NAME,
        choices=[CANONICAL_EXTENSION_NAME, LEGACY_EXTENSION_NAME],
        help=(
            "Kaizen catalog identity, which selects the conversation contract "
            f"(default: {CANONICAL_EXTENSION_NAME})."
        ),
    )
    p.add_argument(
        "--agent-id",
        default=None,
        help=(
            f"Agent to converse with. Required for '{LEGACY_EXTENSION_NAME}'; "
            f"rejected for '{CANONICAL_EXTENSION_NAME}', which selects the "
            "agent per message instead."
        ),
    )
    p.add_argument(
        "--title", default=None, help=f"Legacy-only ('{LEGACY_EXTENSION_NAME}')."
    )
    p.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help=f"Agent step ceiling; legacy-only ('{LEGACY_EXTENSION_NAME}', default 500).",
    )
    p.add_argument(
        "--ephemeral",
        action="store_true",
        help=f"Legacy-only ('{LEGACY_EXTENSION_NAME}').",
    )
    p.add_argument("--workroom-id", default=None)
    p.set_defaults(func=cmd_create_conversation)


def _add_chat_parser(sub) -> None:
    """Register the ``chat`` subcommand."""
    p = sub.add_parser(
        "chat",
        help="Send a prompt to an agent and return its reply (exercises the agent end to end).",
        allow_abbrev=False,
    )
    p.add_argument(
        "--kaizen-base-url",
        required=True,
        help="Kaizen instance API root (per-workroom).",
    )
    p.add_argument(
        "--extension-name",
        default=CANONICAL_EXTENSION_NAME,
        choices=[CANONICAL_EXTENSION_NAME, LEGACY_EXTENSION_NAME],
        help=(
            "Kaizen catalog identity, which selects the conversation contract "
            f"(default: {CANONICAL_EXTENSION_NAME})."
        ),
    )
    p.add_argument("--agent-id", required=True)
    p.add_argument("--message", required=True, help="Prompt to send to the agent.")
    p.add_argument("--workroom-id", default=None)
    p.add_argument(
        "--title",
        default=None,
        help=f"Conversation title; legacy-only ('{LEGACY_EXTENSION_NAME}').",
    )
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
        type=_non_negative_float,
        default=3.0,
        help="Seconds between event polls while waiting (default: 3).",
    )
    p.set_defaults(func=cmd_chat)


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
    p.add_argument(
        "--poll-interval",
        type=_non_negative_float,
        default=5.0,
        help="Seconds between readiness polls.",
    )
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
        type=_non_negative_float,
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
    p.add_argument(
        "--extension-name",
        default=CANONICAL_EXTENSION_NAME,
        choices=[CANONICAL_EXTENSION_NAME, LEGACY_EXTENSION_NAME],
        help=(
            "Kaizen catalog identity, which selects the agent-create contract "
            f"(default: {CANONICAL_EXTENSION_NAME})."
        ),
    )
    p.add_argument(
        "--persona",
        default=None,
        help=f"System persona (required for '{CANONICAL_EXTENSION_NAME}').",
    )
    p.add_argument(
        "--capability-ceiling",
        default=None,
        help=f"Capability tier for '{CANONICAL_EXTENSION_NAME}' (server default: read).",
    )
    p.add_argument(
        "--model", default=None, help=f"Required for '{LEGACY_EXTENSION_NAME}' only."
    )
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

    _add_bind_model_parser(sub, "chat", cmd_bind_chat_model)

    _add_bind_model_parser(
        sub, "embedding", cmd_bind_embedding_model, discoverable=True
    )

    _add_create_conversation_parser(sub)

    _add_chat_parser(sub)

    p = sub.add_parser("import-skill", help="Import a skill package (.zip).")
    p.add_argument("--file", required=True)
    p.set_defaults(func=cmd_import_skill)

    p = sub.add_parser(
        "configure-connector",
        help="Register a cluster-wide connector (connector-agnostic).",
    )
    p.add_argument(
        "--type",
        required=True,
        help="connector_type, resolved against the published catalog (e.g. m365).",
    )
    p.add_argument("--name", required=True, help="Display name for the connector.")
    p.add_argument(
        "--config-json",
        default="{}",
        help=(
            "Provider config as a JSON object; validated server-side against the "
            "connector's manifest config_schema."
        ),
    )
    p.add_argument(
        "--scope",
        action="append",
        help="OAuth scope (repeatable); omit for service-token connectors.",
    )
    p.set_defaults(func=cmd_configure_connector)

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
