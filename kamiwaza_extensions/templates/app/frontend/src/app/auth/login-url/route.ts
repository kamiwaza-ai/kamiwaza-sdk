import { createProxyHandlers } from "@kamiwaza-ai/extensions-lib/server";

const { GET } = createProxyHandlers({
    target: process.env.BACKEND_URL || "http://backend:8000",
});

export { GET };
