import type { Metadata, Viewport } from "next";
import { Nav } from "@/components/Nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "PayGuard Dashboard",
  description: "A safety layer for AI agents that move money: every tool call, judged live.",
};
export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-bg font-sans text-ink">
        <Nav />
        <main className="mx-auto max-w-6xl px-4 pb-20 pt-8 sm:px-6">{children}</main>
      </body>
    </html>
  );
}
