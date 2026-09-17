/**
 * Inline runtime bootstrap for the root layout.
 *
 * Emits only `{ routingMode, appPath }` — in the path build variant the
 * appPath is the relocation sentinel, so the boot relocator rewrites the
 * prerendered inline script to the real deployment prefix. No spawn-specific
 * value (deployment id, public URL) may be rendered here, or a statically
 * prerendered layout would bake it stale. Keep this component free of
 * `headers()`/dynamic APIs so the layout stays statically optimizable.
 */

import React from "react";

import type { KamiwazaClientRouting } from "./shared";

export interface KamiwazaRuntimeBootstrapProps {
    nonce?: string;
}

export function KamiwazaRuntimeBootstrap(
    props: KamiwazaRuntimeBootstrapProps = {},
): React.JSX.Element {
    const appPath = process.env.KZ_INTERNAL_BAKED_APP_PATH ?? "";
    const routing: KamiwazaClientRouting = {
        routingMode: appPath === "" ? "port" : "path",
        appPath,
    };
    // Escape "<" so the payload cannot terminate the inline script tag.
    const payload = JSON.stringify(routing).replace(/</g, "\\u003c");
    const script = `globalThis.__KAMIWAZA_RUNTIME__=Object.freeze(${payload});`;

    return (
        <script
            nonce={props.nonce}
            dangerouslySetInnerHTML={{ __html: script }}
        />
    );
}
