import Link from "next/link";
import { NavBar } from "../../components/NavBar";

export default function LoggedOutPage() {
    return (
        <main className="min-h-screen bg-kw-bg p-6 max-w-7xl mx-auto">
            <NavBar currentPath="/logged-out" />

            <div className="flex flex-col items-center justify-center mt-24">
                <div className="card max-w-md text-center p-8">
                    <h1 className="font-heading text-2xl font-bold text-kw-text-primary mb-4">
                        Logged out
                    </h1>
                    <p className="text-kw-text-secondary text-sm mb-6 leading-relaxed">
                        You have been logged out of {{name}}.
                    </p>
                    <Link href="/" className="btn-primary inline-block">
                        Back to Chat
                    </Link>
                </div>
            </div>
        </main>
    );
}
