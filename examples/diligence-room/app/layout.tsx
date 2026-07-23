import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "The Due-Diligence Room",
  description:
    "A multi-agent business demo for the ArangoDB agentic memory core — specialist agents investigate a target, disagree, and reconcile over shared, bi-temporal memory.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
