/* i18n.js — UI strings for German and French.
 * Static elements carry data-i18n="key" (text) or data-i18n-attr="attr:key" (attributes).
 */

const I18N = {
    de: {
        appTitle: "Urteilsradar",
        language: "Sprache",
        toggleTheme: "Helles/dunkles Design wechseln",
        tabSearch: "Urteile",
        tabKb: "Datenbank",
        mockBadge: "Demo-Daten",
        mockBadgeHint: "Simuliertes Backend mit erfundenen Urteilen: nichts wird auf einem Server gespeichert.",

        /* search */
        from: "Von",
        to: "Bis",
        datePlaceholder: "TT.MM.JJJJ",
        openCalendar: "Kalender öffnen",
        search: "Suchen",
        searching: "Suche läuft …",
        periodLoading: (msg) => msg || "Zeitraum wird geladen und mit Apertus bewertet …",
        periodError: (msg) => `Zeitraum konnte nicht geladen werden: ${msg}`,
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
        calendar: "Kalender",

        /* filters + result list */
        filterText: "Suche in Nummer, Gegenstand, Begründung",
        relevance: "Relevanz",
        filterMin: "Minimale Relevanz",
        filterMax: "Maximale Relevanz",
        resetFilters: "Zurücksetzen",
        filterLevel: "Einstufung",
        levelHigh: "Relevant",
        levelMedium: "Knapp darunter",
        levelLow: "Nicht relevant",
        levelAll: "Alle",
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
        tagConfirmed: "bestätigt",
        localSuffix: " (nur lokal)",
        tagSummarizing: "wird zusammengefasst",
        tagQueued: "wartet",
        tagFailed: "Zusammenfassung fehlgeschlagen",

        /* case detail */
        close: "Schliessen",
        resizeDetail: "Breite des Detailbereichs (Pfeiltasten; Doppelklick: Standard)",
        resizeValue: (pct) => `Detailbereich ${pct} % breit`,
        judgement: "Urteil",
        openOriginal: "Urteil auf bger.ch öffnen",
        openLocal: "Gespeicherten Volltext öffnen",
        question: "Rechtsfrage",
        whyRelevant: "Warum relevant",
        whyNotRelevant: "Warum nicht relevant",
        rule: "Merksatz für die Praxis",
        criteria: "Kriterien und Belege aus dem Urteil",
        quoteExact: "wörtlich im Urteil",
        quoteApprox: "sinngemäss im Urteil",
        quoteMissing: "nicht im Urteil gefunden",
        signals: "Signale der Triage",
        criteriaMissing: "Kriterien mit Belegzitaten liefert die Triage für dieses Urteil noch nicht.",
        germanOnly: "Dieser Abschnitt liegt nur auf Deutsch vor.",
        refPrefix: "E. ",
        similarCases: "Ähnliche frühere Urteile",
        similarity: (s) => `Ähnlichkeit ${s}`,

        /* summaries */
        summary: "Zusammenfassung",
        langDe: "Deutsch",
        langFr: "Französisch",
        origin_generated: "generiert",
        origin_edited: "bearbeitet",
        originGeneratedHint: (model) => `Automatisch erstellt (${model}), nicht von Hand geändert.`,
        origin_aligned: "angeglichen",
        originEditedHint: "Von Hand bearbeitet.",
        originAlignedHint: (model) => `Von Apertus (${model}) an die bearbeitete Fassung der anderen Sprache angeglichen. Bitte prüfen.`,
        summarize: "Zusammenfassen",
        summarizing: "Zusammenfassung wird erstellt …",
        summaryQueued: "In der Warteschlange – wird automatisch zusammengefasst …",
        noSummaryYet: "Für dieses Urteil wurde noch keine Zusammenfassung erstellt.",
        notAutoSummarized: (threshold, score) =>
            `Relevanz ${score} liegt unter ${threshold}: Dieses Urteil wird nicht automatisch zusammengefasst.`,
        errorSummary: "Die Zusammenfassung konnte nicht erstellt werden.",
        retry: "Erneut versuchen",
        save: "Speichern",
        saving: "Wird gespeichert …",
        saved: "Gespeichert",
        unsaved: "Ungespeicherte Änderungen",
        errorSave: "Speichern fehlgeschlagen.",
        savingAligning: "Wird gespeichert, Apertus gleicht die andere Sprache an …",
        resetSummary: "Zurücksetzen",
        resetSummaryHint: "Verwirft die bearbeitete Fassung beider Sprachen und zeigt wieder den von Apertus erstellten Text.",
        resetConfirm: "Bearbeitete Fassung (Deutsch und Französisch) verwerfen und zum generierten Text zurückkehren?",
        resetting: "Wird zurückgesetzt …",
        resetDone: "Zurückgesetzt auf den generierten Text",
        errorReset: "Zurücksetzen fehlgeschlagen.",
        pendingLegacy: "Frühere Bearbeitung, noch nicht übernommen: «Speichern» übernimmt sie, «Zurücksetzen» verwirft sie.",
        alignedToast: (lang) => `Gespeichert. Apertus hat die ${lang === "de" ? "deutsche" : "französische"} Fassung an Ihre Änderung angeglichen. Bitte prüfen.`,
        alignSkippedToast: (lang) => `Gespeichert. Die ${lang === "de" ? "deutsche" : "französische"} Fassung ist von Hand bearbeitet und wurde deshalb nicht automatisch angeglichen.`,
        alignFailedToast: (lang) => `Gespeichert. Die ${lang === "de" ? "deutsche" : "französische"} Fassung konnte nicht angeglichen werden (Apertus nicht erreichbar) und ist unverändert.`,
        openSuvaFormat: "Im Suva-Format öffnen",
        exportTitle: "BGer-Urteil",

        /* relevance correction */
        correction: "Korrektur",
        correctionNote: "Ihre Entscheidung bestimmt die Einstufung dieses Urteils, und das System lernt daraus für ähnliche Urteile (gleiches Rechtsgebiet, gleiche Kriterien). Ein zweiter Klick nimmt sie zurück.",
        markRelevant: "Für Suva relevant",
        markNotRelevant: "Nicht relevant",
        feedbackSaved: "Entscheidung gespeichert.",
        feedbackLearned: (n) => `Entscheidung gespeichert. Daraus gelernt: ${n} ${n === 1 ? "weiteres Urteil" : "weitere Urteile"} neu bewertet.`,
        learnedDeltaHint: (base) => `Aus den Entscheidungen der Juristinnen und Juristen gelernt. Ohne sie: ${base}`,
        feedbackLocalOnly: "Korrektur konnte nicht am Server gespeichert werden.",

        /* extra case & summary basis (#72) */
        tagSingleJudge: "Einzelrichter",
        principleLabel: (n) => `Grundsatzgehalt ${n}/3`,
        uvUseLabel: (n) => `UV-Nutzen ${n}/3`,
        suvaSelected: "Von Suva gewählt",
        summaryBasis: "Worauf sich die Zusammenfassung stützt",
        summaryBasisLaws: "Gesetzesartikel",
        summaryBasisConsiderations: "Massgebende Erwägungen",
        summaryBasisExamples: "Stil-Vorbilder",

        /* database */
        kbTitle: "Datenbank",
        kbIntro: "Alle gespeicherten Zusammenfassungen. Sie lassen sich hier durchsuchen und bearbeiten.",
        kbSearchPlaceholder: "Zusammenfassungen durchsuchen …",
        kbCount: (n) => `${n} Eintr${n === 1 ? "ag" : "äge"}`,
        kbEmpty: "Noch keine Zusammenfassungen gespeichert.",
        kbNoMatch: "Keine Treffer.",
        errorKb: "Die Datenbank konnte nicht gelesen werden. Läuft das Backend?",
        storedAt: "Gespeichert am",
        clearDb: "Datenbank leeren (temporär)",
        clearDbConfirm: "Alle gespeicherten Zusammenfassungen und Relevanz-Korrekturen löschen?",
        errorClearDb: "Die Datenbank konnte nicht geleert werden.",
    },
    fr: {
        appTitle: "Radar des arrêts",
        language: "Langue",
        toggleTheme: "Basculer entre thème clair et sombre",
        tabSearch: "Arrêts",
        tabKb: "Base de données",
        mockBadge: "Données de démo",
        mockBadgeHint: "Backend simulé avec des arrêts fictifs : rien n'est enregistré sur un serveur.",

        from: "Du",
        to: "Au",
        datePlaceholder: "JJ.MM.AAAA",
        openCalendar: "Ouvrir le calendrier",
        search: "Rechercher",
        searching: "Recherche en cours …",
        periodLoading: (msg) => msg || "Chargement et évaluation de la période par Apertus …",
        periodError: (msg) => `Impossible de charger la période : ${msg}`,
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
        calendar: "Calendrier",

        filterText: "Recherche : numéro, objet, motivation",
        relevance: "Pertinence",
        filterMin: "Pertinence minimale",
        filterMax: "Pertinence maximale",
        resetFilters: "Réinitialiser",
        filterLevel: "Classement",
        levelHigh: "Pertinent",
        levelMedium: "Juste en dessous",
        levelLow: "Non pertinent",
        levelAll: "Tous",
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
        tagConfirmed: "confirmé",
        localSuffix: " (local seulement)",
        tagSummarizing: "résumé en cours",
        tagQueued: "en attente",
        tagFailed: "résumé échoué",

        close: "Fermer",
        resizeDetail: "Largeur du panneau de détail (flèches ; double-clic : par défaut)",
        resizeValue: (pct) => `Panneau de détail : ${pct} % de la largeur`,
        judgement: "Arrêt",
        openOriginal: "Ouvrir l'arrêt sur bger.ch",
        openLocal: "Ouvrir le texte intégral enregistré",
        question: "Question juridique",
        whyRelevant: "Pourquoi pertinent",
        whyNotRelevant: "Pourquoi non pertinent",
        rule: "À retenir pour la pratique",
        criteria: "Critères et passages de l'arrêt",
        quoteExact: "mot pour mot dans l'arrêt",
        quoteApprox: "en substance dans l'arrêt",
        quoteMissing: "introuvable dans l'arrêt",
        signals: "Signaux du tri",
        criteriaMissing: "Le tri ne fournit pas encore de critères avec citations pour cet arrêt.",
        germanOnly: "Cette partie n'existe qu'en allemand.",
        refPrefix: "consid. ",
        similarCases: "Arrêts antérieurs similaires",
        similarity: (s) => `similarité ${s}`,

        summary: "Résumé",
        langDe: "Allemand",
        langFr: "Français",
        origin_generated: "généré",
        origin_edited: "modifié",
        originGeneratedHint: (model) => `Établi automatiquement (${model}), sans modification manuelle.`,
        origin_aligned: "aligné",
        originEditedHint: "Modifié à la main.",
        originAlignedHint: (model) => `Aligné par Apertus (${model}) sur la version modifiée de l'autre langue. À relire.`,
        summarize: "Résumer",
        summarizing: "Résumé en cours de rédaction …",
        summaryQueued: "Dans la file d'attente – le résumé sera généré automatiquement …",
        noSummaryYet: "Aucun résumé n'a encore été établi pour cet arrêt.",
        notAutoSummarized: (threshold, score) =>
            `La pertinence ${score} est inférieure à ${threshold} : cet arrêt n'est pas résumé automatiquement.`,
        errorSummary: "Le résumé n'a pas pu être généré.",
        retry: "Réessayer",
        save: "Enregistrer",
        saving: "Enregistrement …",
        saved: "Enregistré",
        unsaved: "Modifications non enregistrées",
        errorSave: "L'enregistrement a échoué.",
        savingAligning: "Enregistrement, Apertus aligne l'autre langue …",
        resetSummary: "Réinitialiser",
        resetSummaryHint: "Supprime la version modifiée des deux langues et réaffiche le texte rédigé par Apertus.",
        resetConfirm: "Supprimer la version modifiée (allemand et français) et revenir au texte généré ?",
        resetting: "Réinitialisation …",
        resetDone: "Texte généré rétabli",
        errorReset: "La réinitialisation a échoué.",
        pendingLegacy: "Modification antérieure pas encore reprise : « Enregistrer » la reprend, « Réinitialiser » la supprime.",
        alignedToast: (lang) => `Enregistré. Apertus a aligné la version ${lang === "de" ? "allemande" : "française"} sur votre modification. Veuillez la relire.`,
        alignSkippedToast: (lang) => `Enregistré. La version ${lang === "de" ? "allemande" : "française"} a été modifiée à la main et n'a donc pas été alignée automatiquement.`,
        alignFailedToast: (lang) => `Enregistré. La version ${lang === "de" ? "allemande" : "française"} n'a pas pu être alignée (Apertus injoignable) et reste inchangée.`,
        openSuvaFormat: "Ouvrir au format Suva",
        exportTitle: "Arrêt du TF",

        correction: "Correction",
        correctionNote: "Votre décision détermine le classement de cet arrêt, et le système en tire des enseignements pour les arrêts similaires (même domaine, mêmes critères). Un second clic l'annule.",
        markRelevant: "Pertinent pour la Suva",
        markNotRelevant: "Non pertinent",
        feedbackSaved: "Décision enregistrée.",
        feedbackLearned: (n) => `Décision enregistrée. Le système en a tiré des enseignements : ${n} ${n === 1 ? "autre arrêt réévalué" : "autres arrêts réévalués"}.`,
        learnedDeltaHint: (base) => `Appris des décisions des juristes. Sans elles : ${base}`,
        feedbackLocalOnly: "La correction n'a pas pu être enregistrée sur le serveur.",

        tagSingleJudge: "Juge unique",
        principleLabel: (n) => `Portée de principe ${n}/3`,
        uvUseLabel: (n) => `Intérêt LAA ${n}/3`,
        suvaSelected: "Sélectionné par la Suva",
        summaryBasis: "Sur quoi s'appuie le résumé",
        summaryBasisLaws: "Articles de loi",
        summaryBasisConsiderations: "Considérants déterminants",
        summaryBasisExamples: "Exemples de style",

        kbTitle: "Base de données",
        kbIntro: "Tous les résumés enregistrés. Ils peuvent être recherchés et modifiés ici.",
        kbSearchPlaceholder: "Rechercher dans les résumés …",
        kbCount: (n) => `${n} entrée${n === 1 ? "" : "s"}`,
        kbEmpty: "Aucun résumé enregistré pour l'instant.",
        kbNoMatch: "Aucun résultat.",
        errorKb: "La base de données n'a pas pu être lue. Le backend est-il démarré ?",
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
