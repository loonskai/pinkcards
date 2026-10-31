import { useState } from "react";
import { api, Card } from "./api";

const CLOZE = /\{\{c(\d+)::(.*?)\}\}/gs;
const front = (t: string) => t.replace(CLOZE, '<span class="cloze">[…]</span>');
const back = (t: string) => t.replace(CLOZE, '<span class="cloze shown">$2</span>');

function clozeIssues(t: string): string[] {
  const parts = [...t.matchAll(CLOZE)];
  const e: string[] = [];
  if (!parts.length) e.push("no {{c1::…}}");
  if (parts.some((p) => p[1] !== "1")) e.push("only c1 allowed");
  if (/\{\{|\}\}/.test(t.replace(CLOZE, ""))) e.push("unbalanced braces");
  return e;
}

type Props = { card: Card; selected: boolean; onToggle: () => void; onChange: (c: Card) => void; onReload: () => void; onMsg: (m: string) => void };

export function CardView({ card, selected, onToggle, onChange, onReload, onMsg }: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Partial<Card>>({});
  const [flip, setFlip] = useState(false);
  const [regen, setRegen] = useState("");
  const [lang, setLang] = useState("");
  const [busy, setBusy] = useState(false);

  const guard = async (fn: () => Promise<any>) => {
    setBusy(true);
    try { await fn(); } catch (e) { onMsg(String(e)); }
    setBusy(false);
  };
  const c = { ...card, ...draft } as Card;
  const live = editing ? clozeIssues(c.text ?? "") : card.validation_errors;
  const valid = card.validation_errors.length === 0;
  const set = (k: keyof Card, v: any) => setDraft((d) => ({ ...d, [k]: v }));
  const g = c.grammar ?? {};
  const setG = (k: string, v: string) => set("grammar", { ...g, [k]: v || null });

  const save = () => guard(async () => { onChange(await api.edit(card.id, draft)); setDraft({}); setEditing(false); });
  const act = (fn: () => Promise<Card>) => guard(async () => { onChange(await fn()); });

  return (
    <div className={"card " + card.status}>
      <div className="card-head">
        {card.status === "generated" && <input type="checkbox" checked={selected} onChange={onToggle} disabled={!valid} />}
        <span className="badge">{card.kind}</span>
        {card.language_confidence === "low" && <span className="badge warn">low confidence: {card.language_reason}</span>}
        {card.already_known === 1 && <span className="badge warn">already known</span>}
        {card.status === "failed" && <span className="badge bad">failed</span>}
        <span className="muted">{card.deck ?? "no deck"} · #{card.id}</span>
      </div>

      {card.error && <div className="errors">{card.error}</div>}
      {live.length > 0 && <div className="errors">{live.join("; ")}</div>}

      {card.text && !editing && (
        <div className="preview" onClick={() => setFlip(!flip)} title="click to flip">
          <div dangerouslySetInnerHTML={{ __html: flip ? back(card.text) : front(card.text) }} />
          <div className="section" dangerouslySetInnerHTML={{ __html: card.translation ?? "" }} />
          {card.hint && <div className="section">{card.hint}</div>}
          {flip && card.note && <div className="section note" dangerouslySetInnerHTML={{ __html: card.note }} />}
        </div>
      )}
      {card.text && !editing && (
        <div className="reviewer muted">
          form: <b>{card.form_used}</b> ← {card.expression}{card.is_base_form ? " (base)" : ""} · meaning: {card.meaning_gloss_ru}
        </div>
      )}

      {editing && (
        <div className="edit">
          <label>Text<textarea value={c.text ?? ""} onChange={(e) => set("text", e.target.value)} /></label>
          <label>Expression (base form)<input value={c.expression ?? ""} onChange={(e) => set("expression", e.target.value)} /></label>
          <label>Form used<input value={c.form_used ?? ""} onChange={(e) => set("form_used", e.target.value)} /></label>
          <label className="inline"><input type="checkbox" checked={!!c.is_base_form} onChange={(e) => set("is_base_form", e.target.checked ? 1 : 0)} /> is base form</label>
          <label>Translation<textarea value={c.translation ?? ""} onChange={(e) => set("translation", e.target.value)} /></label>
          <label>HINT<input value={c.hint ?? ""} onChange={(e) => set("hint", e.target.value)} /></label>
          <div className="row">
            <label>Gender<select value={g.gender ?? ""} onChange={(e) => setG("gender", e.target.value)}>
              <option value="">–</option><option>m</option><option>f</option><option>n</option></select></label>
            <label>Article<select value={g.article ?? ""} onChange={(e) => setG("article", e.target.value)}>
              <option value="">–</option><option>le</option><option>la</option><option>l'</option></select></label>
            <label>Aspect pair<input value={g.aspect_pair ?? ""} onChange={(e) => setG("aspect_pair", e.target.value)} /></label>
          </div>
          <p className="muted">NOTE is rebuilt on save from base form / grammar.</p>
          <button className="primary" disabled={busy || clozeIssues(c.text ?? "").length > 0} onClick={save}>Save</button>
          <button onClick={() => { setEditing(false); setDraft({}); }}>Cancel</button>
        </div>
      )}

      <div className="actions">
        {card.status === "rejected" ? (
          <button disabled={busy} onClick={() => guard(async () => { await api.unreject(card.id); onReload(); })}>Un-reject</button>
        ) : (<>
          <button className="primary" disabled={busy || !valid || card.status === "approved"}
            onClick={() => guard(async () => {
              const r = await api.approve([card.id]);
              if (Object.keys(r.refused).length) onMsg(JSON.stringify(r.refused));
              onReload();
            })}>Approve</button>
          <button disabled={busy} onClick={() => setEditing(!editing)}>Edit</button>
          <input className="small" placeholder="regenerate instruction (optional)" value={regen} onChange={(e) => setRegen(e.target.value)} />
          <button disabled={busy} onClick={() => act(() => api.regenerate(card.id, regen))}>Regenerate</button>
          <select value={lang} onChange={(e) => setLang(e.target.value)}>
            <option value="">language…</option><option>pl</option><option>fr</option><option>en</option></select>
          <button disabled={busy || !lang} onClick={() => act(() => api.language(card.id, lang))}>Change language</button>
          <button disabled={busy || !card.expression} onClick={() => guard(async () => { await api.anotherMeaning(card.id); onReload(); })}>Another meaning</button>
          <button disabled={busy || !card.expression} className={card.already_known ? "primary" : ""}
            onClick={() => guard(async () => { await api.newForm(card.id); onReload(); })}>Add new form</button>
          <button disabled={busy} className="danger" onClick={() => guard(async () => { await api.reject(card.id); onReload(); })}>Reject</button>
        </>)}
      </div>
    </div>
  );
}
