/* api.js — the only place the UI talks to the backend.
 *
 * While there is no backend, USE_MOCK = true simulates it in the browser
 * (state persisted in localStorage under "mockDb.v2"). To connect the real backend,
 * set USE_MOCK = false and implement these endpoints:
 *
 *   GET  {API_BASE}/judgements?from=YYYY-MM-DD&to=YYYY-MM-DD&lang=de|fr
 *        -> [{ id, caseNumber, date, url,
 *              lang,                  // language of the judgement: "de" | "fr" | "it"
 *              area,                  // short code, e.g. "UV", "IV" (see AREA_NAMES in i18n.js)
 *              subject: {de, fr},     // what the case is about
 *              explanation: {de, fr}, // why (not) relevant
 *              score,                 // relevance 0..100 computed by the backend
 *              adjustment,            // persisted net manual adjustment from feedback (see below)
 *              summarized,            // true if the judgement is already in the database
 *              // optional — each detail section is only shown when its field is present:
 *              court: {de, fr}, outcome,          // outcome: see OUTCOME_NAMES in i18n.js
 *              question: {de, fr}, rule: {de, fr},
 *              criteria: [{ id, at, quote, status }],   // id: K1..K5 / N1..N3,
 *                                                       // status: "exakt" | "ungefähr" | "fehlt"
 *              similarCases: [{ caseNumber, subject, similarity }] }]
 *
 *        Mapping from the triage output (see ../triage-README.md):
 *          caseNumber = case_id, date = judgement_date, lang = language, score = final_score,
 *          explanation = relevance_reason, similarCases = similar_cases.
 *
 *   POST {API_BASE}/judgements/{id}/summary
 *        -> { id, caseNumber, date, area, subject: {de, fr}, de, fr, storedAt }
 *        Generates the German AND the French summary (two separate Apertus calls,
 *        not a translation), stores both in the database and returns the entry.
 *        If the judgement is already in the database, return the stored entry.
 *
 *   PUT  {API_BASE}/judgements/{id}/summary   body: { de, fr }
 *        -> the entry as above, plus editedAt
 *        Saves a summary the user edited by hand (overwrites the stored text).
 *
 *   POST {API_BASE}/judgements/{id}/summary/adjust   body: { lang, text, instruction }
 *        -> { lang, text }
 *        Apertus rewrites the summary in ONE language following the user's instruction
 *        (e.g. "kürzer fassen"). `text` is the current text in the editor. Nothing is
 *        stored: the result lands in the editor and is saved with PUT like any other edit.
 *
 *   POST {API_BASE}/judgements/{id}/feedback   body: { adjustment, direction, level }
 *        The user marked the judgement as relevant / not relevant for Suva.
 *        level = "high" | "medium" | "low" (the level the judgement is in afterwards),
 *        adjustment = new net manual adjustment so that score + adjustment lies in that level,
 *        direction = +1 (raised) | -1 (lowered).
 *        The backend must persist this and use it in future lookups:
 *          1. return it as `adjustment` for this judgement in every later search, and
 *          2. use the voted judgements as relevance signal, e.g. as positive/negative
 *             few-shot examples in the Apertus relevance prompt, so similar new
 *             judgements are scored higher/lower.
 *
 *   GET  {API_BASE}/config
 *        -> { thresholds: { high, medium } }   traffic-light thresholds from configs/triage.json
 *
 *   GET  {API_BASE}/knowledge-base?q=<query>&lang=de|fr
 *        -> [{ id, caseNumber, date, area, subject: {de, fr}, de, fr, storedAt }]
 *        Full-text (later: semantic) search over all stored summaries, newest first.
 *
 *   DELETE {API_BASE}/knowledge-base      (TEMPORARY, development only — remove before go-live)
 *        Deletes all stored summaries and all relevance feedback.
 */

const USE_MOCK = false;
const API_BASE = "/api";

/* ---------- mock data (fictional, for UI development only) ---------- */

const COURT_III = { de: "III. öffentlich-rechtliche Abteilung", fr: "IIIe Cour de droit public" };
const COURT_IV = { de: "IV. öffentlich-rechtliche Abteilung", fr: "IVe Cour de droit public" };
const COURT_CIVIL_I = { de: "I. zivilrechtliche Abteilung", fr: "Ire Cour de droit civil" };

const MOCK_JUDGEMENTS = [
    {
        id: "8C_101_2026", caseNumber: "8C_101/2026", date: "2026-11-12", score: 92,
        lang: "de", court: COURT_IV, area: "UV", outcome: "abgewiesen",
        subject: { de: "Kausalzusammenhang bei Schleudertrauma",
                   fr: "Lien de causalité en cas de traumatisme du rachis cervical" },
        question: { de: "Nach welchen Kriterien ist die adäquate Kausalität bei einem Schleudertrauma ohne organisch nachweisbare Unfallfolgen zu prüfen?",
                    fr: "Selon quels critères faut-il examiner la causalité adéquate en cas de traumatisme cervical sans séquelles organiques objectivables ?" },
        explanation: { de: "Suva ist Partei; zentrale Frage der adäquaten Kausalität nach UVG.",
                       fr: "La Suva est partie ; question centrale de la causalité adéquate selon la LAA." },
        rule: { de: "Fehlen organisch objektivierbare Unfallfolgen, ist die Adäquanz gesondert anhand der Schleudertrauma-Kriterien zu prüfen.",
                fr: "En l'absence de séquelles organiques objectivables, la causalité adéquate s'examine séparément selon les critères du traumatisme cervical." },
        criteria: [
            { id: "K1", at: "E3.2, E4.1", status: "exakt",
              quote: "Streitig ist, ob die über den Fallabschluss hinaus geklagten Beschwerden in einem adäquaten Kausalzusammenhang zum Unfall stehen." },
            { id: "K5", at: "E1", status: "ungefähr",
              quote: "Die Suva stellte ihre Leistungen ein und hielt daran mit Einspracheentscheid fest." },
        ],
        similarCases: [
            { caseNumber: "8C_691/2024", subject: "Schleudertrauma, Adäquanzprüfung", similarity: 0.71 },
            { caseNumber: "8C_177/2025", subject: "Fallabschluss, natürliche Kausalität", similarity: 0.58 },
        ],
    },
    {
        id: "8C_633_2026", caseNumber: "8C_633/2026", date: "2026-11-20", score: 88,
        lang: "de", court: COURT_IV, area: "UV", outcome: "gutgeheissen",
        subject: { de: "Berufskrankheit, Listenstoffe und Kausalität",
                   fr: "Maladie professionnelle, substances listées et causalité" },
        question: { de: "Wann gilt eine Krankheit als vorwiegend durch einen Listenstoff verursacht?",
                    fr: "Quand une maladie est-elle réputée causée de manière prépondérante par une substance listée ?" },
        explanation: { de: "Berufskrankheit nach Art. 9 UVG, Suva als Versicherer.",
                       fr: "Maladie professionnelle selon l'art. 9 LAA, la Suva en tant qu'assureur." },
        rule: { de: "«Vorwiegend» im Sinne von Art. 9 Abs. 1 UVG verlangt einen Verursachungsanteil von mehr als 50 %.",
                fr: "« De manière prépondérante » au sens de l'art. 9 al. 1 LAA exige une part causale supérieure à 50 %." },
        criteria: [
            { id: "K1", at: "E2.1", status: "exakt",
              quote: "Als Berufskrankheiten gelten Krankheiten, die bei der beruflichen Tätigkeit ausschliesslich oder vorwiegend durch schädigende Stoffe verursacht worden sind." },
            { id: "K4", at: "E5.3", status: "ungefähr",
              quote: "An dieser Rechtsprechung ist festzuhalten; sie ist indessen wie folgt zu präzisieren." },
        ],
    },
    {
        id: "8C_245_2026", caseNumber: "8C_245/2026", date: "2026-10-28", score: 85,
        lang: "fr", court: COURT_IV, area: "UV", outcome: "abgewiesen",
        subject: { de: "Unfallbegriff, ungewöhnlicher äusserer Faktor beim Sport",
                   fr: "Notion d'accident, facteur extérieur extraordinaire lors d'une activité sportive" },
        question: { de: "Liegt ein ungewöhnlicher äusserer Faktor vor, wenn sich die Verletzung bei einem sportartüblichen Bewegungsablauf ereignet?",
                    fr: "Y a-t-il un facteur extérieur extraordinaire lorsque la lésion survient lors d'un mouvement habituel pour le sport pratiqué ?" },
        explanation: { de: "Präzisiert den Unfallbegriff (Art. 4 ATSG) – hohe Praxisrelevanz.",
                       fr: "Précise la notion d'accident (art. 4 LPGA) – grande portée pratique." },
        rule: { de: "Ein sportartüblicher Bewegungsablauf ohne Programmwidrigkeit erfüllt den Unfallbegriff nicht.",
                fr: "Un mouvement habituel pour le sport pratiqué, sans déroulement anormal, ne remplit pas la notion d'accident." },
        criteria: [
            { id: "K1", at: "E3.1", status: "exakt",
              quote: "Le caractère extraordinaire de l'atteinte ne concerne pas les effets du facteur extérieur, mais seulement ce facteur lui-même." },
        ],
    },
    {
        id: "8C_312_2026", caseNumber: "8C_312/2026", date: "2026-10-15", score: 78,
        lang: "de", court: COURT_IV, area: "UV", outcome: "teilweise",
        subject: { de: "Integritätsentschädigung, Bemessung bei Mehrfachverletzungen",
                   fr: "Indemnité pour atteinte à l'intégrité, calcul en cas de lésions multiples" },
        question: { de: "Wie sind mehrere Integritätsschäden aus demselben Unfall zusammenzurechnen?",
                    fr: "Comment additionner plusieurs atteintes à l'intégrité résultant du même accident ?" },
        explanation: { de: "Betrifft Leistungsbemessung der Unfallversicherung.",
                       fr: "Concerne le calcul des prestations de l'assurance-accidents." },
        criteria: [
            { id: "K1", at: "E4.2", status: "exakt",
              quote: "Fallen mehrere körperliche, geistige oder psychische Integritätsschäden zusammen, wird die Integritätsentschädigung nach der gesamten Beeinträchtigung festgesetzt." },
        ],
    },
    {
        id: "8C_520_2026", caseNumber: "8C_520/2026", date: "2026-09-05", score: 73,
        lang: "de", court: COURT_IV, area: "UV", outcome: "abgewiesen",
        subject: { de: "Rückfall und Spätfolgen, Leistungspflicht der Suva",
                   fr: "Rechute et séquelles tardives, obligation de prester de la Suva" },
        question: { de: "Wer trägt die Beweislast für den Kausalzusammenhang zwischen einem Rückfall und dem ursprünglichen Unfall?",
                    fr: "Qui supporte le fardeau de la preuve du lien de causalité entre une rechute et l'accident initial ?" },
        explanation: { de: "Suva ist Beschwerdegegnerin; Abgrenzung Rückfall/Krankheit.",
                       fr: "La Suva est intimée ; distinction entre rechute et maladie." },
        criteria: [
            { id: "K5", at: "E1.1", status: "exakt",
              quote: "Die Suva verneinte ihre Leistungspflicht für den gemeldeten Rückfall." },
            { id: "N2", at: "E4.3", status: "ungefähr",
              quote: "Die Vorinstanz hat die medizinischen Akten einlässlich gewürdigt." },
        ],
    },
    {
        id: "8C_077_2026", caseNumber: "8C_77/2026", date: "2026-09-30", score: 64,
        lang: "de", court: COURT_IV, area: "UV", outcome: "abgewiesen",
        subject: { de: "Invalidenrente, Einkommensvergleich nach Unfall",
                   fr: "Rente d'invalidité, comparaison des revenus après un accident" },
        question: { de: "Ist ein Abzug vom Tabellenlohn gerechtfertigt, wenn die Einschränkungen bereits im Zumutbarkeitsprofil berücksichtigt sind?",
                    fr: "Un abattement sur le salaire statistique se justifie-t-il lorsque les limitations sont déjà prises en compte dans le profil d'exigibilité ?" },
        explanation: { de: "Unfallbezug vorhanden, Schwerpunkt aber auf Invaliditätsbemessung.",
                       fr: "Lien avec un accident, mais l'accent porte sur l'évaluation de l'invalidité." },
        criteria: [
            { id: "K1", at: "E5.1", status: "exakt",
              quote: "Die Beschwerde richtet sich allein gegen den vorinstanzlichen Verzicht auf einen Abzug vom Tabellenlohn." },
        ],
    },
    {
        id: "9C_410_2026", caseNumber: "9C_410/2026", date: "2026-09-18", score: 41,
        lang: "fr", court: COURT_III, area: "IV", outcome: "abgewiesen",
        subject: { de: "Berufliche Eingliederungsmassnahmen",
                   fr: "Mesures de réadaptation professionnelle" },
        question: { de: "Besteht Anspruch auf Umschulung, wenn die bisherige Tätigkeit weiterhin teilweise zumutbar ist?",
                    fr: "Existe-t-il un droit au reclassement lorsque l'activité exercée jusqu'ici reste partiellement exigible ?" },
        explanation: { de: "Nur indirekter Bezug zur Unfallversicherung.",
                       fr: "Lien seulement indirect avec l'assurance-accidents." },
        criteria: [
            { id: "K3", at: "E3.2", status: "ungefähr",
              quote: "Le droit au reclassement suppose une perte de gain durable d'environ 20 %." },
        ],
    },
    {
        id: "4A_188_2026", caseNumber: "4A_188/2026", date: "2026-08-27", score: 18,
        lang: "de", court: COURT_CIVIL_I, area: "MIET", outcome: "nichteintreten",
        subject: { de: "Kündigung wegen Eigenbedarf", fr: "Résiliation pour besoin propre" },
        explanation: { de: "Kein Bezug zur Unfallversicherung – nicht relevant.",
                       fr: "Aucun lien avec l'assurance-accidents – non pertinent." },
        criteria: [
            { id: "N3", at: "E1", status: "exakt",
              quote: "Die Beschwerde in Zivilsachen betrifft eine mietrechtliche Streitigkeit." },
            { id: "N1", at: "E2", status: "exakt",
              quote: "Auf die Beschwerde ist mangels hinreichender Begründung nicht einzutreten." },
        ],
    },
];

const MOCK_SUMMARY = {
    de: (j) => `Regeste (Entwurf, Mock)\n\n${j.subject.de}\n\nSachverhalt: Platzhaltertext. Hier erscheint später die von Apertus erstellte Zusammenfassung im Stil der Suva – mit Sachverhalt, rechtlichen Erwägungen und Ergebnis.\n\nErwägungen: …\n\nErgebnis: …`,
    fr: (j) => `Regeste (brouillon, mock)\n\n${j.subject.fr}\n\nFaits : texte provisoire. Le résumé rédigé par Apertus dans le style de la Suva apparaîtra ici – avec les faits, les considérants juridiques et le résultat.\n\nConsidérants : …\n\nRésultat : …`,
};

const MOCK_ADJUST_NOTE = { de: "Mock – angepasst gemäss Anweisung", fr: "Mock – adapté selon la consigne" };

function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

/* Simulated backend storage: feedback (id -> adjustment) and database (id -> entry). */
const mockDb = {
    load() {
        try {
            const db = JSON.parse(localStorage.getItem("mockDb.v2"));
            return { feedback: db?.feedback || {}, kb: db?.kb || {} };
        } catch {
            return { feedback: {}, kb: {} };
        }
    },
    save(db) {
        localStorage.setItem("mockDb.v2", JSON.stringify(db));
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

function summaryUrl(id) {
    return `${API_BASE}/judgements/${encodeURIComponent(id)}/summary`;
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

/** Generates DE + FR summaries and stores them in the database. */
async function summarizeJudgement(id) {
    if (USE_MOCK) {
        const db = mockDb.load();
        if (db.kb[id]) return db.kb[id];
        await delay(1200);
        const j = MOCK_JUDGEMENTS.find((m) => m.id === id);
        if (!j) throw new Error("unknown judgement");
        const entry = {
            id: j.id, caseNumber: j.caseNumber, date: j.date, area: j.area, subject: j.subject,
            de: MOCK_SUMMARY.de(j), fr: MOCK_SUMMARY.fr(j),
            storedAt: new Date().toISOString(),
        };
        const fresh = mockDb.load();   // re-read: parallel calls may have written meanwhile
        fresh.kb[id] = entry;
        mockDb.save(fresh);
        return entry;
    }
    return jsonFetch(summaryUrl(id), { method: "POST" });
}

/** Saves a manually edited summary ({ de, fr }) to the database. */
async function saveSummary(id, texts) {
    if (USE_MOCK) {
        await delay(300);
        const db = mockDb.load();
        if (!db.kb[id]) throw new Error("not in database");
        db.kb[id] = { ...db.kb[id], de: texts.de, fr: texts.fr, editedAt: new Date().toISOString() };
        mockDb.save(db);
        return db.kb[id];
    }
    return jsonFetch(summaryUrl(id), {
        method: "PUT",
        body: JSON.stringify({ de: texts.de, fr: texts.fr }),
    });
}

/** Lets Apertus rewrite the summary in one language following `instruction`. Not stored. */
async function adjustSummary(id, lang, text, instruction) {
    if (USE_MOCK) {
        await delay(900);
        return { lang, text: `${text.trim()}\n\n[${MOCK_ADJUST_NOTE[lang]}: «${instruction}»]` };
    }
    return jsonFetch(`${summaryUrl(id)}/adjust`, {
        method: "POST",
        body: JSON.stringify({ lang, text, instruction }),
    });
}

/** Persists a relevance correction; the backend uses it in future lookups. */
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
    });
}

/** TEMPORARY (development only): deletes all summaries and feedback. */
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
                const haystack = [e.caseNumber, e.subject.de, e.subject.fr, e.de, e.fr].join(" ").toLowerCase();
                return terms.every((term) => haystack.includes(term));
            })
            .sort((a, b) => b.storedAt.localeCompare(a.storedAt));
    }
    const params = new URLSearchParams({ q: query, lang });
    return jsonFetch(`${API_BASE}/knowledge-base?${params}`);
}
