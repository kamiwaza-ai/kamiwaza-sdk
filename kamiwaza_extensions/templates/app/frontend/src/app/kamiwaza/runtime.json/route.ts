// Lazy full runtime config (deployment id, public URLs). Nothing in the
// default hydration path calls this; clients reach it through
// loadKamiwazaRuntime() when they actually need deployment details.
import { createRuntimeConfigResponse } from "@kamiwaza-ai/extensions-lib/runtime/server";

export const dynamic = "force-dynamic";

export function GET() {
    return createRuntimeConfigResponse();
}
