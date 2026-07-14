import type { GameState } from "@/lib/tools";
import { ledger, metNpcs, trustOf } from "@/lib/world";
import type { GuildSave } from "@/lib/guild";
import { LedgerPanel } from "./LedgerPanel";

export function Dossier({ game, guild }: { game: GameState; guild: GuildSave }) {
  const ev = { inventory: game.inventory, heardClaims: game.heardClaims };
  const npcs = metNpcs(game.heardClaims);
  const entries = ledger(ev, game.caughtClaims);

  return (
    <aside className="pane dossier">
      <div className="pane-head">
        <span className="pane-title">Dossier</span>
        <span className="pane-meta">{game.caughtClaims.length} caught</span>
      </div>

      <div className="dossier-body">
        {/* Guild meta-progression (E-5) */}
        <LedgerPanel game={game} guild={guild} />

        {/* Satchel */}
        <div className="section">
          <h3>Satchel <span className="count">{game.inventory.length}</span></h3>
          {game.inventory.length === 0 ? (
            <div className="empty">empty — take something</div>
          ) : (
            <div className="inv">
              {game.inventory.map((it) => (
                <span className="chip item" key={it}>◇ {it}</span>
              ))}
            </div>
          )}
        </div>

        {/* Persons of interest */}
        <div className="section">
          <h3>Persons of Interest <span className="count">{npcs.length}</span></h3>
          {npcs.length === 0 ? (
            <div className="empty">talk to someone</div>
          ) : (
            npcs.map((npc) => {
              const trust = trustOf(npc, game.caughtClaims);
              const tone = trust < 40 ? "low" : trust > 65 ? "high" : "";
              return (
                <div className="npc" key={npc.id}>
                  <div className="npc-row">
                    <div className="npc-name">
                      <span className="avatar">{npc.name[0]}</span>
                      <span><span className="n">{npc.name}</span> <span className="role">· {npc.role}</span></span>
                    </div>
                    <span className="trust-val">{trust}%</span>
                  </div>
                  <div className={`meter ${tone}`}><i style={{ width: `${trust}%` }} /></div>
                </div>
              );
            })
          )}
        </div>

        {/* Contradiction ledger */}
        <div className="section">
          <h3>Contradiction Ledger <span className="count">{entries.length}</span></h3>
          {entries.length === 0 ? (
            <div className="empty">nothing yet — gather testimony</div>
          ) : (
            <div className="ledger">
              {entries.map(({ npc, claim, status }) => (
                <div className={`entry ${status}`} key={claim.id}>
                  <div className="entry-top">
                    <span className={`entry-tag ${status}`}>
                      {status === "caught" ? "lie caught" : "doesn't add up"}
                    </span>
                    <span className="entry-who">{npc.name}</span>
                  </div>
                  <div className={`claim ${status === "caught" ? "false" : ""}`}>“{claim.text}”</div>
                  {claim.refutedBy && (
                    <div className="claim refute"><span className="vs">refuted by →</span> {claim.refutedBy}</div>
                  )}
                  <div className="entry-foot">
                    {status === "caught" ? (
                      <><span className="k">superseded</span> · valid_time invalidated</>
                    ) : (
                      <><span className="k pend">needs_review</span> · confront to resolve</>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </aside>
  );
}
