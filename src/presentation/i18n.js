/* i18n.js — UI strings for German and French.
 * Static elements carry data-i18n="key" (text) or data-i18n-attr="attr:key" (attributes).
 */

const I18N = {
    de: {
        appTitle: "Urteilsradar",
        appSubtitle: "Relevante Bundesgerichtsurteile finden und zusammenfassen",
        language: "Sprache",
        toggleTheme: "Helles/dunkles Design wechseln",
        tabSearch: "Urteile",
        tabKb: "Datenbank",

        /* search */
        from: "Von",
        to: "Bis",
        datePlaceholder: "TT.MM.JJJJ",
        openCalendar: "Kalender öffnen",
        search: "Suchen",
        searching: "Suche läuft …",
        emptyInitial: "Wähle einen Zeitraum und starte die Suche.",
        emptyNoResults: "Keine Urteile in diesem Zeitraum gefunden.",
        emptyFiltered: "Kein Urteil passt zu diesem Filter.",
        errorSearch: "Die Suche ist fehlgeschlagen. Bitte erneut versuchen.",
        errMissing: "Bitte beide Daten angeben.",
        errInvalid: "Ungültiges Datum. Format: TT.MM.JJJJ",
        errOrder: "Das Startdatum muss vor dem Enddatum liegen.",
        errFuture: "Das Enddatum darf nicht in der Zukunft liegen.",

        /* calendar */
        today: "Heute",
        prevMonth: "Vorheriger Monat",
        nextMonth: "Nächster Monat",
        prevYear: "Vorheriges Jahr",
        nextYear: "Nächstes Jahr",
        prevYears: "Frühere Jahre",
        nextYears: "Spätere Jahre",
        chooseMonth: "Monat und Jahr wählen",
        chooseYear: "Jahr wählen",

        /* filters + result list */
        filterText: "Suche in Nummer, Gegenstand, Begründung",
        relevance: "Relevanz",
        filterMin: "Minimale Relevanz",
        filterMax: "Maximale Relevanz",
        resetFilters: "Zurücksetzen",
        resultsCount: (n) => `${n} Urteil${n === 1 ? "" : "e"}`,
        resultsCountFiltered: (shown, total) => `${shown} von ${total} Urteilen`,
        colCase: "Urteil",
        colDate: "Datum",
        colArea: "Gebiet und Gegenstand",
        colRelevance: "Relevanz",
        colStatus: "Stand",
        scaleTitle: (score, threshold) => `Relevanz ${score} von 100. Ab ${threshold} wird automatisch zusammengefasst.`,
        tagInDb: "in Datenbank",
        tagCorrected: "korrigiert",
        tagSummarizing: "wird zusammengefasst",

        /* case detail */
        close: "Schliessen",
        judgement: "Urteil",
        openOriginal: "Urteil auf bger.ch öffnen",
        question: "Rechtsfrage",
        whyRelevant: "Warum relevant",
        whyNotRelevant: "Warum nicht relevant",
        rule: "Merksatz für die Praxis",
        criteria: "Kriterien und Belege aus dem Urteil",
        quoteExact: "wörtlich im Urteil",
        quoteApprox: "sinngemäss im Urteil",
        quoteMissing: "nicht im Urteil gefunden",
        refPrefix: "E. ",
        similarCases: "Ähnliche frühere Urteile",
        similarity: (s) => `Ähnlichkeit ${s}`,

        /* summaries */
        summary: "Zusammenfassung",
        langDe: "Deutsch",
        langFr: "Französisch",
        summarize: "Zusammenfassen",
        summarizing: "Zusammenfassung wird erstellt …",
        notAutoSummarized: (threshold) => `Unter einer Relevanz von ${threshold} wird nicht automatisch zusammengefasst.`,
        errorSummary: "Die Zusammenfassung konnte nicht erstellt werden.",
        save: "Speichern",
        saving: "Wird gespeichert …",
        saved: "Gespeichert",
        unsaved: "Ungespeicherte Änderungen",
        errorSave: "Speichern fehlgeschlagen.",
        adjust: "Anpassen",
        adjustPlaceholder: "Anweisung an Apertus, z. B. «kürzer fassen»",
        adjustApply: "Anwenden",
        adjusting: "Wird angepasst …",
        errorAdjust: "Die Anpassung ist fehlgeschlagen.",

        /* relevance correction */
        correction: "Korrektur",
        correctionNote: "Ihre Einschätzung wird gespeichert und dient als Beispiel, wenn das System ähnliche Urteile beurteilt.",
        markRelevant: "Für Suva relevant",
        markNotRelevant: "Nicht relevant",
        feedbackSaved: "Korrektur gespeichert.",
        errorFeedback: "Die Korrektur wurde nicht gespeichert.",

        /* database */
        kbTitle: "Datenbank",
        kbIntro: "Alle gespeicherten Zusammenfassungen. Sie lassen sich hier durchsuchen, bearbeiten und anpassen.",
        kbSearchPlaceholder: "Zusammenfassungen durchsuchen …",
        kbCount: (n) => `${n} Eintr${n === 1 ? "ag" : "äge"}`,
        kbEmpty: "Noch keine Zusammenfassungen gespeichert.",
        kbNoMatch: "Keine Treffer.",
        storedAt: "Gespeichert am",
        clearDb: "Datenbank leeren (temporär)",
        clearDbConfirm: "Alle gespeicherten Zusammenfassungen und Relevanz-Korrekturen löschen?",
        errorClearDb: "Die Datenbank konnte nicht geleert werden.",
    },
    fr: {
        appTitle: "Radar des arrêts",
        appSubtitle: "Trouver et résumer les arrêts pertinents du Tribunal fédéral",
        language: "Langue",
        toggleTheme: "Basculer entre thème clair et sombre",
        tabSearch: "Arrêts",
        tabKb: "Base de données",

        from: "Du",
        to: "Au",
        datePlaceholder: "JJ.MM.AAAA",
        openCalendar: "Ouvrir le calendrier",
        search: "Rechercher",
        searching: "Recherche en cours …",
        emptyInitial: "Choisissez une période et lancez la recherche.",
        emptyNoResults: "Aucun arrêt trouvé pour cette période.",
        emptyFiltered: "Aucun arrêt ne correspond à ce filtre.",
        errorSearch: "La recherche a échoué. Veuillez réessayer.",
        errMissing: "Veuillez indiquer les deux dates.",
        errInvalid: "Date invalide. Format : JJ.MM.AAAA",
        errOrder: "La date de début doit précéder la date de fin.",
        errFuture: "La date de fin ne peut pas être dans le futur.",

        today: "Aujourd'hui",
        prevMonth: "Mois précédent",
        nextMonth: "Mois suivant",
        prevYear: "Année précédente",
        nextYear: "Année suivante",
        prevYears: "Années précédentes",
        nextYears: "Années suivantes",
        chooseMonth: "Choisir le mois et l'année",
        chooseYear: "Choisir l'année",

        filterText: "Recherche : numéro, objet, motivation",
        relevance: "Pertinence",
        filterMin: "Pertinence minimale",
        filterMax: "Pertinence maximale",
        resetFilters: "Réinitialiser",
        resultsCount: (n) => `${n} arrêt${n === 1 ? "" : "s"}`,
        resultsCountFiltered: (shown, total) => `${shown} sur ${total} arrêts`,
        colCase: "Arrêt",
        colDate: "Date",
        colArea: "Domaine et objet",
        colRelevance: "Pertinence",
        colStatus: "État",
        scaleTitle: (score, threshold) => `Pertinence ${score} sur 100. Résumé automatique dès ${threshold}.`,
        tagInDb: "dans la base",
        tagCorrected: "corrigé",
        tagSummarizing: "résumé en cours",

        close: "Fermer",
        judgement: "Arrêt",
        openOriginal: "Ouvrir l'arrêt sur bger.ch",
        question: "Question juridique",
        whyRelevant: "Pourquoi pertinent",
        whyNotRelevant: "Pourquoi non pertinent",
        rule: "À retenir pour la pratique",
        criteria: "Critères et passages de l'arrêt",
        quoteExact: "mot pour mot dans l'arrêt",
        quoteApprox: "en substance dans l'arrêt",
        quoteMissing: "introuvable dans l'arrêt",
        refPrefix: "consid. ",
        similarCases: "Arrêts antérieurs similaires",
        similarity: (s) => `similarité ${s}`,

        summary: "Résumé",
        langDe: "Allemand",
        langFr: "Français",
        summarize: "Résumer",
        summarizing: "Résumé en cours de rédaction …",
        notAutoSummarized: (threshold) => `En dessous d'une pertinence de ${threshold}, aucun résumé n'est généré automatiquement.`,
        errorSummary: "Le résumé n'a pas pu être généré.",
        save: "Enregistrer",
        saving: "Enregistrement …",
        saved: "Enregistré",
        unsaved: "Modifications non enregistrées",
        errorSave: "L'enregistrement a échoué.",
        adjust: "Adapter",
        adjustPlaceholder: "Consigne pour Apertus, p. ex. « plus court »",
        adjustApply: "Appliquer",
        adjusting: "Adaptation en cours …",
        errorAdjust: "L'adaptation a échoué.",

        correction: "Correction",
        correctionNote: "Votre appréciation est enregistrée et sert d'exemple lorsque le système évalue des arrêts similaires.",
        markRelevant: "Pertinent pour la Suva",
        markNotRelevant: "Non pertinent",
        feedbackSaved: "Correction enregistrée.",
        errorFeedback: "La correction n'a pas été enregistrée.",

        kbTitle: "Base de données",
        kbIntro: "Tous les résumés enregistrés. Ils peuvent être recherchés, modifiés et adaptés ici.",
        kbSearchPlaceholder: "Rechercher dans les résumés …",
        kbCount: (n) => `${n} entrée${n === 1 ? "" : "s"}`,
        kbEmpty: "Aucun résumé enregistré pour l'instant.",
        kbNoMatch: "Aucun résultat.",
        storedAt: "Enregistré le",
        clearDb: "Vider la base (temporaire)",
        clearDbConfirm: "Supprimer tous les résumés enregistrés et les corrections de pertinence ?",
        errorClearDb: "La base n'a pas pu être vidée.",
    },
};

/* Lookup tables for codes sent by the backend; unknown codes are shown as they are. */

const AREA_NAMES = {
    de: { UV: "Unfallversicherung", IV: "Invalidenversicherung", MV: "Militärversicherung", ALV: "Arbeitslosenversicherung", EL: "Ergänzungsleistungen", KV: "Krankenversicherung", AHV: "AHV", BV: "Berufliche Vorsorge", FZ: "Familienzulagen", EO: "Erwerbsersatz", SOZ: "Sozialversicherung allgemein", MIET: "Mietrecht" },
    fr: { UV: "Assurance-accidents", IV: "Assurance-invalidité", MV: "Assurance militaire", ALV: "Assurance-chômage", EL: "Prestations complémentaires", KV: "Assurance-maladie", AHV: "AVS", BV: "Prévoyance professionnelle", FZ: "Allocations familiales", EO: "Allocations pour perte de gain", SOZ: "Assurances sociales en général", MIET: "Droit du bail" },
};

const OUTCOME_NAMES = {
    de: { abgewiesen: "Beschwerde abgewiesen", gutgeheissen: "Beschwerde gutgeheissen", teilweise: "teilweise gutgeheissen", nichteintreten: "Nichteintreten", andere: "anderer Ausgang", unklar: "Ausgang nicht erkannt" },
    fr: { abgewiesen: "recours rejeté", gutgeheissen: "recours admis", teilweise: "partiellement admis", nichteintreten: "non-entrée en matière", andere: "autre issue", unklar: "issue non reconnue" },
};

const CRITERIA_NAMES = {
    de: {
        K1: "Materielle Frage der Unfallversicherung",
        K2: "Allgemeines Sozialversicherungsrecht mit Wirkung auf die UV-Praxis",
        K3: "Übertragbarer Grundsatz aus der IV oder einem anderen Zweig",
        K4: "Klärung oder Änderung der Rechtsprechung",
        K5: "Suva oder UVG-Versicherer ist Partei, materielle Prüfung",
        N1: "Nichteintreten oder reine Prozessfrage",
        N2: "Reine Beweiswürdigung im Einzelfall",
        N3: "Rechtsgebiet ohne Bezug zur Unfallversicherung",
    },
    fr: {
        K1: "Question de fond de l'assurance-accidents",
        K2: "Droit général des assurances sociales ayant un effet sur la pratique LAA",
        K3: "Principe transposable issu de l'AI ou d'une autre branche",
        K4: "Clarification ou changement de jurisprudence",
        K5: "La Suva ou un assureur LAA est partie, examen au fond",
        N1: "Non-entrée en matière ou pure question de procédure",
        N2: "Pure appréciation des preuves dans le cas d'espèce",
        N3: "Domaine sans lien avec l'assurance-accidents",
    },
};

const LOCALES = { de: "de-CH", fr: "fr-CH" };

let currentLang = localStorage.getItem("lang") || "de";
if (!I18N[currentLang]) currentLang = "de";

function t(key, ...args) {
    const value = I18N[currentLang][key] ?? I18N.de[key] ?? key;
    return typeof value === "function" ? value(...args) : value;
}

/** Text of a `{de, fr}` field in the UI language (German as fallback). */
function loc(field) {
    return field?.[currentLang] || field?.de || "";
}

const areaName = (code) => AREA_NAMES[currentLang][code] || code || "";
const outcomeName = (code) => OUTCOME_NAMES[currentLang][code] || "";
const criterionName = (id) => CRITERIA_NAMES[currentLang][id] || "";

function formatDate(isoDate) {
    return new Intl.DateTimeFormat(LOCALES[currentLang], {
        day: "2-digit", month: "2-digit", year: "numeric",
    }).format(new Date(isoDate + "T00:00:00"));
}

function applyTranslations(root = document) {
    document.documentElement.lang = currentLang;
    document.title = t("appTitle");
    root.querySelectorAll("[data-i18n]").forEach((el) => {
        el.textContent = t(el.dataset.i18n);
    });
    root.querySelectorAll("[data-i18n-attr]").forEach((el) => {
        el.dataset.i18nAttr.split(";").forEach((pair) => {
            const [attr, key] = pair.split(":");
            el.setAttribute(attr.trim(), t(key.trim()));
        });
    });
}

function setLanguage(lang) {
    if (!I18N[lang]) return;
    currentLang = lang;
    localStorage.setItem("lang", lang);
    applyTranslations();
}
