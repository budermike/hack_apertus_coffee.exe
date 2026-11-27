/* api.js — the only place the UI talks to the backend.
 *
 * While there is no backend, USE_MOCK = true simulates it in the browser
 * (state persisted in localStorage under "mockDb"). To connect the real backend,
 * set USE_MOCK = false and implement these endpoints:
 *
 *   GET  {API_BASE}/judgements?from=YYYY-MM-DD&to=YYYY-MM-DD&lang=de|fr
 *        -> [{ id, caseNumber, date, url,
 *              title: {de, fr}, explanation: {de, fr},
 *              score,        // relevance 0..100 computed by the backend
 *              adjustment,   // persisted net manual adjustment from feedback (see below)
 *              summarized }] // true if the judgement is already in the knowledge base
 *
 *   POST {API_BASE}/judgements/{id}/summary
 *        -> { id, caseNumber, date, title: {de, fr}, de, fr, storedAt }
 *        Generates the German AND the French summary (two separate Apertus calls,
 *        not a translation), stores both in the knowledge base and returns the entry.
 *        If the judgement is already in the knowledge base, return the stored entry.
 *
 *   PUT  {API_BASE}/judgements/{id}/summary   body: { de, fr }
 *        -> { id, caseNumber, date, title: {de, fr}, de, fr, storedAt, editedAt }
 *        Saves a summary the user edited by hand (overwrites the generated text in the KB).
 *
 *   POST {API_BASE}/judgements/{id}/feedback   body: { adjustment, direction, level }
 *        The user moved the judgement to another relevance level (traffic light).
 *        level = "high" | "medium" | "low" (the level chosen by the user),
 *        adjustment = new net manual adjustment so that score + adjustment lies in that level,
 *        direction = +1 (raised) | -1 (lowered).
 *        The backend must persist this and use it in future lookups:
 *          1. return it as `adjustment` for this judgement in every later search, and
 *          2. use the voted judgements as relevance signal, e.g. as positive/negative
 *             few-shot examples in the Apertus relevance prompt, so similar new
 *             judgements are scored higher/lower.
 *
 *   GET  {API_BASE}/knowledge-base?q=<query>&lang=de|fr
 *        -> [{ id, caseNumber, date, title: {de, fr}, de, fr, storedAt }]
 *        Full-text (later: semantic) search over all stored summaries, newest first.
 *
 *   DELETE {API_BASE}/knowledge-base      (TEMPORARY, development only — remove before go-live)
 *        Deletes all stored summaries and all relevance feedback.
 */

const USE_MOCK = true;
const API_BASE = "/api";

/* ---------- mock data (fictional, for UI development only) ---------- */

const MOCK_JUDGEMENTS = [
    {
        id: "8C_101_2026", caseNumber: "8C 101/2026", date: "2026-11-12", score: 92,
        title: { de: "Unfallversicherung; Kausalzusammenhang bei Schleudertrauma",
                 fr: "Assurance-accidents ; lien de causalité en cas de traumatisme du rachis cervical" },
        explanation: { de: "Suva ist Partei; zentrale Frage der adäquaten Kausalität nach UVG.",
                       fr: "La Suva est partie ; question centrale de la causalité adéquate selon la LAA." },
    },
    {
        id: "8C_245_2026", caseNumber: "8C 245/2026", date: "2026-10-28", score: 85,
        title: { de: "Unfallbegriff; ungewöhnlicher äusserer Faktor beim Sport",
                 fr: "Notion d'accident ; facteur extérieur extraordinaire lors d'une activité sportive" },
        explanation: { de: "Präzisiert den Unfallbegriff (Art. 4 ATSG) – hohe Praxisrelevanz.",
                       fr: "Précise la notion d'accident (art. 4 LPGA) – grande portée pratique." },
    },
    {
        id: "8C_312_2026", caseNumber: "8C 312/2026", date: "2026-10-15", score: 78,
        title: { de: "Integritätsentschädigung; Bemessung bei Mehrfachverletzungen",
                 fr: "Indemnité pour atteinte à l'intégrité ; calcul en cas de lésions multiples" },
        explanation: { de: "Betrifft Leistungsbemessung der Unfallversicherung.",
                       fr: "Concerne le calcul des prestations de l'assurance-accidents." },
    },
    {
        id: "8C_077_2026", caseNumber: "8C 77/2026", date: "2026-09-30", score: 64,
        title: { de: "Invalidenrente; Einkommensvergleich nach Unfall",
                 fr: "Rente d'invalidité ; comparaison des revenus après un accident" },
        explanation: { de: "Unfallbezug vorhanden, Schwerpunkt aber auf Invaliditätsbemessung.",
                       fr: "Lien avec un accident, mais l'accent porte sur l'évaluation de l'invalidité." },
    },
    {
        id: "9C_410_2026", caseNumber: "9C 410/2026", date: "2026-09-18", score: 41,
        title: { de: "Invalidenversicherung; berufliche Eingliederungsmassnahmen",
                 fr: "Assurance-invalidité ; mesures de réadaptation professionnelle" },
        explanation: { de: "Nur indirekter Bezug zur Unfallversicherung.",
                       fr: "Lien seulement indirect avec l'assurance-accidents." },
    },
    {
        id: "8C_520_2026", caseNumber: "8C 520/2026", date: "2026-09-05", score: 73,
        title: { de: "Rückfall und Spätfolgen; Leistungspflicht der Suva",
                 fr: "Rechute et séquelles tardives ; obligation de prester de la Suva" },
        explanation: { de: "Suva ist Beschwerdegegnerin; Abgrenzung Rückfall/Krankheit.",
                       fr: "La Suva est intimée ; distinction entre rechute et maladie." },
    },
    {
        id: "4A_188_2026", caseNumber: "4A 188/2026", date: "2026-08-27", score: 18,
        title: { de: "Mietrecht; Kündigung wegen Eigenbedarf",
                 fr: "Droit du bail ; résiliation pour besoin propre" },
        explanation: { de: "Kein Bezug zur Unfallversicherung – nicht relevant.",
                       fr: "Aucun lien avec l'assurance-accidents – non pertinent." },
    },
    {
        id: "8C_633_2026", caseNumber: "8C 633/2026", date: "2026-11-20", score: 88,
        title: { de: "Berufskrankheit; Listenstoffe und Kausalität",
                 fr: "Maladie professionnelle ; substances listées et causalité" },
        explanation: { de: "Berufskrankheit nach Art. 9 UVG, Suva als Versicherer.",
                       fr: "Maladie professionnelle selon l'art. 9 LAA, la Suva en tant qu'assureur." },
    },
];

const MOCK_SUMMARY = {
    de: (j) => `Regeste (Entwurf, Mock)\n\n${j.title.de}\n\nSachverhalt: Platzhaltertext. Hier erscheint später die von Apertus erstellte Zusammenfassung im Stil der Suva – mit Sachverhalt, rechtlichen Erwägungen und Ergebnis.\n\nErwägungen: …\n\nErgebnis: …`,
    fr: (j) => `Regeste (brouillon, mock)\n\n${j.title.fr}\n\nFaits : texte provisoire. Le résumé rédigé par Apertus dans le style de la Suva apparaîtra ici – avec les faits, les considérants juridiques et le résultat.\n\nConsidérants : …\n\nRésultat : …`,
};

function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

/* Simulated backend storage: feedback (id -> adjustment) and knowledge base (id -> entry). */
const mockDb = {
    load() {
        try {
            const db = JSON.parse(localStorage.getItem("mockDb"));
            return { feedback: db?.feedback || {}, kb: db?.kb || {} };
        } catch {
            return { feedback: {}, kb: {} };
        }
    },
    save(db) {
        localStorage.setItem("mockDb", JSON.stringify(db));
    },
};

async function jsonFetch(url, options = {}) {
    const res = await fetch(url, {
        headers: { "Content-Type": "application/json" },
        ...options,
    });
    if (!res.ok) throw new Error(`${options.method || "GET"} ${url} failed: ${res.status}`);
    return res.json();
}

/* ---------- public API ---------- */

async function searchJudgements(from, to, lang) {
    if (USE_MOCK) {
        await delay(700);
        const db = mockDb.load();
        return MOCK_JUDGEMENTS
            .filter((j) => j.date >= from && j.date <= to)
            .map((j) => ({
                ...j,
                url: "#",
                adjustment: db.feedback[j.id] || 0,
                summarized: Boolean(db.kb[j.id]),
            }));
    }
    const params = new URLSearchParams({ from, to, lang });
    return jsonFetch(`${API_BASE}/judgements?${params}`);
}

/** Generates DE + FR summaries and stores them in the knowledge base. */
async function summarizeJudgement(id) {
    if (USE_MOCK) {
        const db = mockDb.load();
        if (db.kb[id]) return db.kb[id];
        await delay(1200);
        const j = MOCK_JUDGEMENTS.find((m) => m.id === id);
        if (!j) throw new Error("unknown judgement");
        const entry = {
            id: j.id, caseNumber: j.caseNumber, date: j.date, title: j.title,
            de: MOCK_SUMMARY.de(j), fr: MOCK_SUMMARY.fr(j),
            storedAt: new Date().toISOString(),
        };
        const fresh = mockDb.load();   // re-read: parallel calls may have written meanwhile
        fresh.kb[id] = entry;
        mockDb.save(fresh);
        return entry;
    }
    return jsonFetch(`${API_BASE}/judgements/${encodeURIComponent(id)}/summary`, { method: "POST" });
}

/** Saves a manually edited summary ({ de, fr }) to the knowledge base. */
async function saveSummary(id, texts) {
    if (USE_MOCK) {
        await delay(300);
        const db = mockDb.load();
        if (!db.kb[id]) throw new Error("not in knowledge base");
        db.kb[id] = { ...db.kb[id], de: texts.de, fr: texts.fr, editedAt: new Date().toISOString() };
        mockDb.save(db);
        return db.kb[id];
    }
    return jsonFetch(`${API_BASE}/judgements/${encodeURIComponent(id)}/summary`, {
        method: "PUT",
        body: JSON.stringify({ de: texts.de, fr: texts.fr }),
    });
}

/** Persists a relevance correction; the backend uses it in future lookups. Best-effort. */
async function sendFeedback(id, adjustment, direction, level) {
    if (USE_MOCK) {
        const db = mockDb.load();
        db.feedback[id] = adjustment;
        mockDb.save(db);
        return;
    }
    await jsonFetch(`${API_BASE}/judgements/${encodeURIComponent(id)}/feedback`, {
        method: "POST",
        body: JSON.stringify({ adjustment, direction, level }),
    }).catch((err) => console.warn("feedback not saved", err));
}

/** TEMPORARY (development only): deletes all summaries and feedback. */
async function clearDatabase() {
    if (USE_MOCK) {
        await delay(150);
        mockDb.save({ feedback: {}, kb: {} });
        return;
    }
    const res = await fetch(`${API_BASE}/knowledge-base`, { method: "DELETE" });
    if (!res.ok) throw new Error(`DELETE knowledge-base failed: ${res.status}`);
}

async function searchKnowledgeBase(query, lang) {
    if (USE_MOCK) {
        await delay(150);
        const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
        return Object.values(mockDb.load().kb)
            .filter((e) => {
                const haystack = [e.caseNumber, e.title.de, e.title.fr, e.de, e.fr].join(" ").toLowerCase();
                return terms.every((term) => haystack.includes(term));
            })
            .sort((a, b) => b.storedAt.localeCompare(a.storedAt));
    }
    const params = new URLSearchParams({ q: query, lang });
    return jsonFetch(`${API_BASE}/knowledge-base?${params}`);
}
