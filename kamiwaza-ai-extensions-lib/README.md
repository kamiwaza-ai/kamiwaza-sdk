# @kamiwaza-ai/extensions-lib

TypeScript runtime library for Kamiwaza extensions. Provides
`SessionProvider` / `AuthGuard` for Next.js client code, identity
extraction and proxy utilities for the server, and a local-dev auth
bridge for forwarding the developer's real Kamiwaza identity into a
container during `kz-ext dev local --auth`.

This package is published to npm as a standalone artifact, paired with
the Python runtime library [`kamiwaza-extensions-lib`](https://pypi.org/project/kamiwaza-extensions-lib/).
Both follow the same semver track but are versioned independently.

## Install

```bash
npm install @kamiwaza-ai/extensions-lib
```

## Usage

```ts
// Client (Next.js app router)
import { SessionProvider, AuthGuard, useSession } from "@kamiwaza-ai/extensions-lib/client";

// Server (Next.js route handlers / middleware)
import { extractIdentity, createProxyHandlers } from "@kamiwaza-ai/extensions-lib/server";

// Local dev — forward the host kz-ext login into a container
import { createLocalDevAuthMiddleware } from "@kamiwaza-ai/extensions-lib/local-dev-auth";
```

See [`CHANGELOG.md`](https://github.com/kamiwaza-ai/kamiwaza-sdk/blob/main/kamiwaza-ai-extensions-lib/CHANGELOG.md)
for release notes.

## License

Apache-2.0 — see [LICENSE](./LICENSE).
