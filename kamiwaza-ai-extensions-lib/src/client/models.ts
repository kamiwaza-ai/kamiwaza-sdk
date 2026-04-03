"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useSession } from "./useSession";
import type { AvailableModel } from "./types";

function parseModel(d: Record<string, unknown>): AvailableModel {
    const caps = Array.isArray(d.capabilities) ? d.capabilities.filter(
        (c): c is string => typeof c === "string"
    ) : [];
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
 * Hook that fetches available models from the extension backend.
 *
 * Calls `GET /api/models` (prefixed by base path from SessionProvider
 * context) and returns typed `AvailableModel` objects.
 */
export function useModels(endpoint: string = "/api/models") {
    const { basePath } = useSession();
    const [models, setModels] = useState<AvailableModel[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<Error | null>(null);
    const controllerRef = useRef<AbortController | null>(null);

    const fetchModels = useCallback(async () => {
        controllerRef.current?.abort();
        const controller = new AbortController();
        controllerRef.current = controller;

        try {
            setLoading(true);
            const res = await fetch(`${basePath}${endpoint}`, {
                credentials: "include",
                signal: controller.signal,
            });
            if (controller.signal.aborted) return;
            if (!res.ok) throw new Error(`Model fetch failed: ${res.status}`);
            const data = await res.json();

            if (controller.signal.aborted) return;
            const parsed: AvailableModel[] = (Array.isArray(data) ? data : []).map(
                (d: Record<string, unknown>) => parseModel(d)
            );
            setModels(parsed);
            setError(null);
        } catch (err) {
            if (controller.signal.aborted) return;
            setError(err instanceof Error ? err : new Error(String(err)));
        } finally {
            if (!controller.signal.aborted) setLoading(false);
        }
    }, [basePath, endpoint]);

    useEffect(() => {
        fetchModels();
        return () => controllerRef.current?.abort();
    }, [fetchModels]);

    return { models, loading, error, refresh: fetchModels };
}
