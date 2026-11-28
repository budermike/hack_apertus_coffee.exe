/* mock.js — simulated backend, for UI development and demos WITHOUT a server.
 *
 * Nothing in this file talks to the real backend. api.js uses it only when USE_MOCK is
 * true (page opened as a file, or "?mock" in the address); the page then shows a
 * "Demo-Daten" badge. The data is fictional and lives in this browser's localStorage
 * ("mockDb.v2"). Each function answers in the same shape as the backend route named
 * next to it, so api.js treats both alike.
 */

const mockBackend = (() => {
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

    function delay(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
    }

    /* Simulated storage: feedback (id -> { adjustment, decision }) and database (id -> entry). */
    const db = {
        load() {
            try {
                const data = JSON.parse(localStorage.getItem("mockDb.v2"));
                return { feedback: data?.feedback || {}, kb: data?.kb || {} };
            } catch {
                return { feedback: {}, kb: {} };
            }
        },
        save(data) {
            localStorage.setItem("mockDb.v2", JSON.stringify(data));
        },
    };

    return {
        /** GET /judgements */
        async searchJudgements(from, to) {
            await delay(700);
            const data = db.load();
            return MOCK_JUDGEMENTS
                .filter((j) => j.date >= from && j.date <= to)
                .map((j) => ({
                    ...j,
                    url: "#",
                    baseScore: j.score,
                    adjustment: 0,   // the demo backend stores the decision but does not learn from it
                    userLevel: data.feedback[j.id]?.decision || null,
                    summarized: Boolean(data.kb[j.id]),
                }));
        },

        /** POST /judgements/{id}/summary */
        async summarizeJudgement(id) {
            const stored = db.load().kb[id];
            if (stored) return stored;
            await delay(1200);
            const j = MOCK_JUDGEMENTS.find((m) => m.id === id);
            if (!j) throw new Error("unknown judgement");
            const entry = {
                id: j.id, caseNumber: j.caseNumber, date: j.date, url: "#", area: j.area, subject: j.subject,
                de: MOCK_SUMMARY.de(j), fr: MOCK_SUMMARY.fr(j),
                status: { de: "generated", fr: "generated" }, model: { de: "mock", fr: "mock" },
                storedAt: new Date().toISOString(),
            };
            const data = db.load();   // re-read: parallel calls may have written meanwhile
            data.kb[id] = entry;
            db.save(data);
            return entry;
        },

        /** PUT /judgements/{id}/summary: as in the backend, a changed language becomes the one
         *  edited version and is shown at once; if only one changed, the other follows (here
         *  a marked placeholder instead of an Apertus text), unless it was typed by hand. */
        async saveSummary(id, texts) {
            await delay(300);
            const data = db.load();
            const entry = data.kb[id];
            if (!entry) throw new Error("not in database");
            const next = {
                ...entry,
                status: { ...entry.status },
                model: { ...entry.model },
                alignedBy: { de: null, fr: null, ...entry.alignedBy },
                editedAt: new Date().toISOString(),
            };
            const changed = ["de", "fr"].filter((lang) => typeof texts[lang] === "string" && texts[lang] !== entry[lang]);
            for (const lang of changed) {
                next[lang] = texts[lang];
                next.status[lang] = "approved";
                next.model[lang] = "human";
                next.alignedBy[lang] = null;
            }
            let alignment = null;
            if (changed.length === 1) {
                const source = changed[0];
                const target = source === "de" ? "fr" : "de";
                if (entry.status[target] !== "generated" && !next.alignedBy[target]) {
                    alignment = { lang: target, ok: false, reason: "edited_by_hand" };
                } else {
                    const base = entry[target].replace(/\n\n\[Mock: [^\]]*\]$/, "");
                    next[target] = `${base}\n\n[Mock: ${target.toUpperCase()} ← ${source.toUpperCase()} angeglichen]`;
                    next.status[target] = "approved";
                    next.model[target] = "human";
                    next.alignedBy[target] = "mock";
                    alignment = { lang: target, ok: true };
                }
            }
            data.kb[id] = next;
            db.save(data);
            return { ...next, alignment };
        },

        /** DELETE /judgements/{id}/summary/edit: back to the generated text */
        async resetSummary(id) {
            await delay(200);
            const data = db.load();
            const entry = data.kb[id];
            const j = MOCK_JUDGEMENTS.find((m) => m.id === id);
            if (!entry || !j) throw new Error("not in database");
            data.kb[id] = {
                ...entry,
                de: MOCK_SUMMARY.de(j), fr: MOCK_SUMMARY.fr(j),
                status: { de: "generated", fr: "generated" }, model: { de: "mock", fr: "mock" },
                alignedBy: { de: null, fr: null }, editedAt: null,
            };
            db.save(data);
            return data.kb[id];
        },

        /** POST /judgements/{id}/feedback */
        async sendFeedback(id, _adjustment, _direction, _level, decision) {
            const data = db.load();
            if (decision) data.feedback[id] = { decision };
            else delete data.feedback[id];
            db.save(data);
            return { stored: true };
        },

        /** GET /knowledge-base */
        async searchKnowledgeBase(query) {
            await delay(150);
            const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
            return Object.values(db.load().kb)
                .filter((e) => {
                    const haystack = [e.caseNumber, e.subject.de, e.subject.fr, e.de, e.fr].join(" ").toLowerCase();
                    return terms.every((term) => haystack.includes(term));
                })
                .sort((a, b) => b.storedAt.localeCompare(a.storedAt));
        },

        /** Development helper without a backend counterpart: wipes summaries and feedback. */
        async clearDatabase() {
            await delay(150);
            db.save({ feedback: {}, kb: {} });
        },
    };
})();
