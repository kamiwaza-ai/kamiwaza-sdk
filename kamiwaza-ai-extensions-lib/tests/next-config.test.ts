import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
    KAMIWAZA_BASE_PATH_SENTINEL,
    SUPPORTED_NEXT_VERSIONS,
    withKamiwazaAppGarden,
    _internals,
} from "../src/next-config/index";

const SUPPORTED = SUPPORTED_NEXT_VERSIONS[0];
const realDetect = _internals.detectNextVersion;

beforeEach(() => {
    _internals.detectNextVersion = () => SUPPORTED;
    delete process.env.NODE_ENV;
});

afterEach(() => {
    _internals.detectNextVersion = realDetect;
    delete process.env.KZ_NEXT_BUILD_VARIANT;
    delete process.env.NODE_ENV;
});

describe("withKamiwazaAppGarden", () => {
    it("exports a high-entropy absolute sentinel", () => {
        expect(KAMIWAZA_BASE_PATH_SENTINEL).toBe("/__KZ_RUNTIME_BASE_7F3A91C2__");
    });

    it("bakes the sentinel for the path variant", () => {
        process.env.KZ_NEXT_BUILD_VARIANT = "path";
        const config = withKamiwazaAppGarden({ output: "standalone" });
        expect(config.basePath).toBe(KAMIWAZA_BASE_PATH_SENTINEL);
        expect(config.assetPrefix).toBe(KAMIWAZA_BASE_PATH_SENTINEL);
        expect(config.output).toBe("standalone");
        expect(config.env?.KZ_INTERNAL_BAKED_APP_PATH).toBe(KAMIWAZA_BASE_PATH_SENTINEL);
        expect(config.experimental?.isrFlushToDisk).toBe(false);
    });

    it("bakes no base path for the port variant", () => {
        process.env.KZ_NEXT_BUILD_VARIANT = "port";
        const config = withKamiwazaAppGarden({});
        expect(config.basePath).toBeUndefined();
        expect(config.assetPrefix).toBeUndefined();
        expect(config.env?.KZ_INTERNAL_BAKED_APP_PATH).toBe("");
    });

    it("behaves like the port variant when the variant is unset (next dev)", () => {
        const config = withKamiwazaAppGarden();
        expect(config.basePath).toBeUndefined();
        expect(config.env?.KZ_INTERNAL_BAKED_APP_PATH).toBe("");
    });

    it("ignores a build variant during development (N1)", () => {
        process.env.KZ_NEXT_BUILD_VARIANT = "path";
        process.env.NODE_ENV = "development";
        const config = withKamiwazaAppGarden();
        expect(config.basePath).toBeUndefined();
        expect(config.env?.KZ_INTERNAL_BAKED_APP_PATH).toBe("");
    });

    it("defaults output to standalone and preserves author config", () => {
        process.env.KZ_NEXT_BUILD_VARIANT = "path";
        const config = withKamiwazaAppGarden({ images: { unoptimized: true } });
        expect(config.output).toBe("standalone");
        expect(config.images?.unoptimized).toBe(true);
    });

    it("rejects author overrides of reserved settings", () => {
        process.env.KZ_NEXT_BUILD_VARIANT = "path";
        expect(() => withKamiwazaAppGarden({ basePath: "/custom" })).toThrow(/basePath/);
        expect(() => withKamiwazaAppGarden({ assetPrefix: "/custom" })).toThrow(/assetPrefix/);
        expect(() => withKamiwazaAppGarden({ output: "export" })).toThrow(/standalone/);
        expect(() =>
            withKamiwazaAppGarden({ env: { KZ_INTERNAL_BAKED_APP_PATH: "/x" } }),
        ).toThrow(/KZ_INTERNAL_BAKED_APP_PATH/);
    });

    it("rejects unsupported relocation-hostile options", () => {
        process.env.KZ_NEXT_BUILD_VARIANT = "path";
        expect(() =>
            withKamiwazaAppGarden({ productionBrowserSourceMaps: true }),
        ).toThrow(/source map/i);
        expect(() =>
            withKamiwazaAppGarden({ experimental: { sri: { algorithm: "sha256" } } } as never),
        ).toThrow(/sri/i);
        expect(() =>
            withKamiwazaAppGarden({ experimental: { serverSourceMaps: true } } as never),
        ).toThrow(/source map/i);
        expect(() =>
            withKamiwazaAppGarden({ experimental: { manualClientBasePath: true } } as never),
        ).toThrow(/manualClientBasePath/i);
        expect(() =>
            withKamiwazaAppGarden({
                serverExternalPackages: ["@kamiwaza-ai/extensions-lib"],
            }),
        ).toThrow(/serverExternalPackages/i);
    });

    it("enforces the exact supported Next version for production variants (B5)", () => {
        process.env.KZ_NEXT_BUILD_VARIANT = "path";
        _internals.detectNextVersion = () => "15.4.0";
        expect(() => withKamiwazaAppGarden({})).toThrow(/15\.4\.0/);
    });

    it("fails closed when the Next version cannot be detected for a production variant (B5)", () => {
        process.env.KZ_NEXT_BUILD_VARIANT = "port";
        _internals.detectNextVersion = () => undefined;
        expect(() => withKamiwazaAppGarden({})).toThrow(/detect/i);
    });

    it("does not enforce the version pin for plain next dev", () => {
        _internals.detectNextVersion = () => "15.4.0";
        expect(() => withKamiwazaAppGarden({})).not.toThrow();
    });
});
