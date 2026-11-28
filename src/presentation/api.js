/* api.js — the only place the UI talks to the backend.
 *
 * Three clearly separated parts:
 *   1. Real backend (HTTP)   — src/hack_apertus_coffee_exe/summary/api.py, routes below.
 *   2. Mock backend          — mock.js, used instead of HTTP when USE_MOCK is true.
 *   3. Temporary local store — section "TEMPORARY" below: keeps in this browser what the
 *                              backend cannot store yet. Remove each part when its issue is done.
 *
 * Backend routes used (start: python -m hack_apertus_coffee_exe.summary.api --triage <file>):
 *
 *   GET  {API_BASE}/config
 *        -> { thresholds: { high, medium } }   from configs/triage.json
 *
 *   GET  {API_BASE}/judgements?from=YYYY-MM-DD&to=YYYY-MM-DD&lang=de|fr
 *        -> [{ id, caseNumber, date, url, lang, area, subject: {de, fr}, explanation: {de, fr},
 *              score,        // relevance 0..100
 *              adjustment,   // net manual correction; always 0 until the backend stores feedback
 *              summarized,   // true if a summary is stored
 *              // optional — the UI shows what is there:
 *              court: {de, fr}, outcome, signals: [..], topics: [..],
 *              question: {de, fr}, rule: {de, fr},
 *              criteria: [{ id, at, quote, status }],   // id: K1..K5 / N1..N3,
 *                                                       // status: "exakt" | "ungefähr" | "fehlt"
 *              similarCases: [{ caseNumber, subject, similarity }] }]
 *
 *        Area and outcome codes: AREA_NAMES / OUTCOME_NAMES in i18n.js.
 *        Mapping from the triage output (see ../triage-README.md):
 *          caseNumber = case_id, date = judgement_date, lang = language, score = final_score,
 *          explanation = relevance_reason, similarCases = similar_cases.
 *        The relevance scoring (`triage.cli relevance`, results/triage_<period>.json) also fills
 *        metadata.relevance: area, outcome, question, rule and criteria
 *        [{a_id, b_stelle, c_zitat, beleg_status}] = criteria [{id, at, quote, status}].
 *        Its score is already on this 0..100 scale: 60 and above = on the Suva list.
 *
 *   POST {API_BASE}/judgements/{id}/summary
 *        -> entry { id, caseNumber, date, url, title: {de, fr}, de, fr,
 *                   status: {de, fr}, model: {de, fr}, coherenceOk: {de, fr}, issues: {de, fr},
 *                   pending: {de, fr}, storedAt, editedAt }
 *        Returns the stored summary; generates DE + FR with Apertus first if there is none.
 *        `status` / `model` are the columns summaries.status / summaries.model of the shown
 *        version ("generated" + model ID, or "approved" + "human"); `pending` is the text of a
 *        newer 'edited' version. The UI's origin tag per language is derived from these three
 *        fields only (see summaryOrigin), never from the text or from what was typed.
 *
 *   PUT  {API_BASE}/judgements/{id}/summary   body: { de, fr }
 *        -> entry. Changed texts are stored as 'edited' versions (`pending`); `de` / `fr`
 *        keep showing the generated or last approved text until the edit is approved.
 *
 *   POST {API_BASE}/judgements/{id}/summary/approve   body: { languages? }
 *        -> entry. The pending edits become the shown version.
 *
 *   POST {API_BASE}/judgements/{id}/feedback   body: { adjustment, direction, level, decision }
 *        -> { stored }   PLACEHOLDER in the backend: logged, not stored (stored = false).
 *        decision = "high" | "low" | null: what the user clicked ("Für Suva relevant" /
 *        "Nicht relevant" / taken back); level = level the judgement is in afterwards;
 *        adjustment = net correction so that score + adjustment lies in that level;
 *        direction = +1 raised | -1 lowered | 0 level confirmed.
 *        Once stored, the backend should return `adjustment` and `userLevel` (= decision)
 *        with the judgement in every later search.
 *
 *   GET  {API_BASE}/knowledge-base?q=<query>&lang=de|fr
 *        -> [entry]   all stored summaries containing every search term, newest first
 *
 * Not available in the backend yet (the UI says so instead of pretending):
 *   - adjusting one summary language with Apertus ("Anpassen")
 *   (criteria with quotes, question and rule are delivered when using `triage.cli relevance` output)
 */

/** Mock instead of HTTP: when the page is opened as a file (no server), or with "?mock". */
const USE_MOCK = location.protocol === "file:" || new URLSearchParams(location.search).has("mock");
const API_BASE = "/api";

const SUMMARY_LANGS = ["de", "fr"];

/* ---------- 1. real backend (HTTP) ---------- */

async function jsonFetch(url, options = {}) {
    const res = await fetch(url, {
        headers: { "Content-Type": "application/json" },
        ...options,
    });
    if (!res.ok) throw new Error(`${options.method || "GET"} ${url} failed: ${res.status}`);
    return res.json();
}

function summaryUrl(id) {
    return `${API_BASE}/judgements/${encodeURIComponent(id)}/summary`;
}

const httpBackend = {
    searchJudgements(from, to, lang) {
        return jsonFetch(`${API_BASE}/judgements?${new URLSearchParams({ from, to, lang })}`);
    },
    summarizeJudgement(id) {
        return jsonFetch(summaryUrl(id), { method: "POST" });
    },
    saveSummary(id, texts) {
        return jsonFetch(summaryUrl(id), { method: "PUT", body: JSON.stringify({ de: texts.de, fr: texts.fr }) });
    },
    approveSummary(id) {
        return jsonFetch(`${summaryUrl(id)}/approve`, { method: "POST", body: "{}" });
    },
    sendFeedback(id, adjustment, direction, level, decision) {
        return jsonFetch(`${API_BASE}/judgements/${encodeURIComponent(id)}/feedback`, {
            method: "POST",
            body: JSON.stringify({ adjustment, direction, level, decision }),
        });
    },
    searchKnowledgeBase(query, lang) {
        return jsonFetch(`${API_BASE}/knowledge-base?${new URLSearchParams({ q: query, lang })}`);
    },
};

/* ---------- 2. mock backend: see mock.js ---------- */

const backend = USE_MOCK ? mockBackend : httpBackend;

/* ---------- 3. TEMPORARY local store ---------- */

/** Relevance decisions the backend does not store yet (its feedback route is a placeholder):
 *  id -> { adjustment, level }. They are kept in this browser only, so they survive a new
 *  search or a reload here, but no colleague sees them and triage does not learn from them.
 *  Remove when the backend stores feedback and returns `adjustment` / `userLevel`. */
const localFeedback = {
    KEY: "localFeedback",
    load() {
        try {
            return JSON.parse(localStorage.getItem(this.KEY)) || {};
        } catch {
            return {};
        }
    },
    set(id, decision) {
        const all = this.load();
        if (decision) all[id] = decision;
        else delete all[id];
        localStorage.setItem(this.KEY, JSON.stringify(all));
    },
    clear() {
        localStorage.removeItem(this.KEY);
    },
};

/* ---------- shapes: one place where backend answers are checked and tidied ---------- */

/** Numbers may arrive as strings ("75"); anything that is no number becomes `fallback`. */
function toNumber(value, fallback = 0) {
    const n = typeof value === "string" ? parseFloat(value) : value;
    return Number.isFinite(n) ? n : fallback;
}

/** Case numbers are shown as on the result list of the demo: "8C_173/2025". */
function formatCaseNumber(value) {
    return String(value || "").trim().replace(/\s+/, "_");
}

function normalizeJudgement(raw, localDecisions) {
    // what the backend knows wins; otherwise the decision remembered in this browser
    const fromBackend = toNumber(raw.adjustment) !== 0 || Boolean(raw.userLevel);
    const local = fromBackend ? null : localDecisions[raw.id];
    return {
        ...raw,
        caseNumber: formatCaseNumber(raw.caseNumber || raw.id),
        score: toNumber(raw.score),
        adjustment: toNumber(local ? local.adjustment : raw.adjustment),
        userLevel: (local ? local.level : raw.userLevel) || null,   // "high" | "low": decided by hand
        feedbackLocalOnly: Boolean(local),                          // true: only known to this browser
        summarized: Boolean(raw.summarized),
        criteria: Array.isArray(raw.criteria) ? raw.criteria : [],
        signals: Array.isArray(raw.signals) ? raw.signals : [],
        topics: Array.isArray(raw.topics) ? raw.topics : [],
        similarCases: (Array.isArray(raw.similarCases) ? raw.similarCases : []).map((c) => ({
            ...c,
            caseNumber: formatCaseNumber(c.caseNumber),
            similarity: toNumber(c.similarity),
        })),
    };
}

/** Origin of the text the editor shows for `lang`, from the database flags:
 *    "edited"     a saved human edit waits for approval (pending), or the shown version
 *                 is an approved human edit (status "approved"/"edited", model "human")
 *    "generated"  the shown version is the model's text (status "generated")
 *    null         the backend sent no status for this language: no tag is shown */
function summaryOrigin(raw, lang) {
    if (typeof raw.pending?.[lang] === "string") return "edited";
    const status = raw.status?.[lang];
    if (status === "edited" || status === "approved") return "edited";
    if (status === "generated") return "generated";
    return null;
}

/** Summary entry for the editor. `de` / `fr` hold the newest text: a saved edit that still
 *  waits for approval (`pendingLangs`) wins over the shown version. */
function normalizeEntry(raw) {
    const pending = raw.pending || {};
    const entry = {
        id: raw.id,
        caseNumber: formatCaseNumber(raw.caseNumber || raw.id),
        date: raw.date,
        url: raw.url,
        area: raw.area,
        subject: raw.subject,
        title: raw.title,
        storedAt: raw.storedAt || "",
        editedAt: raw.editedAt || null,
        pendingLangs: SUMMARY_LANGS.filter((lang) => typeof pending[lang] === "string"),
        origin: Object.fromEntries(SUMMARY_LANGS.map((lang) => [lang, summaryOrigin(raw, lang)])),
        model: Object.fromEntries(SUMMARY_LANGS.map((lang) => [lang, raw.model?.[lang] || null])),
    };
    for (const lang of SUMMARY_LANGS) entry[lang] = pending[lang] ?? raw[lang] ?? "";
    return entry;
}

/* ---------- public API (used by script.js) ---------- */

/** Backend settings (thresholds); null in mock mode or when the backend does not answer. */
async function fetchConfig() {
    if (USE_MOCK) return null;
    try {
        return await jsonFetch(`${API_BASE}/config`);
    } catch (err) {
        console.warn("config not loaded, using defaults", err);
        return null;
    }
}

async function searchJudgements(from, to, lang) {
    const rows = await backend.searchJudgements(from, to, lang);
    const local = USE_MOCK ? {} : localFeedback.load();
    return rows.map((row) => normalizeJudgement(row, local));
}

/** Returns the stored summary entry; the backend generates DE + FR first if there is none. */
async function summarizeJudgement(id) {
    return normalizeEntry(await backend.summarizeJudgement(id));
}

/** Saves edited summary texts ({ de, fr }). */
async function saveSummary(id, texts) {
    return normalizeEntry(await backend.saveSummary(id, texts));
}

/** Approves the saved edits, so they become the shown version. */
async function approveSummary(id) {
    return normalizeEntry(await backend.approveSummary(id));
}

/** Sends a relevance decision (`decision`: "high" | "low" | null = taken back). Resolves to
 *  { stored }: false means the backend did not keep it and it was saved in this browser
 *  only (see localFeedback). */
async function sendFeedback(id, adjustment, direction, level, decision) {
    let stored = false;
    try {
        const answer = await backend.sendFeedback(id, adjustment, direction, level, decision);
        stored = answer?.stored === true;
    } catch (err) {
        console.warn("feedback not accepted by the backend", err);
    }
    if (!USE_MOCK) localFeedback.set(id, stored || !decision ? null : { adjustment, level: decision });
    return { stored };
}

async function searchKnowledgeBase(query, lang) {
    return (await backend.searchKnowledgeBase(query, lang)).map(normalizeEntry);
}

/** Mock only (development): deletes all summaries and feedback. The real database keeps every version. */
async function clearDatabase() {
    if (!USE_MOCK) throw new Error("the real database cannot be cleared");
    await mockBackend.clearDatabase();
}
