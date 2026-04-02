import type { AvailableModel } from "./types";

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

    return data.map((d: Record<string, unknown>) => ({
        id: String(d.id ?? d.deployment_id ?? ""),
        name: String(d.name ?? d.model_name ?? ""),
        repoId: d.repo_id as string | undefined,
        type: d.type as string | undefined,
        capabilities: (d.capabilities as string[]) ?? [],
        status: String(d.status ?? d.phase ?? "unknown"),
    }));
}
