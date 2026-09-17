import { afterEach, describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import React from "react";

import { KamiwazaRuntimeBootstrap } from "../src/runtime/bootstrap";

afterEach(() => {
    delete process.env.KZ_INTERNAL_BAKED_APP_PATH;
});

describe("KamiwazaRuntimeBootstrap", () => {
    it("emits an inline script installing the frozen runtime global (path variant)", () => {
        process.env.KZ_INTERNAL_BAKED_APP_PATH = "/__KZ_RUNTIME_BASE_7F3A91C2__";
        const html = renderToStaticMarkup(<KamiwazaRuntimeBootstrap />);
        expect(html).toContain("<script");
        expect(html).toContain("__KAMIWAZA_RUNTIME__");
        expect(html).toContain("Object.freeze");
        expect(html).toContain('"routingMode":"path"');
        expect(html).toContain('"appPath":"/__KZ_RUNTIME_BASE_7F3A91C2__"');
    });

    it("emits the port variant when no path is baked", () => {
        const html = renderToStaticMarkup(<KamiwazaRuntimeBootstrap />);
        expect(html).toContain('"routingMode":"port"');
        expect(html).toContain('"appPath":""');
    });

    it("escapes script-closing sequences in the payload", () => {
        process.env.KZ_INTERNAL_BAKED_APP_PATH = "/x";
        const html = renderToStaticMarkup(<KamiwazaRuntimeBootstrap />);
        expect(html).not.toContain("</script></script>");
        // The serialized payload must not be able to terminate the script tag
        // early: no raw "<" may appear inside the inline JSON.
        const script = html.slice(html.indexOf(">") + 1, html.lastIndexOf("</script>"));
        expect(script).not.toContain("<");
    });

    it("passes a nonce through to the script tag", () => {
        const html = renderToStaticMarkup(<KamiwazaRuntimeBootstrap nonce="abc123" />);
        expect(html).toContain('nonce="abc123"');
    });
});
