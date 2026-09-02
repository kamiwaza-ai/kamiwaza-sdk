import Link from "next/link";
import { appAsset } from "@kamiwaza-ai/extensions-lib/runtime";

import { LongText } from "./long-text";

// A >1024-char string prop containing an appAsset URL forces a
// byte-length-framed Flight T-row whose payload includes the sentinel in
// the path artifact — the exact case the .rsc transformer exists for (B1).
const LONG =
    `${"x".repeat(1100)} icon at ${appAsset("/kmza-icon.svg")}` +
    " KZ_FLIGHT_TAIL_9A6E2D43";

export default function Home() {
    return (
        <main>
            <h1>canary home</h1>
            <Link href="/nested">nested</Link>
            <Link href="/go">redirect</Link>
            <LongText text={LONG} />
        </main>
    );
}
