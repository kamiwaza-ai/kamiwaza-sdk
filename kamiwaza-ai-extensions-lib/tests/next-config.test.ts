import { afterEach, describe, expect, it } from "vitest";

import {
    KAMIWAZA_BASE_PATH_SENTINEL,
    SUPPORTED_NEXT_VERSIONS,
    withKamiwazaAppGarden,
} from "../src/next-config/index";

const SUPPORTED = SUPPORTED_NEXT_VERSIONS[0];

afterEach(() => {
    delete process.env.KZ_NEXT_BUILD_VARIANT;
});

describe("withKamiwazaAppGarden", () => {
    it("exports a high-entropy absolute sentinel", () => {
        expect(KAMIWAZA_BASE_PATH_SENTINEL).toBe("/__KZ_RUNTIME_BASE_7F3A91C2__");
    });

    it("bakes the sentinel for the path variant", () => {
        process.env.KZ_NEXT_BUILD_VARIANT = "path";
        const config = withKamiwazaAppGarden({ output: "standalone" }, { nextVersion: SUPPORTED });
        expect(config.basePath).toBe(KAMIWAZA_BASE_PATH_SENTINEL);
        expect(config.assetPrefix).toBe(KAMIWAZA_BASE_PATH_SENTINEL);
        expect(config.output).toBe("standalone");
        expect(config.env?.KZ_INTERNAL_BAKED_APP_PATH).toBe(KAMIWAZA_BASE_PATH_SENTINEL);
        expect(config.experimental?.isrFlushToDisk).toBe(false);
    });

    it("bakes no base path for the port variant", () => {
        process.env.KZ_NEXT_BUILD_VARIANT = "port";
        const config = withKamiwazaAppGarden({}, { nextVersion: SUPPORTED });
        expect(config.basePath).toBeUndefined();
        expect(config.assetPrefix).toBeUndefined();
        expect(config.env?.KZ_INTERNAL_BAKED_APP_PATH).toBe("");
    });

    it("behaves like the port variant when the variant is unset (next dev)", () => {
        const config = withKamiwazaAppGarden();
        expect(config.basePath).toBeUndefined();
        expect(config.env?.KZ_INTERNAL_BAKED_APP_PATH).toBe("");
    });

    it("defaults output to standalone and preserves author config", () => {
        process.env.KZ_NEXT_BUILD_VARIANT = "path";
        const config = withKamiwazaAppGarden(
            { images: { unoptimized: true } },
            { nextVersion: SUPPORTED },
        );
        expect(config.output).toBe("standalone");
        expect(config.images?.unoptimized).toBe(true);
    });

    it("rejects author overrides of reserved settings", () => {
        process.env.KZ_NEXT_BUILD_VARIANT = "path";
        const opts = { nextVersion: SUPPORTED };
        expect(() => withKamiwazaAppGarden({ basePath: "/custom" }, opts)).toThrow(/basePath/);
        expect(() =>
            withKamiwazaAppGarden({ assetPrefix: "/custom" }, opts),
        ).toThrow(/assetPrefix/);
        expect(() =>
            withKamiwazaAppGarden({ env: { KZ_INTERNAL_BAKED_APP_PATH: "/x" } }, opts),
        ).toThrow(/KZ_INTERNAL_BAKED_APP_PATH/);
    });

    it("rejects unsupported relocation-hostile options", () => {
        process.env.KZ_NEXT_BUILD_VARIANT = "path";
        const opts = { nextVersion: SUPPORTED };
        expect(() =>
            withKamiwazaAppGarden({ productionBrowserSourceMaps: true }, opts),
        ).toThrow(/source map/i);
        expect(() =>
            withKamiwazaAppGarden({ experimental: { sri: { algorithm: "sha256" } } } as never, opts),
        ).toThrow(/sri/i);
        expect(() =>
            withKamiwazaAppGarden(
                { experimental: { manualClientBasePath: true } } as never,
                opts,
            ),
        ).toThrow(/manualClientBasePath/i);
    });

    it("enforces the exact supported Next version for production variants", () => {
        process.env.KZ_NEXT_BUILD_VARIANT = "path";
        expect(() =>
            withKamiwazaAppGarden({}, { nextVersion: "15.4.0" }),
        ).toThrow(/15\.4\.0/);
    });

    it("does not enforce the version pin for plain next dev", () => {
        expect(() => withKamiwazaAppGarden({}, { nextVersion: "15.4.0" })).not.toThrow();
    });
});
