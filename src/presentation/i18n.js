/* i18n.js — UI strings for German and French.
 * Static elements carry data-i18n="key" (text) or data-i18n-attr="attr:key" (attributes).
 */

const I18N = {
    de: {
        appTitle: "Urteils-Radar",
        appSubtitle: "Relevante Bundesgerichtsurteile finden und zusammenfassen",
        searchTitle: "Zeitraum",
        from: "Von",
        to: "Bis",
        datePlaceholder: "TT.MM.JJJJ",
        openCalendar: "Kalender öffnen",
        search: "Suchen",
        searching: "Suche läuft …",
        resultsTitle: "Ergebnisse",
        resultsCount: (n) => `${n} Urteil${n === 1 ? "" : "e"}`,
        emptyInitial: "Wähle einen Zeitraum und starte die Suche.",
        emptyNoResults: "Keine Urteile in diesem Zeitraum gefunden.",
        errorSearch: "Die Suche ist fehlgeschlagen. Bitte erneut versuchen.",
        errorSummary: "Die Zusammenfassung konnte nicht erstellt werden.",
        errMissing: "Bitte beide Daten angeben.",
        errInvalid: "Ungültiges Datum. Format: TT.MM.JJJJ",
        errOrder: "Das Startdatum muss vor dem Enddatum liegen.",
        errFuture: "Das Enddatum darf nicht in der Zukunft liegen.",
        relevance: "Relevanz",
        upvote: "Relevanz erhöhen",
        downvote: "Relevanz senken",
        summarize: "Zusammenfassen",
        hideSummary: "Ausblenden",
        summarizing: "Zusammenfassung wird erstellt …",
        whyRelevant: "Warum relevant?",
        original: "Originalurteil",
        summaryLang: "Sprache der Zusammenfassung",
        toggleTheme: "Helles/dunkles Design wechseln",
        today: "Heute",
        prevMonth: "Vorheriger Monat",
        nextMonth: "Nächster Monat",
        prevYear: "Vorheriges Jahr",
        nextYear: "Nächstes Jahr",
        prevYears: "Frühere Jahre",
        nextYears: "Spätere Jahre",
        chooseMonth: "Monat und Jahr wählen",
        chooseYear: "Jahr wählen",
        filterText: "Ergebnisse filtern …",
        filterMin: "Minimale Relevanz",
        filterMax: "Maximale Relevanz",
        resetFilters: "Zurücksetzen",
        resultsCountFiltered: (shown, total) => `${shown} von ${total} Urteilen`,
        emptyFiltered: "Keine Ergebnisse für diese Filter.",
        selectAll: "Alle auswählen",
        selectItem: "Urteil auswählen",
        summarizeSelected: (n) => `Ausgewählte zusammenfassen (${n})`,
        inKb: "In Wissensdatenbank",
        kbTitle: "Wissensdatenbank",
        kbSearchPlaceholder: "Zusammenfassungen durchsuchen …",
        kbCount: (n) => `${n} Eintr${n === 1 ? "ag" : "äge"}`,
        kbEmpty: "Noch keine Zusammenfassungen gespeichert.",
        kbNoMatch: "Keine Treffer.",
        storedAt: "Gespeichert am",
        footer: "Prototyp · Hack Apertus Zürich 2026 · Zusammenfassungen generiert mit Apertus",
    },
    fr: {
        appTitle: "Radar des arrêts",
        appSubtitle: "Trouver et résumer les arrêts pertinents du Tribunal fédéral",
        searchTitle: "Période",
        from: "Du",
        to: "Au",
        datePlaceholder: "JJ.MM.AAAA",
        openCalendar: "Ouvrir le calendrier",
        search: "Rechercher",
        searching: "Recherche en cours …",
        resultsTitle: "Résultats",
        resultsCount: (n) => `${n} arrêt${n === 1 ? "" : "s"}`,
        emptyInitial: "Choisissez une période et lancez la recherche.",
        emptyNoResults: "Aucun arrêt trouvé pour cette période.",
        errorSearch: "La recherche a échoué. Veuillez réessayer.",
        errorSummary: "Le résumé n'a pas pu être généré.",
        errMissing: "Veuillez indiquer les deux dates.",
        errInvalid: "Date invalide. Format : JJ.MM.AAAA",
        errOrder: "La date de début doit précéder la date de fin.",
        errFuture: "La date de fin ne peut pas être dans le futur.",
        relevance: "Pertinence",
        upvote: "Augmenter la pertinence",
        downvote: "Diminuer la pertinence",
        summarize: "Résumer",
        hideSummary: "Masquer",
        summarizing: "Résumé en cours de rédaction …",
        whyRelevant: "Pourquoi pertinent ?",
        original: "Arrêt original",
        summaryLang: "Langue du résumé",
        toggleTheme: "Basculer entre thème clair et sombre",
        today: "Aujourd'hui",
        prevMonth: "Mois précédent",
        nextMonth: "Mois suivant",
        prevYear: "Année précédente",
        nextYear: "Année suivante",
        prevYears: "Années précédentes",
        nextYears: "Années suivantes",
        chooseMonth: "Choisir le mois et l'année",
        chooseYear: "Choisir l'année",
        filterText: "Filtrer les résultats …",
        filterMin: "Pertinence minimale",
        filterMax: "Pertinence maximale",
        resetFilters: "Réinitialiser",
        resultsCountFiltered: (shown, total) => `${shown} sur ${total} arrêts`,
        emptyFiltered: "Aucun résultat pour ces filtres.",
        selectAll: "Tout sélectionner",
        selectItem: "Sélectionner l'arrêt",
        summarizeSelected: (n) => `Résumer la sélection (${n})`,
        inKb: "Dans la base de connaissances",
        kbTitle: "Base de connaissances",
        kbSearchPlaceholder: "Rechercher dans les résumés …",
        kbCount: (n) => `${n} entrée${n === 1 ? "" : "s"}`,
        kbEmpty: "Aucun résumé enregistré pour l'instant.",
        kbNoMatch: "Aucun résultat.",
        storedAt: "Enregistré le",
        footer: "Prototype · Hack Apertus Zurich 2026 · Résumés générés avec Apertus",
    },
};

const LOCALES = { de: "de-CH", fr: "fr-CH" };

let currentLang = localStorage.getItem("lang") || "de";
if (!I18N[currentLang]) currentLang = "de";

function t(key, ...args) {
    const value = I18N[currentLang][key] ?? I18N.de[key] ?? key;
    return typeof value === "function" ? value(...args) : value;
}

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
