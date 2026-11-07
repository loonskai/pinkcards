export type Card = {
  id: number; source_note_id: string; parent_card_id: number | null; kind: string;
  language: string | null; language_confidence: string | null; language_reason: string | null;
  text: string | null; expression: string | null; form_used: string | null; is_base_form: number | null;
  grammar: { gender?: string | null; article?: string | null; aspect_pair?: string | null } | null;
  translation: string | null; hint: string | null; note: string | null; tags: string[] | null;
  deck: string | null; meaning_gloss_ru: string | null; already_known: number; status: string;
  error: string | null; audio_url: string | null; audio_error: string | null; audio_enabled: boolean;
  audio_text_hash: string | null; details_enabled: boolean; details_stale: boolean; validation_errors: string[]; flagged: boolean;
};
export type Check = { label: string; ok: boolean; message: string };
export type Status = {
  checks: Record<string, Check>;
  sync: { enabled: boolean; reason: string | null };
  push: { enabled: boolean; reason: string | null };
};

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method, headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    let d = r.statusText;
    try { d = (await r.json()).detail ?? d; } catch { /* ignore */ }
    throw new Error(String(d));
  }
  return r.json();
}

export const api = {
  status: () => req<Status>("GET", "/api/status"),
  sync: () => req<any>("POST", "/api/sync"),
  cards: (q: Record<string, string>) => req<Card[]>("GET", "/api/cards?" + new URLSearchParams(q)),
  unsupported: () => req<any[]>("GET", "/api/notes/unsupported"),
  edit: (id: number, f: Partial<Card>) => req<Card>("PATCH", `/api/cards/${id}`, f),
  approve: (ids: number[]) => req<{ approved: number[]; refused: Record<string, string> }>("POST", "/api/cards/approve", { ids }),
  reject: (id: number) => req<any>("POST", `/api/cards/${id}/reject`),
  unreject: (id: number) => req<any>("POST", `/api/cards/${id}/unreject`),
  regenerate: (id: number, instruction: string) => req<Card>("POST", `/api/cards/${id}/regenerate`, { instruction: instruction || null }),
  language: (id: number, language: string) => req<Card>("POST", `/api/cards/${id}/language`, { language }),
  anotherMeaning: (id: number) => req<Card>("POST", `/api/cards/${id}/another-meaning`),
  newForm: (id: number) => req<Card>("POST", `/api/cards/${id}/new-form`),
  regenerateAudio: (id: number) => req<Card & { anki: { updated: boolean; error?: string; reason?: string } | null }>(
    "POST", `/api/cards/${id}/regenerate-audio`),
  regenerateDetails: (id: number) => req<Card & { anki: { updated: boolean; error?: string; reason?: string } | null; details_error: string | null }>(
    "POST", `/api/cards/${id}/regenerate-details`),
  resendToAnki: (id: number) => req<Card & { anki: { updated: boolean; audio?: boolean; error?: string; reason?: string } }>(
    "POST", `/api/cards/${id}/resend-to-anki`),
  push: () => req<{ sent: number; failed: number }>("POST", "/api/push"),
  exportApkg: () => req<any>("POST", "/api/export"),
  confirmExport: () => req<any>("POST", "/api/export/confirm"),
};
