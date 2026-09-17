// Frontend liveness contract for the container health check. Stable in both
// build variants; served under the deployment prefix in path mode.
export const dynamic = "force-dynamic";

export function GET() {
    return Response.json(
        { status: "ok", service: "frontend" },
        { headers: { "cache-control": "no-store" } },
    );
}
