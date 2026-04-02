import type { AvailableModel } from "./types";

function parseModel(d: Record<string, unknown>): AvailableModel {
    const caps = Array.isArray(d.capabilities)
        ? d.capabilities.filter((c): c is string => typeof c === "string")
        : [];
    return {
        id: String(d.id ?? d.deployment_id ?? ""),
        name: String(d.name ?? d.model_name ?? ""),
        repoId: typeof d.repo_id === "string" ? d.repo_id : undefined,
        type: typeof d.type === "string" ? d.type : undefined,
        capabilities: caps,
        status: String(d.status ?? d.phase ?? "unknown"),
    };
}

/**
 * Fetch available models from the extension backend.
 *
 * Server-side helper for use in Server Components or API routes.
 * Calls the backend directly (not the platform API).
 */
export async function fetchModels(
    backendUrl: string,
    endpoint: string = "/api/models",
    headers?: Record<string, string>
): Promise<AvailableModel[]> {
    const url = `${backendUrl.replace(/\/+$/, "")}${endpoint}`;
    const res = await fetch(url, { headers });

    if (!res.ok) return [];

    const data = await res.json();
    if (!Array.isArray(data)) return [];

    return data.map((d: Record<string, unknown>) => parseModel(d));
}
