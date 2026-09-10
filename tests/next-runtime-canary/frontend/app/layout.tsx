import { appAsset, KamiwazaRuntimeBootstrap } from "@kamiwaza-ai/extensions-lib/runtime";
import "./globals.css";

export const metadata = {
    title: "kz-next-runtime-canary",
    icons: { icon: appAsset("/kmza-icon.svg") },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
    return (
        <html lang="en">
            <head>
                <KamiwazaRuntimeBootstrap />
            </head>
            <body>{children}</body>
        </html>
    );
}
