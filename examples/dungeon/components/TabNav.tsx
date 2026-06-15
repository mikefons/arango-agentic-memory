"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export function TabNav() {
  const path = usePathname();
  return (
    <nav className="tabnav">
      <Link className={`tab ${path === "/" ? "active" : ""}`} href="/">Play</Link>
      <Link className={`tab ${path?.startsWith("/graph") ? "active" : ""}`} href="/graph">Graph</Link>
      <Link className={`tab ${path?.startsWith("/ontology") ? "active" : ""}`} href="/ontology">Ontology</Link>
    </nav>
  );
}
