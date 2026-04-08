import { createProxyHandlers } from "@kamiwaza-ai/extensions-lib/server";

const { GET } = createProxyHandlers(
    {
        target: process.env.BACKEND_URL || "http://backend:8000",
        backendUrl: process.env.BACKEND_URL || "http://backend:8000",
    } as never,
);

export { GET };
