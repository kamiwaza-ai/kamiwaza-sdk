import { createProxyHandlers } from "@kamiwaza-ai/extensions-lib/server";

const { POST } = createProxyHandlers({
    target: process.env.BACKEND_URL || "http://backend:8000",
});

export { POST };
