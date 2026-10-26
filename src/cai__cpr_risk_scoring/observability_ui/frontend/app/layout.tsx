import "./globals.css";
import type { Metadata } from "next";
import { Providers } from "@/components/providers";
import { Nav } from "@/components/nav";

export const metadata: Metadata = {
  title: "AI Auditor — Observability",
  description: "Operations, risk, executive and change-review observability for the AI Auditor.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <Providers>
          <div className="mx-auto max-w-[1400px] px-4 py-4">
            <Nav />
            <main>{children}</main>
          </div>
        </Providers>
      </body>
    </html>
  );
}
