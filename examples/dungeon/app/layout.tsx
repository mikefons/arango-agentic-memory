import type { Metadata } from "next";
import { Geist, Geist_Mono, Fraunces } from "next/font/google";
import "./globals.css";

const sans = Geist({ subsets: ["latin"], variable: "--font-sans" });
const mono = Geist_Mono({ subsets: ["latin"], variable: "--font-mono" });
const serif = Fraunces({ subsets: ["latin"], variable: "--font-serif" });

export const metadata: Metadata = {
  title: "Memory Dungeon",
  description:
    "A text-adventure where the world persists and the NPCs lie — a reference agent for the ArangoDB agentic memory core.",
};

// Set the theme before first paint to avoid a flash (reads localStorage, then OS).
const themeScript = `
(function () {
  try {
    var s = localStorage.getItem('md-theme');
    var prefersLight = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches;
    document.documentElement.setAttribute('data-theme', s || (prefersLight ? 'light' : 'dark'));
  } catch (e) {
    document.documentElement.setAttribute('data-theme', 'dark');
  }
})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className={`${sans.variable} ${mono.variable} ${serif.variable}`}>
        <div className="grain" />
        {children}
      </body>
    </html>
  );
}
