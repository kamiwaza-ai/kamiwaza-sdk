const ALLOWED_BRIDGE_HEADERS = new Set([
    "authorization",
    "x-auth-token",
    "x-user-id",
    "x-user-email",
    "x-user-name",
    "x-user-roles",
    "x-workroom-id",
    "x-request-id",
]);

function bridgeEnabled(): boolean {
    const raw = (process.env.KAMIWAZA_LOCAL_DEV_AUTH_BRIDGE ?? "").trim().toLowerCase();
    return raw !== "" && !["0", "false", "no"].includes(raw);
}

export function getLocalDevAuthHeaders(): Record<string, string> {
    if (!bridgeEnabled()) return {};

    const raw = process.env.KAMIWAZA_LOCAL_DEV_AUTH_HEADERS_JSON;
    if (!raw) return {};

    let parsed: unknown;
    try {
        parsed = JSON.parse(raw);
    } catch {
        return {};
    }

    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        return {};
    }

    const out: Record<string, string> = {};
    for (const [key, value] of Object.entries(parsed)) {
        const lowered = key.toLowerCase();
        if (!ALLOWED_BRIDGE_HEADERS.has(lowered)) continue;
        if (value === null || value === undefined || value === "") continue;
        out[lowered] = String(value);
    }
    return out;
}
