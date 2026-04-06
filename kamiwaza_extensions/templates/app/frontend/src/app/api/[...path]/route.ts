import { createProxyHandlers } from "@kamiwaza-ai/extensions-lib/server";

const { DELETE, GET, PATCH, POST, PUT } = createProxyHandlers({
    target: process.env.BACKEND_URL || "http://backend:8000",
});

export { DELETE, GET, PATCH, POST, PUT };
