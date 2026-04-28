import { createProxyHandlers } from "@kamiwaza-ai/extensions-lib/server";

// Next.js App Router strips basePath before routing, so handlers receive
// paths like /api/foo regardless of KAMIWAZA_APP_PATH. No pathPrefix needed.
const { DELETE, GET, PATCH, POST, PUT } = createProxyHandlers(
    {
        target: process.env.BACKEND_URL || "http://backend:8000",
        backendUrl: process.env.BACKEND_URL || "http://backend:8000",
    } as never,
);

export { DELETE, GET, PATCH, POST, PUT };
