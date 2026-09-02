export const dynamic = "force-dynamic";

export function GET(request: Request) {
    return Response.json({ ok: true, url: request.url });
}
