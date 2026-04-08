"use client";

import { SessionProvider } from "@kamiwaza-ai/extensions-lib/client";

export function Providers({ children }: { children: React.ReactNode }) {
    return <SessionProvider>{children}</SessionProvider>;
}
