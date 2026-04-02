"use client";

import { useCallback, useEffect, useState } from "react";
import type { AvailableModel } from "./types";

/**
 * Hook that fetches available models from the extension backend.
 *
 * Calls `GET /api/models` (prefixed by base path) and returns typed
 * `AvailableModel` objects.
 */
export function useModels(endpoint: string = "/api/models") {
    const [models, setModels] = useState<AvailableModel[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<Error | null>(null);

    const basePath = typeof window !== "undefined"
        ? process.env.NEXT_PUBLIC_APP_BASE_PATH ?? ""
        : "";

    const fetchModels = useCallback(async () => {
        try {
            setLoading(true);
            const res = await fetch(`${basePath}${endpoint}`, {
                credentials: "include",
            });
            if (!res.ok) throw new Error(`Model fetch failed: ${res.status}`);
            const data = await res.json();

            const parsed: AvailableModel[] = (Array.isArray(data) ? data : []).map(
                (d: Record<string, unknown>) => ({
                    id: String(d.id ?? d.deployment_id ?? ""),
                    name: String(d.name ?? d.model_name ?? ""),
                    repoId: d.repo_id as string | undefined,
                    type: d.type as string | undefined,
                    capabilities: (d.capabilities as string[]) ?? [],
                    status: String(d.status ?? d.phase ?? "unknown"),
                })
            );
            setModels(parsed);
            setError(null);
        } catch (err) {
            setError(err instanceof Error ? err : new Error(String(err)));
        } finally {
            setLoading(false);
        }
    }, [basePath, endpoint]);

    useEffect(() => {
        fetchModels();
    }, [fetchModels]);

    return { models, loading, error, refresh: fetchModels };
}
