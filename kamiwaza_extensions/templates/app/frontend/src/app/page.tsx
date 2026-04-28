"use client";

import {
    AuthGuard,
    useSession,
} from "@kamiwaza-ai/extensions-lib/client";
import {
    useEffect,
    useRef,
    useState,
    type ComponentType,
    type KeyboardEvent,
    type ReactNode,
} from "react";
import { NavBar } from "../components/NavBar";

type ChatMessage = {
    role: "user" | "assistant";
    content: string;
};

type ModelOption = {
    value: string;
    label: string;
};

type ChatResponse = {
    choices?: Array<{
        message?: {
            content?: string | Array<{ type?: string; text?: string }>;
        };
    }>;
};

type ApiErrorPayload = {
    detail?: string;
    message?: string;
    error?: string;
};

type ModelsPayload = {
    models?: unknown[];
    data?: unknown[];
};

type SessionApiCompat = {
    session: unknown;
    loading: boolean;
    error: Error | null;
    logout: () => Promise<unknown>;
    refresh?: () => Promise<void>;
    refreshSession?: () => Promise<void>;
};

function pickString(value: unknown) {
    return typeof value === "string" && value.trim() ? value.trim() : "";
}

function extractModels(payload: unknown) {
    if (Array.isArray(payload)) {
        return payload;
    }

    if (!payload || typeof payload !== "object") {
        return [];
    }

    const record = payload as ModelsPayload;

    if (Array.isArray(record.models)) {
        return record.models;
    }

    if (Array.isArray(record.data)) {
        return record.data;
    }

    return [];
}

function getCookieValue(name: string) {
    if (typeof document === "undefined") {
        return "";
    }

    const cookie = document.cookie
        .split("; ")
        .find((entry) => entry.startsWith(`${name}=`));

    if (!cookie) {
        return "";
    }

    return decodeURIComponent(cookie.slice(name.length + 1));
}

function resolveBasePath() {
    const envBasePath = pickString(process.env.NEXT_PUBLIC_APP_BASE_PATH);

    if (envBasePath) {
        return envBasePath.replace(/\/$/, "");
    }

    const cookieBasePath = pickString(getCookieValue("app-base-path"));

    if (cookieBasePath) {
        return cookieBasePath.replace(/\/$/, "");
    }

    if (typeof window === "undefined") {
        return "";
    }

    const appPathMatch = window.location.pathname.match(/^(.+\/runtime\/apps\/[^/]+)/);
    return appPathMatch?.[1] ?? "";
}

async function fetchWithBase(path: string, init?: RequestInit) {
    const basePath = resolveBasePath();
    return fetch(`${basePath}${path}`, {
        credentials: "include",
        ...init,
    });
}

function normalizeModels(models: unknown): ModelOption[] {
    if (!Array.isArray(models)) {
        return [];
    }

    const seen = new Set<string>();
    const options: ModelOption[] = [];

    for (const model of models) {
        if (typeof model === "string") {
            const value = model.trim();

            if (value && !seen.has(value)) {
                seen.add(value);
                options.push({ value, label: value });
            }
            continue;
        }

        if (!model || typeof model !== "object") {
            continue;
        }

        const record = model as Record<string, unknown>;
        const value =
            pickString(record.id) ||
            pickString(record.name) ||
            pickString(record.model) ||
            pickString(record.slug);

        if (!value || seen.has(value)) {
            continue;
        }

        const label =
            pickString(record.display_name) ||
            pickString(record.title) ||
            pickString(record.name) ||
            value;

        seen.add(value);
        options.push({ value, label });
    }

    return options;
}

function extractAssistantText(response: ChatResponse) {
    const content = response.choices?.[0]?.message?.content;

    if (typeof content === "string" && content.trim()) {
        return content.trim();
    }

    if (Array.isArray(content)) {
        const text = content
            .map((part) => (typeof part?.text === "string" ? part.text : ""))
            .join("")
            .trim();

        if (text) {
            return text;
        }
    }

    throw new Error("The selected model returned an empty response.");
}

async function extractErrorMessage(response: Response) {
    try {
        const payload = (await response.json()) as ApiErrorPayload;
        return (
            pickString(payload.detail) ||
            pickString(payload.message) ||
            pickString(payload.error) ||
            `Chat request failed with status ${response.status}.`
        );
    } catch {
        return `Chat request failed with status ${response.status}.`;
    }
}

function useAvailableModels() {
    const [models, setModels] = useState<unknown[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<Error | null>(null);

    async function loadModels() {
        setLoading(true);
        setError(null);

        try {
            const response = await fetchWithBase("/api/models");

            if (!response.ok) {
                throw new Error(`Model request failed with status ${response.status}.`);
            }

            const payload = await response.json();
            setModels(extractModels(payload));
        } catch (loadError) {
            setError(
                loadError instanceof Error
                    ? loadError
                    : new Error("Unable to load models."),
            );
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => {
        void loadModels();
    }, []);

    return {
        models,
        loading,
        error,
        refresh: loadModels,
    };
}

function Dashboard() {
    const sessionApi = useSession() as SessionApiCompat;
    const { session, loading, error, logout } = sessionApi;
    const {
        models,
        loading: modelsLoading,
        error: modelsError,
        refresh: refreshModels,
    } = useAvailableModels();
    const [selectedModel, setSelectedModel] = useState("");
    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [draft, setDraft] = useState("");
    const [isSending, setIsSending] = useState(false);
    const [sendError, setSendError] = useState("");
    const [modelNotice, setModelNotice] = useState("");
    const transcriptRef = useRef<HTMLDivElement>(null);
    const refreshSession = sessionApi.refreshSession ?? sessionApi.refresh;
    const modelOptions = normalizeModels(models);
    const trimmedDraft = draft.trim();
    const canSend =
        Boolean(selectedModel) &&
        Boolean(trimmedDraft) &&
        !isSending &&
        !modelsLoading;

    useEffect(() => {
        const transcript = transcriptRef.current;

        if (transcript) {
            transcript.scrollTop = transcript.scrollHeight;
        }
    }, [messages, isSending]);

    useEffect(() => {
        if (!selectedModel) {
            return;
        }

        const stillAvailable = modelOptions.some(
            (option) => option.value === selectedModel,
        );

        if (!stillAvailable) {
            setSelectedModel("");
            setModelNotice(
                "Your previously selected model is no longer available. Please choose another model.",
            );
        }
    }, [modelOptions, selectedModel]);

    async function handleRefreshModels() {
        setSendError("");
        await refreshModels();
    }

    async function handleSubmit() {
        if (!canSend) {
            return;
        }

        const userMessage: ChatMessage = {
            role: "user",
            content: trimmedDraft,
        };
        const nextMessages = [...messages, userMessage];

        setMessages(nextMessages);
        setDraft("");
        setSendError("");
        setIsSending(true);

        try {
            const response = await fetchWithBase("/api/chat", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({
                    model: selectedModel,
                    messages: nextMessages,
                }),
            });

            if (!response.ok) {
                throw new Error(await extractErrorMessage(response));
            }

            const payload = (await response.json()) as ChatResponse;
            const assistantText = extractAssistantText(payload);

            setMessages([
                ...nextMessages,
                {
                    role: "assistant",
                    content: assistantText,
                },
            ]);
        } catch (submitError) {
            setSendError(
                submitError instanceof Error
                    ? submitError.message
                    : "Unable to send your message right now.",
            );
        } finally {
            setIsSending(false);
        }
    }

    function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            void handleSubmit();
        }
    }

    return (
        <main className="min-h-screen bg-kw-bg p-6 max-w-7xl mx-auto">
            <NavBar currentPath="/" />

            <header className="mb-8 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
                <div>
                    <h1 className="terminal-header">{{name}}</h1>
                    <p className="text-kw-text-secondary text-sm mt-3 max-w-3xl leading-relaxed">
                        {{description}}. This starter already includes explicit
                        model selection, in-memory chat history, and authenticated
                        requests through the extension backend.
                    </p>
                </div>
                <div className="flex gap-3">
                    <button
                        className="btn-secondary"
                        onClick={() => void refreshSession?.()}
                    >
                        Refresh Session
                    </button>
                    <button className="btn-danger" onClick={() => void logout()}>
                        Logout
                    </button>
                </div>
            </header>

            <section className="mb-6 grid grid-cols-1 gap-2 lg:grid-cols-3">
                <div className="status-pill">
                    <span
                        className={`status-dot ${loading ? "bg-kw-warning animate-pulse" : session ? "bg-kw-success" : "bg-kw-text-secondary/50"}`}
                    />
                    <span className="text-kw-text-secondary">Session:</span>
                    <span className="text-kw-text-primary">
                        {loading ? "loading..." : session ? "active" : "none"}
                    </span>
                </div>
                <div className="status-pill">
                    <span
                        className={`status-dot ${modelsLoading ? "bg-kw-warning animate-pulse" : modelOptions.length > 0 ? "bg-kw-success" : "bg-kw-text-secondary/50"}`}
                    />
                    <span className="text-kw-text-secondary">Models:</span>
                    <span className="text-kw-text-primary">
                        {modelsLoading ? "loading..." : `${modelOptions.length} available`}
                    </span>
                </div>
                <div className="status-pill">
                    <span
                        className={`status-dot ${error || modelsError || sendError ? "bg-kw-error" : "bg-kw-text-secondary/50"}`}
                    />
                    <span className="text-kw-text-secondary">Issues:</span>
                    <span className="text-kw-text-primary">
                        {error?.message || modelsError?.message || sendError || "(none)"}
                    </span>
                </div>
            </section>

            <section className="chat-shell">
                <div className="chat-toolbar">
                    <div className="flex-1 min-w-0">
                        <label htmlFor="model-select" className="card-title mb-2">
                            Model
                        </label>
                        <select
                            id="model-select"
                            className="chat-select"
                            value={selectedModel}
                            onChange={(event) => {
                                setSelectedModel(event.target.value);
                                setModelNotice("");
                                setSendError("");
                            }}
                            disabled={modelsLoading || modelOptions.length === 0}
                        >
                            <option value="">Select a Kamiwaza model</option>
                            {modelOptions.map((option) => (
                                <option key={option.value} value={option.value}>
                                    {option.label}
                                </option>
                            ))}
                        </select>
                    </div>
                    <div className="flex gap-3 self-end">
                        <button
                            className="btn-secondary"
                            onClick={() => void handleRefreshModels()}
                        >
                            Refresh Models
                        </button>
                        <button
                            className="btn-primary"
                            onClick={() => {
                                setMessages([]);
                                setSendError("");
                            }}
                            disabled={messages.length === 0 && !sendError}
                        >
                            Clear Chat
                        </button>
                    </div>
                </div>

                <div className="flex flex-col gap-3">
                    {modelsError ? (
                        <div className="chat-alert chat-alert-error">
                            Unable to load models: {modelsError.message}
                        </div>
                    ) : null}
                    {!modelsLoading && modelOptions.length === 0 ? (
                        <div className="chat-alert">
                            No Kamiwaza models are available for this extension right now.
                        </div>
                    ) : null}
                    {modelNotice ? <div className="chat-alert">{modelNotice}</div> : null}
                    {!selectedModel && modelOptions.length > 0 && !modelsLoading ? (
                        <div className="chat-alert">
                            Choose a model before sending your first message.
                        </div>
                    ) : null}
                    {sendError ? (
                        <div className="chat-alert chat-alert-error">{sendError}</div>
                    ) : null}
                </div>

                <div ref={transcriptRef} className="chat-transcript">
                    {messages.length === 0 ? (
                        <div className="chat-empty-state">
                            <h2 className="card-title mb-3">Conversation</h2>
                            <p className="text-sm text-kw-text-secondary leading-relaxed">
                                Start a conversation once you have selected a model.
                                Messages stay on this page only and are not persisted
                                after refresh.
                            </p>
                        </div>
                    ) : (
                        <div className="flex flex-col gap-4">
                            {messages.map((message, index) => (
                                <article
                                    key={`${message.role}-${index}`}
                                    className={`chat-message ${message.role === "user" ? "chat-message-user" : "chat-message-assistant"}`}
                                >
                                    <div className="chat-message-meta">
                                        {message.role === "user" ? "You" : "Assistant"}
                                    </div>
                                    <p className="chat-message-body">{message.content}</p>
                                </article>
                            ))}
                            {isSending ? (
                                <article className="chat-message chat-message-assistant">
                                    <div className="chat-message-meta">Assistant</div>
                                    <p className="chat-message-body text-kw-text-secondary">
                                        Thinking...
                                    </p>
                                </article>
                            ) : null}
                        </div>
                    )}
                </div>

                <div className="chat-composer">
                    <textarea
                        value={draft}
                        onChange={(event) => setDraft(event.target.value)}
                        onKeyDown={handleComposerKeyDown}
                        className="chat-textarea"
                        rows={4}
                        placeholder={
                            selectedModel
                                ? "Ask something..."
                                : "Select a model to start chatting..."
                        }
                        disabled={isSending}
                    />
                    <div className="flex items-center justify-between gap-3">
                        <p className="text-xs text-kw-text-secondary">
                            Enter sends. Shift+Enter adds a new line.
                        </p>
                        <button
                            className="btn-primary"
                            onClick={() => void handleSubmit()}
                            disabled={!canSend}
                        >
                            {isSending ? "Sending..." : "Send"}
                        </button>
                    </div>
                </div>
            </section>
        </main>
    );
}

export default function Home() {
    // Under USE_AUTH=false the backend's /session returns the canonical
    // anonymous Identity (`name === "Anonymous"`, `is_authenticated === false`).
    // AuthGuard would otherwise fetch /auth/login-url and wait for it to
    // resolve before rendering — wasted round-trip and a "Verifying session…"
    // flash on every local-dev page load (ENG-3889 P6 / §4.8 P6).
    const sessionApi = useSession() as SessionApiCompat;
    const { session, loading } = sessionApi;
    const sessionView = session as
        | { isAuthenticated?: boolean; name?: string | null }
        | null;
    const isAnonymousLocalDev =
        !loading
        && sessionView !== null
        && sessionView.isAuthenticated === false
        && sessionView.name === "Anonymous";

    if (isAnonymousLocalDev) {
        return <Dashboard />;
    }

    const Guard = AuthGuard as ComponentType<{
        children: ReactNode;
        fallback?: ReactNode;
        loadingComponent?: ReactNode;
    }>;

    return (
        <Guard
            fallback={
                <main className="min-h-screen bg-kw-bg flex items-center justify-center">
                    <div className="text-kw-primary font-mono text-sm animate-pulse">
                        Verifying session...
                    </div>
                </main>
            }
            loadingComponent={
                <main className="min-h-screen bg-kw-bg flex items-center justify-center">
                    <div className="text-kw-primary font-mono text-sm animate-pulse">
                        Verifying session...
                    </div>
                </main>
            }
        >
            <Dashboard />
        </Guard>
    );
}
