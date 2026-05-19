// Note: createLocalDevAuthMiddleware is intentionally NOT re-exported here.
// It lives at the dedicated `@kamiwaza-ai/extensions-lib/local-dev-auth`
// subpath so importing from `./server` does not pull `next/server` into
// non-Next consumers (PR #87 round-3 review: codex P2).
export { extractIdentity, extractIdentityStrict } from "./identity";
export { createProxyHandlers } from "./proxy";
export { fetchModels } from "./models";
export {
    streamWithRefresh,
    asStreamInterrupted,
    BodyTooLargeError,
    DEFAULT_MAX_BUFFER_BYTES,
} from "./token-refresh";
export type {
    ProxyOpts,
    RefreshFn,
    RetryableBodyInit,
    Headers as TokenRefreshHeaders,
} from "./token-refresh";
export type { Identity, ProxyConfig, AvailableModel } from "./types";
export {
    KamiwazaRuntimeError,
    MisboundAuthError,
    UnexpectedContextError,
    OutOfEnvelopeAccessError,
    PlatformOutageError,
    StreamInterruptedError,
} from "./errors";
