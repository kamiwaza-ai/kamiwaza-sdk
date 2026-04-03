import { defineConfig } from "tsup";

export default defineConfig([
    {
        entry: {
            "client/index": "src/client/index.ts",
            "server/index": "src/server/index.ts",
        },
        format: ["esm", "cjs"],
        dts: true,
        sourcemap: true,
        external: ["react", "react-dom", "next"],
        splitting: false,
        clean: true,
    },
]);
