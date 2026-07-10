import { defineConfig } from "tsup";

export default defineConfig([
    {
        entry: {
            "client/index": "src/client/index.ts",
            "server/index": "src/server/index.ts",
            // Local-dev auth bridge gets its own subpath so consumers
            // who only use the non-Next server helpers (fetchModels,
            // createProxyHandlers, identity extractors) don't load
            // `next/server` at all. PR #87 round-3 review (codex P2).
            "local-dev-auth/index": "src/local-dev-auth/index.ts",
            // Runtime-path relocation contract (dual-artifact App Garden
            // runtime). next-config is build-time only; runtime is
            // client-safe; runtime/server-index needs Node/server APIs.
            "next-config/index": "src/next-config/index.ts",
            "runtime/index": "src/runtime/index.ts",
            "runtime/server-index": "src/runtime/server-index.ts",
        },
        format: ["esm", "cjs"],
        dts: true,
        sourcemap: true,
        external: ["react", "react-dom", "next"],
        splitting: false,
        clean: true,
    },
]);
