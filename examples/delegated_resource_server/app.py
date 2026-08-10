"""Dependency-free ASGI document resource protected by the shared SDK guard."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

import requests  # type: ignore[import-untyped]

from examples.delegated_resource_server.adapters import (
    DocumentCanonicalizer,
    DocumentResultNormalizer,
)
from examples.delegated_resource_server.mutations import (
    ExactApprovedMutationFixture,
    MutationRejected,
    MutationRequest,
)
from examples.delegated_resource_server.runtime import (
    BoundedJwksProvider,
    ResourceRuntimeConfig,
)
from kamiwaza_sdk.delegated_workloads import (
    CoreResourceGuardHTTPClient,
    ProtectedResourceGuard,
    ResourceGuardRegistration,
    ResourceGuardRejected,
    SealedDelegatedContext,
)
from kamiwaza_sdk.delegated_workloads.integrations.asgi import DelegatedResourceASGI
from kamiwaza_sdk.delegated_workloads.proof import body_digest
from kamiwaza_sdk.delegated_workloads.transport import SessionPort


Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
_RESOURCE_TYPE = "conformance.document"


@dataclass(frozen=True, slots=True, kw_only=True)
class ResourceApplicationConfig:
    audience: str
    registration_revision_id: UUID
    descriptor_version: str = "v1"
    guard_contract_version: str = "guard:v1"

    def registration(self, action: str) -> ResourceGuardRegistration:
        return ResourceGuardRegistration(
            resource_type=_RESOURCE_TYPE,
            descriptor_version=self.descriptor_version,
            revision_id=self.registration_revision_id,
            audience=self.audience,
            action=action,
            guard_contract_version=self.guard_contract_version,
        )


class DocumentStore:
    """Small deterministic store used only by the conformance resource."""

    def __init__(self) -> None:
        self._documents: dict[str, dict[str, object]] = {}

    def get(self, resource_id: str) -> Mapping[str, object] | None:
        document = self._documents.get(resource_id)
        return dict(document) if document is not None else None

    def put(self, resource_id: str, title: str) -> Mapping[str, object]:
        current = self._documents.get(resource_id, {})
        previous = current.get("version", 0)
        version = previous + 1 if isinstance(previous, int) else 1
        document = {
            "id": resource_id,
            "title": title,
            "status": "ready",
            "version": version,
        }
        self._documents[resource_id] = document
        return dict(document)


class DocumentApplication:
    def __init__(
        self,
        store: DocumentStore,
        mutations: ExactApprovedMutationFixture,
    ) -> None:
        self._store = store
        self._mutations = mutations
        self._canonicalizer = DocumentCanonicalizer()
        self._normalizer = DocumentResultNormalizer()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        context = _sealed_context(scope)
        resource_id = self._canonicalizer.canonicalize(_document_id(scope))
        if context.context.resource.id != resource_id:
            raise ResourceGuardRejected()
        if scope.get("method") == "GET":
            await self._read(resource_id, context, send)
            return
        mutation = await _mutation_request(receive, resource_id)
        try:
            applied = self._mutations.mutate(mutation, context)
        except MutationRejected:
            raise ResourceGuardRejected() from None
        result = self._normalizer.normalize(applied)
        await _json_response(send, 200, _attributed(result, context))

    async def _read(
        self,
        resource_id: str,
        context: SealedDelegatedContext,
        send: Send,
    ) -> None:
        document = self._store.get(resource_id)
        if document is None:
            await _json_response(send, 404, {"error": {"code": "not_found"}})
            return
        result = self._normalizer.normalize(document)
        await _json_response(send, 200, _attributed(result, context))


class NeutralResourceApplication:
    def __init__(
        self,
        guard: ProtectedResourceGuard,
        config: ResourceApplicationConfig,
        store: DocumentStore,
        mutations: ExactApprovedMutationFixture,
    ) -> None:
        application = DocumentApplication(store, mutations)
        self._read = DelegatedResourceASGI(
            application,
            guard,
            config.registration("read"),
        )
        self._mutate = DelegatedResourceASGI(
            application,
            guard,
            config.registration("mutate"),
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        method = scope.get("method")
        path = scope.get("path")
        if method == "GET" and path == "/healthz":
            await _json_response(send, 200, {"status": "ok"})
            return
        if not _document_path(path):
            await _json_response(send, 404, {"error": {"code": "not_found"}})
            return
        if method == "GET":
            await self._read(scope, receive, send)
            return
        if method == "PUT":
            await self._mutate(scope, receive, send)
            return
        await _json_response(send, 405, {"error": {"code": "method_not_allowed"}})


def build_application(
    guard: ProtectedResourceGuard,
    config: ResourceApplicationConfig,
    store: DocumentStore | None = None,
    mutations: ExactApprovedMutationFixture | None = None,
) -> NeutralResourceApplication:
    resolved_store = store or DocumentStore()
    resolved_mutations = mutations or ExactApprovedMutationFixture(resolved_store)
    return NeutralResourceApplication(
        guard,
        config,
        resolved_store,
        resolved_mutations,
    )


def create_app() -> NeutralResourceApplication:
    """Build the deployable app from non-secret runtime configuration."""
    runtime = ResourceRuntimeConfig.from_environment()
    session = requests.Session()
    port = cast(SessionPort, session)
    keys = BoundedJwksProvider(
        port,
        runtime.core_base_url.rstrip("/") + "/.well-known/jwks.json",
    )
    decisions = CoreResourceGuardHTTPClient(runtime.core_base_url, port)
    guard = ProtectedResourceGuard(keys, decisions)
    config = ResourceApplicationConfig(
        audience=runtime.audience,
        registration_revision_id=runtime.registration_revision_id,
    )
    return build_application(guard, config)


def _sealed_context(scope: Mapping[str, object]) -> SealedDelegatedContext:
    state = scope.get("state")
    context = (
        state.get("kamiwaza_delegated_context") if isinstance(state, Mapping) else None
    )
    if not isinstance(context, SealedDelegatedContext):
        raise ResourceGuardRejected()
    return context


def _document_id(scope: Mapping[str, object]) -> str:
    path = scope.get("path")
    if not isinstance(path, str) or not _document_path(path):
        raise ResourceGuardRejected()
    return path.removeprefix("/v1/documents/")


def _document_path(path: object) -> bool:
    return (
        isinstance(path, str) and path.startswith("/v1/documents/") and len(path) > 14
    )


async def _mutation_request(
    receive: Receive,
    resource_id: str,
) -> MutationRequest:
    message = await receive()
    body = message.get("body")
    payload = _mutation_payload(body)
    title = payload.get("title")
    if not _valid_title(title):
        raise ResourceGuardRejected()
    return MutationRequest(
        resource_id,
        cast(str, title),
        body_digest(cast(bytes, body)),
    )


def _mutation_payload(body: object) -> dict[str, object]:
    if not isinstance(body, bytes):
        raise ResourceGuardRejected()
    payload = _decoded_json(body)
    if not isinstance(payload, dict):
        raise ResourceGuardRejected()
    if set(payload) != {"title"}:
        raise ResourceGuardRejected()
    return cast(dict[str, object], payload)


def _decoded_json(body: bytes) -> object:
    try:
        return json.loads(body)
    except (TypeError, ValueError) as exc:
        raise ResourceGuardRejected() from exc


def _valid_title(value: object) -> bool:
    length = len(value) if isinstance(value, str) else 0
    return all((isinstance(value, str), bool(value), length <= 256))


def _attributed(
    result: Mapping[str, object],
    context: SealedDelegatedContext,
) -> dict[str, object]:
    return {
        **result,
        "subject_id": str(context.subject_id),
        "actor_id": context.actor_id,
    }


async def _json_response(
    send: Send,
    status: int,
    body: Mapping[str, object],
) -> None:
    encoded = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": encoded})


__all__ = (
    "DocumentStore",
    "NeutralResourceApplication",
    "ResourceApplicationConfig",
    "build_application",
    "create_app",
)
