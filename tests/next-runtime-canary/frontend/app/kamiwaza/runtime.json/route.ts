import { createRuntimeConfigResponse } from "@kamiwaza-ai/extensions-lib/runtime/server";

export const dynamic = "force-dynamic";

export function GET() {
    return createRuntimeConfigResponse();
}
