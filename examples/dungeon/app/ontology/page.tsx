import { OntologyReview } from "@/components/OntologyReview";
import { ThemeToggle } from "@/components/ThemeToggle";
import { TabNav } from "@/components/TabNav";

export default function OntologyPage() {
  return (
    <div className="app graph-app">
      <header>
        <div className="header-left">
          <div className="brand">
            <span className="glyph">
              <svg width="20" height="18" viewBox="0 0 20 18" fill="none">
                <path d="M10 1 L19 17 L1 17 Z" stroke="currentColor" strokeWidth="1.4" fill="currentColor" fillOpacity="0.12" />
                <circle cx="10" cy="12" r="1.6" fill="currentColor" />
              </svg>
            </span>
            <span className="wordmark">Memory&nbsp;<b>Dungeon</b></span>
          </div>
          <TabNav />
        </div>
        <div className="crumbs"><span className="here">ontology</span></div>
        <div className="head-right">
          <span className="pill"><span className="dot" />ArangoDB</span>
          <ThemeToggle />
        </div>
      </header>
      <main className="graph-main onto-main">
        <OntologyReview />
      </main>
    </div>
  );
}
