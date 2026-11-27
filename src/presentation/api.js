/* api.js — the only place the UI talks to the backend.
 *
 * While there is no backend, USE_MOCK = true returns synthetic data.
 * To connect the real backend, set USE_MOCK = false and implement the endpoints:
 *   GET  {API_BASE}/judgements?from=YYYY-MM-DD&to=YYYY-MM-DD&lang=de|fr
 *        -> [{ id, caseNumber, date, title: {de, fr}, explanation: {de, fr}, score, url }]
 *   POST {API_BASE}/judgements/{id}/summary   body: { lang }
 *        -> { lang, text }
 *   POST {API_BASE}/judgements/{id}/feedback  body: { adjustment }   (optional)
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

/* ---------- public API ---------- */

async function searchJudgements(from, to, lang) {
    if (USE_MOCK) {
        await delay(700);
        return MOCK_JUDGEMENTS
            .filter((j) => j.date >= from && j.date <= to)
            .map((j) => ({ ...j, url: "#" }));
    }
    const params = new URLSearchParams({ from, to, lang });
    const res = await fetch(`${API_BASE}/judgements?${params}`);
    if (!res.ok) throw new Error(`search failed: ${res.status}`);
    return res.json();
}

async function summarizeJudgement(id, lang) {
    if (USE_MOCK) {
        await delay(900);
        const j = MOCK_JUDGEMENTS.find((m) => m.id === id);
        if (!j) throw new Error("unknown judgement");
        return { lang, text: MOCK_SUMMARY[lang](j) };
    }
    const res = await fetch(`${API_BASE}/judgements/${encodeURIComponent(id)}/summary`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lang }),
    });
    if (!res.ok) throw new Error(`summary failed: ${res.status}`);
    return res.json();
}

async function sendFeedback(id, adjustment) {
    if (USE_MOCK) return;
    await fetch(`${API_BASE}/judgements/${encodeURIComponent(id)}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ adjustment }),
    }).catch(() => { /* feedback is best-effort */ });
}
