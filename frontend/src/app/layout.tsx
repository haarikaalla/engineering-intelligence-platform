import type { Metadata } from "next";
import Link from "next/link";
import { Network } from "lucide-react";
import "./globals.css";

export const metadata: Metadata = {
  title: "Engineering Intelligence Platform",
  description: "AI-powered repository understanding, impact analysis, security and test intelligence.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <header className="border-b border-slate-800 bg-slate-900 text-white">
          <div className="mx-auto flex max-w-[1500px] items-center justify-between px-6 py-3">
            <Link href="/" className="flex items-center gap-2 font-semibold">
              <span className="rounded-lg bg-brand-600 p-1.5">
                <Network className="h-5 w-5" />
              </span>
              Engineering Intelligence Platform
            </Link>
            <nav className="text-sm text-slate-300">
              <Link href="/" className="hover:text-white">
                Repositories
              </Link>
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-[1500px] px-6 py-6">{children}</main>
      </body>
    </html>
  );
}
