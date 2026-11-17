"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { API_BASE } from "@/lib/api";

const links = [
  { href: "/", label: "Dashboard" },
  { href: "/diff", label: "Diff playground" },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <header className="sticky top-0 z-20 border-b border-ink-700/80 bg-ink-950/85 backdrop-blur">
      <div className="mx-auto flex w-full max-w-7xl items-center justify-between px-5 py-3">
        <Link href="/" className="group flex items-center gap-2.5">
          <span className="grid h-7 w-7 place-items-center rounded-md border border-signal-green/40 bg-signal-green/10 font-mono text-sm text-signal-green">
            ⟳
          </span>
          <span className="text-sm font-semibold tracking-tight">PatchPilot</span>
          <span className="hidden text-[11px] text-mist-500 sm:inline">verified API migrations</span>
        </Link>
        <nav className="flex items-center gap-1">
          {links.map((link) => {
            const active = link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`rounded-md px-3 py-1.5 text-sm transition ${
                  active ? "bg-ink-800 text-mist-100" : "text-mist-500 hover:text-mist-100"
                }`}
              >
                {link.label}
              </Link>
            );
          })}
          <a
            href={`${API_BASE}/docs`}
            target="_blank"
            rel="noreferrer"
            className="rounded-md px-3 py-1.5 text-sm text-mist-500 transition hover:text-mist-100"
          >
            API docs
          </a>
        </nav>
      </div>
    </header>
  );
}
