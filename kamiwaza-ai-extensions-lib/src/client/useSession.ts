"use client";

import { useContext } from "react";
import { SessionCtx } from "./SessionContext";
import type { SessionContext } from "./types";

/**
 * Access the current session state.
 *
 * Must be used inside a `<SessionProvider>`.
 */
export function useSession(): SessionContext {
    return useContext(SessionCtx);
}
