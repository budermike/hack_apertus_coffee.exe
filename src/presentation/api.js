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
 *              baseScore,    // relevance without the lawyers' decisions (= score until one exists)
 *              adjustment,   // always 0: nothing is added to a single score any more
 *              userLevel,    // "high" | "low": the lawyer's own decision on this judgement
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
 *                   status: {de, fr}, model: {de, fr}, alignedBy: {de, fr},
 *                   coherenceOk: {de, fr}, issues: {de, fr}, pending: {de, fr}, storedAt, editedAt }
 *        Returns the stored summary; generates DE + FR with Apertus first if there is none.
 *        `status` / `model` are the columns summaries.status / summaries.model of the shown
 *        version ("generated" + model ID, or "approved" + "human"); `alignedBy` is the Apertus
 *        model ID if that edited version was not typed but brought in line with the other
 *        language. The UI's origin tag per language is derived from these fields only (see
 *        summaryOrigin), never from the text or from what was typed.
 *        `pending` is only filled for edits saved before 28.11.2026 that were never approved.
 *
 *   PUT  {API_BASE}/judgements/{id}/summary   body: { de, fr }
 *        -> entry + alignment. A changed text becomes the ONE edited version of its language
 *        and is shown at once (no approval, a later edit replaces it). If only one language
 *        changed, Apertus brings the other one in line, unless a lawyer typed that one.
 *        alignment: null | { lang, ok: true } | { lang, ok: false, reason: "edited_by_hand" | "failed" }
 *
 *   DELETE {API_BASE}/judgements/{id}/summary/edit
 *        -> entry. Drops the edited version of both languages: back to the generated text,
 *        which is never deleted.
 *
 *   POST {API_BASE}/judgements/{id}/feedback   body: { adjustment: 0, direction, level, decision }
 *        -> { stored: true, ... }   Persists the decision in the summaries DB. Every later
 *        GET /api/judgements returns it as `userLevel`, and the scores of all judgements are
 *        the ones learned from all decisions (triage/feedback_learning.py); GET /api/config
 *        lists what was learned under `learning`.
 *
 *   POST {API_BASE}/subjects/localize   body: { lang, ids: [..] }
 *        -> { lang, subjects: { id: text }, complete }   Subjects ("Gegenstand") of these
 *        judgements in the language of the UI. A subject exists in the language of the
 *        judgement only; Apertus carries it over in legal wording (not word for word), once,
 *        then it is stored. GET /judgements delivers stored ones in `subject[lang]` and
 *        names the languages still missing in `subjectMissing`.
 *
 *   POST {API_BASE}/judgements/{id}/localize   body: { lang }
 *        -> { lang, subject?, quotes: { index: text }, basis?: { laws, considerations }, complete }
 *        The quotes under the criteria and what the summary rests on, in the language of
 *        the UI. A carried-over quote is not the wording of the court any more: the UI
 *        marks it and keeps the original one click away.
 *
 *   GET  {API_BASE}/judgements/{id}/original?lang=de|fr
 *        -> HTML page with the judgement text as the crawler stored it (opened in a new tab,
 *        not fetched by script): the original can be read without the external site.
 *
 *   GET  {API_BASE}/knowledge-base?q=<query>&lang=de|fr
 *        -> [entry]   all stored summaries containing every search term, newest first
 *
 *   POST {API_BASE}/summaries/queue   body: { caseIds: [..], priority?: bool }
 *        -> { running: [..], queued: [..], done: [..], failed: {id: err}, total }
 *
 *   GET  {API_BASE}/summaries/status?ids=<comma-separated>
 *        -> { running, queued, done, failed, total }
 *
 *   DELETE {API_BASE}/summaries/queue/{id}
 *        -> { cancelled: bool }
 *
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

/** Address of the page with the locally stored judgement text (served by the backend). */
function originalPageUrl(id, lang) {
    return `${API_BASE}/judgements/${encodeURIComponent(id)}/original?${new URLSearchParams({ lang })}`;
}

function summaryUrl(id) {
    return `${API_BASE}/judgements/${encodeURIComponent(id)}/summary`;
}

const httpBackend = {
    searchJudgements(from, to, lang) {
        return jsonFetch(`${API_BASE}/judgements?${new URLSearchParams({ from, to, lang })}`);
    },
    ensurePeriod(from, to) {
        return jsonFetch(`${API_BASE}/periods/ensure`, {
            method: "POST",
            body: JSON.stringify({ from, to }),
        });
    },
    periodStatus(from, to) {
        return jsonFetch(`${API_BASE}/periods/status?${new URLSearchParams({ from, to })}`);
    },
    localizeSubjects(lang, ids) {
        return jsonFetch(`${API_BASE}/subjects/localize`, { method: "POST", body: JSON.stringify({ lang, ids }) });
    },
    localizeJudgement(id, lang) {
        return jsonFetch(`${API_BASE}/judgements/${encodeURIComponent(id)}/localize`, {
            method: "POST",
            body: JSON.stringify({ lang }),
        });
    },
    translateReasonFr(id) {
        return jsonFetch(`${API_BASE}/judgements/${encodeURIComponent(id)}/reason-fr`, {
            method: "POST",
            body: "{}",
        });
    },
    summarizeJudgement(id) {
        return jsonFetch(summaryUrl(id), { method: "POST" });
    },
    getSummary(id) {
        return jsonFetch(summaryUrl(id));
    },
    queueSummaries(caseIds, priority = false) {
        return jsonFetch(`${API_BASE}/summaries/queue`, {
            method: "POST",
            body: JSON.stringify({ caseIds, priority }),
        });
    },
    summaryQueueStatus(caseIds) {
        const query = caseIds?.length ? `?${new URLSearchParams({ ids: caseIds.join(",") })}` : "";
        return jsonFetch(`${API_BASE}/summaries/status${query}`);
    },
    cancelQueuedSummary(id) {
        return jsonFetch(`${API_BASE}/summaries/queue/${encodeURIComponent(id)}`, { method: "DELETE" });
    },
    saveSummary(id, texts) {
        return jsonFetch(summaryUrl(id), { method: "PUT", body: JSON.stringify({ de: texts.de, fr: texts.fr }) });
    },
    resetSummary(id) {
        return jsonFetch(`${summaryUrl(id)}/edit`, { method: "DELETE" });
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

/* ---------- shapes: one place where backend answers are checked and tidied ---------- */

/** Numbers may arrive as strings ("75"); anything that is no number becomes `fallback`. */
function toNumber(value, fallback = 0) {
    const n = typeof value === "string" ? parseFloat(value) : value;
    return Number.isFinite(n) ? n : fallback;
}

function normalizeJudgement(raw) {
    return {
        ...raw,
        caseNumber: raw.caseNumber || raw.id,
        score: toNumber(raw.score),
        baseScore: toNumber(raw.baseScore ?? raw.score),
        adjustment: toNumber(raw.adjustment),
        userLevel: raw.userLevel || null,   // "high" | "low": decided by hand
        subjectMissing: Array.isArray(raw.subjectMissing) ? raw.subjectMissing : [],   // UI languages without a subject yet
        scored: typeof raw.scored === "boolean" ? raw.scored : null,   // false: Apertus gave no relevance value
        feedbackLocalOnly: false,
        summarized: Boolean(raw.summarized),
        criteria: Array.isArray(raw.criteria) ? raw.criteria : [],
        signals: Array.isArray(raw.signals) ? raw.signals : [],
        topics: Array.isArray(raw.topics) ? raw.topics : [],
        similarCases: (Array.isArray(raw.similarCases) ? raw.similarCases : []).map((c) => ({
            ...c,
            similarity: toNumber(c.similarity),
        })),
    };
}

/** Origin of the text the editor shows for `lang`, from the database flags:
 *    "edited"     the shown version was typed by a lawyer (status "approved", model "human"),
 *                 or an edit from before 28.11. still waits (pending)
 *    "aligned"    the shown version belongs to the edit, but Apertus wrote it to follow the
 *                 lawyer's change of the other language (alignedBy = model ID)
 *    "generated"  the shown version is the model's text (status "generated")
 *    null         the backend sent no status for this language: no tag is shown */
function summaryOrigin(raw, lang) {
    if (typeof raw.pending?.[lang] === "string") return "edited";
    const status = raw.status?.[lang];
    if (status === "edited" || status === "approved") return raw.alignedBy?.[lang] ? "aligned" : "edited";
    if (status === "generated") return "generated";
    return null;
}

/** Non-empty plain object / array helpers so approved empty `{}` / `[]` do not shadow filled values. */
function nonEmptyObj(obj) {
    return obj && typeof obj === "object" && !Array.isArray(obj) && Object.keys(obj).length > 0 ? obj : null;
}

function nonEmptyArr(arr) {
    return Array.isArray(arr) && arr.length > 0 ? arr : null;
}

/** Summary entry for the editor. `de` / `fr` hold the newest text: an edit saved before
 *  28.11. that never got approved (`pendingLangs`) wins over the shown version, so it is not
 *  lost; the next "Speichern" takes it over. `alignment` is only set on the answer to a save. */
function normalizeEntry(raw) {
    const pending = raw.pending || {};
    const entry = {
        id: raw.id,
        caseNumber: raw.caseNumber || raw.id,
        date: raw.date,
        url: raw.url,
        area: raw.area,
        subject: raw.subject,
        title: raw.title,
        storedAt: raw.storedAt || "",
        editedAt: raw.editedAt || null,
        facts: nonEmptyObj(raw.facts?.de) || nonEmptyObj(raw.facts?.fr)
            || (!raw.facts?.de && !raw.facts?.fr ? nonEmptyObj(raw.facts) : null),
        examples: nonEmptyArr(raw.examples?.de) || nonEmptyArr(raw.examples?.fr) || nonEmptyArr(raw.examples) || [],
        pendingLangs: SUMMARY_LANGS.filter((lang) => typeof pending[lang] === "string"),
        origin: Object.fromEntries(SUMMARY_LANGS.map((lang) => [lang, summaryOrigin(raw, lang)])),
        model: Object.fromEntries(SUMMARY_LANGS.map((lang) => [lang, raw.model?.[lang] || null])),
        alignedBy: Object.fromEntries(SUMMARY_LANGS.map((lang) => [lang, raw.alignedBy?.[lang] || null])),
        alignment: raw.alignment || null,
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
    return rows.map((row) => normalizeJudgement(row));
}

async function ensurePeriod(from, to) {
    return backend.ensurePeriod ? await backend.ensurePeriod(from, to) : { status: "done" };
}

async function periodStatus(from, to) {
    return backend.periodStatus ? await backend.periodStatus(from, to) : { status: "done" };
}

/** Subjects of these judgements in `lang`; the demo backend has only what mock.js holds. */
async function localizeSubjects(lang, ids) {
    return backend.localizeSubjects ? await backend.localizeSubjects(lang, ids) : { subjects: {}, complete: true };
}

/** Quotes and summary basis of one judgement in `lang`, or null in demo mode. */
async function localizeJudgement(id, lang) {
    return backend.localizeJudgement ? await backend.localizeJudgement(id, lang) : null;
}

async function translateReasonFr(id) {
    return backend.translateReasonFr ? await backend.translateReasonFr(id) : null;
}

/** Returns the stored summary entry; the backend generates DE + FR first if there is none. */
async function summarizeJudgement(id) {
    return normalizeEntry(await backend.summarizeJudgement(id));
}

async function fetchStoredSummary(id) {
    return normalizeEntry(await (USE_MOCK ? mockBackend.summarizeJudgement(id) : httpBackend.getSummary(id)));
}

async function queueSummaries(caseIds, priority = false) {
    return backend.queueSummaries ? await backend.queueSummaries(caseIds, priority) : null;
}

async function summaryQueueStatus(caseIds) {
    return backend.summaryQueueStatus ? await backend.summaryQueueStatus(caseIds) : null;
}

async function cancelQueuedSummary(id) {
    return backend.cancelQueuedSummary ? await backend.cancelQueuedSummary(id) : { cancelled: false };
}

/** Saves edited summary texts ({ de, fr }); they are shown at once. The entry's `alignment`
 *  tells whether Apertus brought the other language in line. */
async function saveSummary(id, texts) {
    return normalizeEntry(await backend.saveSummary(id, texts));
}

/** Drops the edited version of both languages: the generated text is shown again. */
async function resetSummary(id) {
    return normalizeEntry(await backend.resetSummary(id));
}

/** Sends a relevance decision (`decision`: "high" | "low" | null = taken back). */
async function sendFeedback(id, adjustment, direction, level, decision) {
    let stored = false;
    try {
        const answer = await backend.sendFeedback(id, adjustment, direction, level, decision);
        stored = answer?.stored === true;
    } catch (err) {
        console.warn("feedback not accepted by the backend", err);
    }
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
