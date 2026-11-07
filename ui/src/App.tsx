import { useCallback, useEffect, useState } from "react";
import { api, Card, Status } from "./api";
import { CardView } from "./CardView";

const FILTERS = [
  { id: "generated", label: "Generated" }, { id: "flagged", label: "Flagged" },
  { id: "approved", label: "Approved" }, { id: "failed", label: "Failed" },
  { id: "rejected", label: "Rejected" }, { id: "unsupported", label: "Unsupported" },
  { id: "exported", label: "Exported" },
];

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [filter, setFilter] = useState("generated");
  const [cards, setCards] = useState<Card[]>([]);
  const [unsupported, setUnsupported] = useState<any[]>([]);
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string>("");
  const [tab, setTab] = useState<string>(() => localStorage.getItem("lang-tab") ?? "");

  const loadStatus = useCallback(() => api.status().then(setStatus).catch((e) => setMsg(String(e))), []);
  const load = useCallback(async () => {
    if (filter === "unsupported") { setUnsupported(await api.unsupported()); setCards([]); return; }
    const q: Record<string, string> = filter === "flagged" ? { status: "generated", flagged: "true" } : { status: filter };
    setCards(await api.cards(q)); setSel(new Set());
  }, [filter]);

  useEffect(() => { load().catch((e) => setMsg(String(e))); }, [load]);
  useEffect(() => { loadStatus(); const t = setInterval(loadStatus, 15000); return () => clearInterval(t); }, [loadStatus]);

  const run = async (name: string, fn: () => Promise<any>) => {
    setBusy(name); setMsg("");
    try { const r = await fn(); setMsg(`${name}: ${JSON.stringify(r)}`); } catch (e) { setMsg(`${name} failed: ${e}`); }
    setBusy(null); await load(); loadStatus();
  };

  const unsupportedLang = (n: any) => String(n.status_reason ?? "").replace("language:", "") || "?";
  const langOf = (c: Card) => c.language ?? "?";
  const items: { lang: string }[] = filter === "unsupported"
    ? unsupported.map((n) => ({ lang: unsupportedLang(n) })) : cards.map((c) => ({ lang: langOf(c) }));
  const counts = items.reduce<Record<string, number>>((a, i) => { a[i.lang] = (a[i.lang] ?? 0) + 1; return a; }, {});
  const langs = Object.keys(counts).sort((a, b) => (a === "?" ? 1 : b === "?" ? -1 : a.localeCompare(b)));
  const activeTab = langs.includes(tab) ? tab : langs[0] ?? "";
  const pickTab = (l: string) => { setTab(l); localStorage.setItem("lang-tab", l); setSel(new Set()); window.scrollTo({ top: 0 }); };
  const visible = cards.filter((c) => langOf(c) === activeTab);
  const selectable = visible.filter((c) => c.status === "generated" && c.validation_errors.length === 0);
  const allSelected = selectable.length > 0 && selectable.every((c) => sel.has(c.id));
  const toggleAll = () => setSel(allSelected ? new Set() : new Set(selectable.map((c) => c.id)));
  const toggle = (id: number) => setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const replace = (c: Card) => setCards((cs) => cs.map((x) => (x.id === c.id ? c : x)));

  return (
    <div className="app">
      <header className="statusbar">
        {status && Object.entries(status.checks).map(([k, c]) => (
          <span key={k} className={"pill " + (c.ok ? "ok" : "bad")} title={c.message}>
            {c.label}: {c.ok ? "ok" : "down"}
          </span>
        ))}
      </header>
      <div className="toolbar">
        <button disabled={!status?.sync.enabled || !!busy} title={status?.sync.reason ?? ""}
          onClick={() => run("Sync", api.sync)}>{busy === "Sync" ? "Syncing…" : "Sync"}</button>
        <button disabled={!status?.push.enabled || !!busy} title={status?.push.reason ?? ""}
          onClick={() => run("Push", api.push)}>{busy === "Push" ? "Pushing…" : "Push to Anki"}</button>
        <button disabled={!!busy} onClick={() => run("Export .apkg", api.exportApkg)}>Export .apkg</button>
        <button disabled={!!busy} onClick={() => run("Confirm import", api.confirmExport)}>Confirm .apkg imported</button>
        {status && !status.sync.enabled && <span className="reason">Sync disabled: {status.sync.reason}</span>}
        {status && !status.push.enabled && <span className="reason">Push disabled: {status.push.reason}</span>}
      </div>
      {msg && <div className="msg">{msg}</div>}
      <nav className="filters">
        {FILTERS.map((f) => <button key={f.id} className={f.id === filter ? "active" : ""} onClick={() => setFilter(f.id)}>{f.label}</button>)}
        {sel.size > 0 && <button className="primary" onClick={() => run("Approve", async () => {
          const r = await api.approve([...sel]); return { approved: r.approved.length, refused: r.refused };
        })}>Approve selected ({sel.size})</button>}
      </nav>
      {langs.length > 0 && (
        <div className="tabs" role="tablist">
          {langs.map((l) => (
            <button key={l} role="tab" aria-selected={l === activeTab} className={"tab" + (l === activeTab ? " active" : "")}
              onClick={() => pickTab(l)}>
              {l === "?" ? "Unknown" : l.toUpperCase()} <span className="count">{counts[l]}</span>
            </button>
          ))}
        </div>
      )}
      {filter !== "unsupported" && selectable.length > 0 && (
        <label className="selectall"><input type="checkbox" checked={allSelected} onChange={toggleAll} /> Select all valid in {activeTab.toUpperCase()} ({selectable.length})</label>
      )}
      {filter === "unsupported" && unsupported.filter((n) => unsupportedLang(n) === activeTab).map((n) => (
        <div key={n.id} className="card"><b>{n.text}</b> <span className="muted">{n.status_reason} · {n.book_title ?? "Notes"}</span></div>
      ))}
      {visible.map((c) => <CardView key={c.id} card={c} selected={sel.has(c.id)} onToggle={() => toggle(c.id)}
        onChange={replace} onReload={load} onMsg={setMsg} />)}
      {!langs.length && <p className="muted">Nothing here.</p>}
    </div>
  );
}
