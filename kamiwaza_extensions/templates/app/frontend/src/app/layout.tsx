import type { Metadata } from "next";
import { Montserrat, Fira_Code } from "next/font/google";
import { appAsset, KamiwazaRuntimeBootstrap } from "@kamiwaza-ai/extensions-lib/runtime";
import { Providers } from "./providers";
import "./globals.css";

const montserrat = Montserrat({
  subsets: ["latin"],
  variable: "--font-montserrat",
  display: "swap",
});

const firaCode = Fira_Code({
  subsets: ["latin"],
  variable: "--font-fira-code",
  display: "swap",
});

// public/-root assets must go through appAsset() (or static imports) so they
// carry the deployment prefix — Next's basePath does NOT rewrite raw
// public-root strings.
export const metadata: Metadata = {
  title: "{{name}} | Kamiwaza Extension",
  description: "{{description}}",
  icons: {
    icon: appAsset("/kmza-icon.png"),
    shortcut: appAsset("/kmza-icon.png"),
    apple: appAsset("/kmza-icon.png"),
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${montserrat.variable} ${firaCode.variable}`}>
      <head>
        {/* Installs globalThis.__KAMIWAZA_RUNTIME__ ({routingMode, appPath})
            before hydration. Keep this layout statically prerenderable — the
            inline value is build-variant text that boot relocation corrects;
            do not read deployment env or headers() here. */}
        <KamiwazaRuntimeBootstrap />
      </head>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
