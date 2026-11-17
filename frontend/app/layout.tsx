import type { Metadata } from "next";
import "./globals.css";
import { Nav } from "@/components/Nav";

export const metadata: Metadata = {
  title: "PatchPilot — verified API migrations",
  description:
    "Detect breaking OpenAPI changes, find the Python code that depends on them, and generate a migration the test suite proves.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <Nav />
        <main className="mx-auto w-full max-w-7xl px-5 pb-24 pt-8">{children}</main>
        <footer className="border-t border-ink-800 py-6 text-center font-mono text-[11px] text-mist-500">
          PatchPilot · OpenAPI diff → AST analysis → LibCST patch → sandboxed verification
        </footer>
      </body>
    </html>
  );
}
