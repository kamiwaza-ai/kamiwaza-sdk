"use client";

import { createContext } from "react";
import type { SessionContext } from "./types";

const defaultContext: SessionContext = {
    session: null,
    loading: true,
    error: null,
    logout: async () => {},
    refresh: async () => {},
};

export const SessionCtx = createContext<SessionContext>(defaultContext);
