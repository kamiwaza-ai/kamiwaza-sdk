"use client";

import {
    AuthGuard,
    useModels,
    useSession,
} from "@kamiwaza-ai/extensions-lib/client";
import { NavBar } from "../components/NavBar";

function Dashboard() {
    const { session, loading, error, logout, refresh } = useSession();
    const {
        models,
        loading: modelsLoading,
        error: modelsError,
        refresh: refreshModels,
    } = useModels();

    return (
        <main className="min-h-screen bg-kw-bg p-6 max-w-7xl mx-auto">
            <NavBar currentPath="/" />

            <header className="mb-8">
                <h1 className="terminal-header">{{name}}</h1>
                <p className="text-kw-text-secondary text-sm mt-3 max-w-3xl leading-relaxed">
                    {{description}}
                </p>
            </header>

            <section className="card mb-6">
                <h2 className="card-title">Status</h2>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                    <div className="flex items-center gap-2 bg-kw-bg rounded px-3 py-2 border border-kw-elevated text-xs font-mono">
                        <span className={`status-dot ${loading ? "bg-kw-warning animate-pulse" : session ? "bg-kw-success" : "bg-kw-text-secondary/50"}`} />
                        <span className="text-kw-text-secondary">Session:</span>
                        <span className="text-kw-text-primary">{loading ? "loading..." : session ? "active" : "none"}</span>
                    </div>
                    <div className="flex items-center gap-2 bg-kw-bg rounded px-3 py-2 border border-kw-elevated text-xs font-mono">
                        <span className={`status-dot ${modelsLoading ? "bg-kw-warning animate-pulse" : models.length > 0 ? "bg-kw-success" : "bg-kw-text-secondary/50"}`} />
                        <span className="text-kw-text-secondary">Models:</span>
                        <span className="text-kw-text-primary">{modelsLoading ? "loading..." : `${models.length} found`}</span>
                    </div>
                    <div className="flex items-center gap-2 bg-kw-bg rounded px-3 py-2 border border-kw-elevated text-xs font-mono">
                        <span className={`status-dot ${error || modelsError ? "bg-kw-error" : "bg-kw-text-secondary/50"}`} />
                        <span className="text-kw-text-secondary">Errors:</span>
                        <span className="text-kw-text-primary">{error?.message || modelsError?.message || "(none)"}</span>
                    </div>
                </div>
                <div className="flex gap-3 mt-4">
                    <button className="btn-primary" onClick={() => void refresh()}>
                        Refresh Session
                    </button>
                    <button className="btn-secondary" onClick={() => void refreshModels()}>
                        Refresh Models
                    </button>
                    <button className="btn-danger" onClick={() => void logout()}>
                        Logout
                    </button>
                </div>
            </section>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <section className="card">
                    <h2 className="card-title">Session</h2>
                    <pre className="json-pre">
                        {JSON.stringify(session, null, 2) ?? "null"}
                    </pre>
                </section>
                <section className="card">
                    <h2 className="card-title">Available Models</h2>
                    <pre className="json-pre">
                        {JSON.stringify(models, null, 2)}
                    </pre>
                </section>
            </div>
        </main>
    );
}

export default function Home() {
    return (
        <AuthGuard
            fallback={
                <main className="min-h-screen bg-kw-bg flex items-center justify-center">
                    <div className="text-kw-primary font-mono text-sm animate-pulse">
                        Verifying session...
                    </div>
                </main>
            }
        >
            <Dashboard />
        </AuthGuard>
    );
}
