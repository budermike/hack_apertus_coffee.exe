/* script.js — UI state and rendering. Depends on i18n.js, datepicker.js and api.js (loaded before). */

/** From this relevance on a judgement counts as relevant and is summarized automatically.
 *  Must equal green_threshold / yellow_threshold in configs/triage.json: the triage maps its
 *  list threshold onto exactly these values (docs/ISSUE_RECALL.md). */
const SUMMARY_THRESHOLD = 60;
const MEDIUM_THRESHOLD = 40;

/** Relevance levels, highest first. */
const LEVELS = ["high", "medium", "low"];
/** Score a judgement is moved to when the user puts it into a level its base score is not in. */
const LEVEL_TARGET_SCORE = { high: 85, medium: 55, low: 20 };

const QUOTE_STATUS = {
    exakt: { key: "quoteExact", tag: "ok" },
    "ungefähr": { key: "quoteApprox", tag: "warn" },
    fehlt: { key: "quoteMissing", tag: "bad" },
};

const COLUMNS = [
    { key: "caseNumber", label: "colCase" },
    { key: "date", label: "colDate" },
    { key: "area", label: "colArea" },
    { key: "score", label: "colRelevance" },
    { key: null, label: "colStatus" },
];

const state = {
    items: [],                 // judgements from the last search (incl. backend `adjustment`)
    hasSearched: false,
    searching: false,
    error: null,               // i18n key of a list-level error
    selectedId: null,          // judgement shown in the detail panel
    sort: { key: "score", dir: -1 },
    filter: { text: "", min: 0, max: 100 },
    criteriaOpen: false,       // "Kriterien und Belege" expanded? (kept while switching cases)

    summaries: {},             // id -> { de, fr } as stored in the database
    summaryLoading: new Set(), // ids currently being summarized
    summaryError: new Set(),   // ids whose summary failed
    drafts: {},                // id -> { de, fr } while the user edits a summary
    saveStatus: {},            // id -> "saving" | "saved" | "error"

    /* "Anpassen" per summary language; keys are `${id}:${lang}` */
    adjustOpen: new Set(),
    adjustText: {},            // key -> instruction typed so far
    adjustBusy: new Set(),
    adjustError: new Set(),

    kbEntries: [],
    kbQuery: "",
    activeTab: localStorage.getItem("tab") === "kb" ? "kb" : "search",
};

/* ---------- helpers ---------- */

const $ = (sel) => document.querySelector(sel);

/** el("p", { class: "note", text: "…", onclick: fn }, child, [more, children]) */
function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
        if (value === null || value === undefined || value === false) continue;
        if (key === "class") node.className = value;
        else if (key === "text") node.textContent = value;
        else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
        else if (value === true) node[key] = true;
        else node.setAttribute(key, value);
    }
    node.append(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false));
    return node;
}

function findItem(id) {
    return state.items.find((i) => i.id === id);
}

/** Displayed score: base score + net manual adjustment, clamped to 0..100. */
function effectiveScore(item) {
    return Math.max(0, Math.min(100, item.score + (item.adjustment || 0)));
}

function scoreLevel(score) {
    if (score >= SUMMARY_THRESHOLD) return "high";
    if (score >= MEDIUM_THRESHOLD) return "medium";
    return "low";
}

const itemLevel = (item) => scoreLevel(effectiveScore(item));

/** "Unfallversicherung – Kausalzusammenhang bei Schleudertrauma" */
function caseTitle(entry) {
    return [areaName(entry.area), loc(entry.subject)].filter(Boolean).join(" – ");
}

/** "E3.1, E5.4" -> "E. 3.1, E. 5.4" (FR: "consid. 3.1, consid. 5.4") */
function formatRef(at) {
    return (at || "").split(/,\s*/).filter(Boolean)
        .map((part) => (/^E\d/.test(part) ? t("refPrefix") + part.slice(1) : part))
        .join(", ");
}

function spinner() {
    return el("span", { class: "spinner dark", "aria-hidden": "true" });
}

let toastTimer;

function toast(message) {
    const node = $("#toast");
    node.textContent = message;
    node.style.display = "block";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { node.style.display = "none"; }, 4000);
}

/** Re-renders `container` and puts the caret back into the field the user was typing in. */
function preservingFocus(container, render) {
    const active = document.activeElement;
    const key = active && container.contains(active) ? active.dataset.focusKey : null;
    const caret = key ? [active.selectionStart, active.selectionEnd, active.scrollTop] : null;
    render();
    if (!key) return;
    const node = container.querySelector(`[data-focus-key="${CSS.escape(key)}"]`);
    if (!node || node.disabled) return;
    node.focus({ preventScroll: true });
    node.setSelectionRange(caret[0], caret[1]);
    node.scrollTop = caret[2];
}

/* ---------- theme ---------- */

function toggleTheme() {
    const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("theme", theme);
}

/* ---------- date inputs (typing + calendar) ---------- */

function setDateInput(textInput, iso) {
    textInput.value = formatDate(iso);
}

function setupDateInput(textInput) {
    const button = document.querySelector(`.calendar-btn[data-target="${textInput.id}"]`);
    createDatePicker(textInput, button, {
        maxIso: () => toIso(new Date()),   // future dates are not allowed
        onPick: (iso) => {
            setDateInput(textInput, iso);
            textInput.classList.remove("invalid");
        },
    });
    textInput.addEventListener("blur", () => {
        const iso = parseDate(textInput.value);
        if (iso) setDateInput(textInput, iso);
    });
}

/** Re-format the date fields after a language change. */
function refreshDateInputs() {
    for (const input of [$("#date-from"), $("#date-to")]) {
        const iso = parseDate(input.value);
        if (iso) setDateInput(input, iso);
    }
}

/* ---------- search ---------- */

function validateRange() {
    const fromInput = $("#date-from");
    const toInput = $("#date-to");
    fromInput.classList.remove("invalid");
    toInput.classList.remove("invalid");

    if (!fromInput.value.trim() || !toInput.value.trim()) {
        if (!fromInput.value.trim()) fromInput.classList.add("invalid");
        if (!toInput.value.trim()) toInput.classList.add("invalid");
        return { error: "errMissing" };
    }
    const from = parseDate(fromInput.value);
    const to = parseDate(toInput.value);
    if (!from || !to) {
        if (!from) fromInput.classList.add("invalid");
        if (!to) toInput.classList.add("invalid");
        return { error: "errInvalid" };
    }
    if (from > to) {
        fromInput.classList.add("invalid");
        return { error: "errOrder" };
    }
    if (to > toIso(new Date())) {
        toInput.classList.add("invalid");
        return { error: "errFuture" };
    }
    return { from, to };
}

let formErrorKey = null;

function showFormError(key) {
    formErrorKey = key;
    $("#form-error").textContent = key ? t(key) : "";
}

async function onSearch(event) {
    event.preventDefault();
    const range = validateRange();
    showFormError(range.error || null);
    if (range.error || state.searching) return;

    const button = $("#search-btn");
    button.disabled = true;
    button.classList.add("loading");
    state.searching = true;
    state.selectedId = null;
    renderResults();

    try {
        state.items = await searchJudgements(range.from, range.to, currentLang);
        state.error = null;
    } catch (err) {
        console.error(err);
        state.items = [];
        state.error = "errorSearch";
    }
    state.hasSearched = true;
    state.searching = false;
    button.disabled = false;
    button.classList.remove("loading");
    renderResults();

    autoSummarizeRelevant();
}

/** Sufficiently relevant judgements are summarized right away, in the background.
 *  All others get a "Zusammenfassen" button in the detail panel. */
async function autoSummarizeRelevant() {
    const ids = state.items.filter((i) => itemLevel(i) === "high").map((i) => i.id);
    if (ids.length === 0) return;
    await Promise.all(ids.map(loadSummary));
    refreshKb();
}

/* ---------- filters + sorting ---------- */

function isFilterActive() {
    const f = state.filter;
    return f.text !== "" || f.min > 0 || f.max < 100;
}

/** Text filter: every word must appear in case number, subject, question or explanation (DE or FR). */
function matchesFilter(item) {
    const score = effectiveScore(item);
    const { text, min, max } = state.filter;
    if (score < min || score > max) return false;
    if (!text) return true;
    const haystack = [
        item.caseNumber, areaName(item.area),
        item.subject?.de, item.subject?.fr, item.question?.de, item.question?.fr,
        item.explanation?.de, item.explanation?.fr,
    ].join(" ").toLowerCase();
    return text.toLowerCase().split(/\s+/).every((term) => haystack.includes(term));
}

function sortValue(item, key) {
    return key === "score" ? effectiveScore(item) : item[key] ?? "";
}

/** Items currently shown: filtered, then sorted by the chosen column. */
function visibleItems() {
    const { key, dir } = state.sort;
    return state.items.filter(matchesFilter).sort((a, b) => {
        const x = sortValue(a, key);
        const y = sortValue(b, key);
        if (x !== y) return (x < y ? -1 : 1) * dir;
        return a.caseNumber < b.caseNumber ? -1 : 1;
    });
}

/** Plain text field for whole numbers 0..100: strip everything that is not a digit. */
function readScoreInput(input, fallback) {
    const digits = input.value.replace(/\D/g, "");
    if (digits !== input.value) input.value = digits;
    return digits === "" ? fallback : Math.min(100, parseInt(digits, 10));
}

function onFilterChange() {
    state.filter = {
        text: $("#filter-text").value.trim(),
        min: readScoreInput($("#filter-min"), 0),
        max: readScoreInput($("#filter-max"), 100),
    };
    renderList();
}

function resetFilters() {
    $("#filter-text").value = "";
    $("#filter-min").value = "0";
    $("#filter-max").value = "100";
    onFilterChange();
}

function sortBy(key) {
    const sameKey = state.sort.key === key;
    state.sort = { key, dir: sameKey ? -state.sort.dir : (key === "score" ? -1 : 1) };
    renderList();
    document.querySelector(`#results-head button[data-key="${key}"]`)?.focus();
}

/* ---------- selecting a case ---------- */

function selectCase(id) {
    const changed = state.selectedId !== id;
    state.selectedId = id;
    renderList();
    renderDetail();
    if (changed) {
        $("#detail-pane").scrollTop = 0;
        if (window.innerWidth <= 1000) window.scrollTo(0, 0);
    }
    // already in the database from an earlier session: fetch the stored summary
    const item = findItem(id);
    if (item?.summarized && !state.summaries[id]) loadSummary(id);
}

function closeDetail() {
    const id = state.selectedId;
    state.selectedId = null;
    renderList();
    renderDetail();
    document.querySelector(`#results-rows tr[data-id="${CSS.escape(id)}"]`)?.focus();
}

/* ---------- relevance correction ---------- */

/** "Für Suva relevant" / "Nicht relevant": moves the judgement into the level `high` / `low`.
 *  Clicking the active choice again takes a manual correction back. */
async function setRelevance(id, level) {
    const item = findItem(id);
    if (!item) return;
    const before = itemLevel(item);
    if (level === before && !item.adjustment) return;   // already there by its own score

    if (level === before || scoreLevel(item.score) === level) item.adjustment = 0;
    else item.adjustment = LEVEL_TARGET_SCORE[level] - item.score;

    const after = itemLevel(item);
    const direction = LEVELS.indexOf(after) < LEVELS.indexOf(before) ? +1 : -1;
    renderList();
    renderDetail();
    if (after === "high" && !state.summaries[id]) loadSummary(id).then(refreshKb);

    try {
        await sendFeedback(id, item.adjustment, direction, after);   // backend persists it for future lookups
        toast(t("feedbackSaved"));
    } catch (err) {
        console.error(err);
        toast(t("errorFeedback"));
    }
}

/* ---------- summaries ---------- */

async function loadSummary(id) {
    if (state.summaries[id] || state.summaryLoading.has(id)) return;
    state.summaryLoading.add(id);
    state.summaryError.delete(id);
    onSummaryChange(id);
    try {
        const entry = await summarizeJudgement(id);   // generates DE + FR, stores in the database
        state.summaries[id] = { de: entry.de, fr: entry.fr };
        const item = findItem(id);
        if (item) item.summarized = true;
    } catch (err) {
        console.error(err);
        state.summaryError.add(id);
    } finally {
        state.summaryLoading.delete(id);
        onSummaryChange(id);
    }
}

function onSummaryChange(id) {
    renderList();
    if (id === state.selectedId) renderDetail();
}

async function summarizeManually(id) {
    await loadSummary(id);
    refreshKb();
}

function isDirty(id) {
    const draft = state.drafts[id];
    const stored = state.summaries[id];
    return Boolean(draft && stored && (draft.de !== stored.de || draft.fr !== stored.fr));
}

/** Text currently in the editor: the draft if there is one, otherwise the stored summary. */
function currentTexts(id) {
    return state.drafts[id] || state.summaries[id];
}

function onDraftInput(id, lang, value, editor) {
    state.drafts[id] = { ...currentTexts(id), [lang]: value };
    delete state.saveStatus[id];
    updateEditorFooter(editor, id);   // no full re-render while typing
}

async function onSave(id) {
    if (!isDirty(id) || state.saveStatus[id] === "saving") return;
    const texts = state.drafts[id];
    state.saveStatus[id] = "saving";
    renderEditors();
    try {
        const entry = await saveSummary(id, texts);
        state.summaries[id] = { de: entry.de, fr: entry.fr };
        // keep the draft if the user typed on while saving
        const draft = state.drafts[id];
        if (draft && draft.de === entry.de && draft.fr === entry.fr) delete state.drafts[id];
        state.saveStatus[id] = "saved";
    } catch (err) {
        console.error(err);
        state.saveStatus[id] = "error";
    }
    renderEditors();
    refreshKb();
}

function toggleAdjust(key) {
    if (state.adjustOpen.has(key)) state.adjustOpen.delete(key);
    else state.adjustOpen.add(key);
    state.adjustError.delete(key);
    renderEditors();
    if (state.adjustOpen.has(key)) {
        // the editor may be rendered in both tabs: focus the visible one
        [...document.querySelectorAll(`[data-adjust-input="${CSS.escape(key)}"]`)]
            .find((input) => input.offsetParent)?.focus();
    }
}

/** Lets Apertus rewrite the summary in one language. The other language is not touched;
 *  the result lands in the editor as an unsaved change. */
async function onAdjust(id, lang) {
    const key = `${id}:${lang}`;
    const instruction = (state.adjustText[key] || "").trim();
    if (!instruction || state.adjustBusy.has(key)) return;

    state.adjustBusy.add(key);
    state.adjustError.delete(key);
    renderEditors();
    try {
        const result = await adjustSummary(id, lang, currentTexts(id)[lang], instruction);
        state.drafts[id] = { ...currentTexts(id), [lang]: result.text };
        delete state.saveStatus[id];
        state.adjustOpen.delete(key);
        delete state.adjustText[key];
    } catch (err) {
        console.error(err);
        state.adjustError.add(key);
    } finally {
        state.adjustBusy.delete(key);
        renderEditors();
    }
}

/* ---------- database ---------- */

let kbRequest = 0;

async function refreshKb() {
    const requestId = ++kbRequest;
    try {
        const entries = await searchKnowledgeBase(state.kbQuery, currentLang);
        if (requestId !== kbRequest) return;   // a newer query is in flight
        state.kbEntries = entries;
        for (const e of entries) state.summaries[e.id] = { de: e.de, fr: e.fr };
    } catch (err) {
        console.error(err);
        state.kbEntries = [];
    }
    renderKb();
}

/** TEMPORARY (development): wipes the stored summaries and the relevance feedback. */
async function onClearDb() {
    if (!confirm(t("clearDbConfirm"))) return;
    try {
        await clearDatabase();
    } catch (err) {
        console.error(err);
        alert(t("errorClearDb"));
        return;
    }
    state.summaries = {};
    state.drafts = {};
    state.saveStatus = {};
    state.adjustOpen.clear();
    state.adjustText = {};
    for (const item of state.items) {
        item.summarized = false;
        item.adjustment = 0;
    }
    renderResults();
    refreshKb();
}

/* ---------- rendering: result list ---------- */

function relevanceScale(item) {
    const score = effectiveScore(item);
    return el("div", { class: "scale-cell" },
        el("div", { class: `scale ${scoreLevel(score)}`, title: t("scaleTitle", score, SUMMARY_THRESHOLD) },
            el("i", { style: `left:${SUMMARY_THRESHOLD}%` }),
            el("b", { style: `left:${score}%` })),
        el("span", { class: "pnum", text: String(score) }));
}

function statusTags(item) {
    return [
        state.summaryLoading.has(item.id) && el("span", { class: "tag blue", text: t("tagSummarizing") }),
        item.summarized && el("span", { class: "tag ok", text: t("tagInDb") }),
        Boolean(item.adjustment) && el("span", { class: "tag", text: t("tagCorrected") }),
    ];
}

function renderRow(item) {
    return el("tr", {
        tabindex: "0",
        "data-id": item.id,
        "aria-selected": String(item.id === state.selectedId),
        onclick: () => selectCase(item.id),
        onkeydown: (e) => { if (e.key === "Enter") selectCase(item.id); },
    },
        el("td", { class: "nr" }, item.caseNumber, el("div", { class: "sub", text: (item.lang || "").toUpperCase() })),
        el("td", { class: "pnum", text: formatDate(item.date) }),
        el("td", {}, areaName(item.area), el("div", { class: "sub", text: loc(item.subject) || outcomeName(item.outcome) })),
        el("td", {}, relevanceScale(item)),
        el("td", {}, el("div", { class: "row" }, statusTags(item))));
}

function renderHead() {
    const { key, dir } = state.sort;
    $("#results-head").replaceChildren(...COLUMNS.map((col) => {
        if (!col.key) return el("th", { text: t(col.label) });
        const sorted = col.key === key;
        return el("th", { "aria-sort": sorted && (dir > 0 ? "ascending" : "descending") },
            el("button", {
                type: "button", "data-key": col.key,
                text: t(col.label) + (sorted ? (dir > 0 ? " ▲" : " ▼") : ""),
                onclick: () => sortBy(col.key),
            }));
    }));
}

function renderList() {
    const visible = visibleItems();
    const total = state.items.length;
    const searching = state.searching;

    renderHead();
    $("#results-rows").replaceChildren(...visible.map(renderRow));
    $("#results-table").hidden = searching || visible.length === 0;
    $("#filter-bar").hidden = searching || total === 0;
    $("#results-count").textContent = isFilterActive()
        ? t("resultsCountFiltered", visible.length, total)
        : t("resultsCount", total);

    let statusKey = null;
    if (searching) statusKey = "searching";
    else if (state.error) statusKey = state.error;
    else if (!state.hasSearched) statusKey = "emptyInitial";
    else if (total === 0) statusKey = "emptyNoResults";
    else if (visible.length === 0) statusKey = "emptyFiltered";
    const status = $("#results-status");
    status.hidden = !statusKey;
    status.textContent = statusKey ? t(statusKey) : "";
}

/* ---------- rendering: summary editor (detail panel and database) ---------- */

function saveStatusText(id) {
    const status = state.saveStatus[id];
    if (status === "saving") return t("saving");
    if (status === "error") return t("errorSave");
    if (isDirty(id)) return t("unsaved");
    if (status === "saved") return `✓ ${t("saved")}`;
    return "";
}

function updateEditorFooter(editor, id) {
    editor.querySelector(".save-btn").disabled = !isDirty(id) || state.saveStatus[id] === "saving";
    const status = editor.querySelector(".save-status");
    status.textContent = saveStatusText(id);
    status.classList.toggle("error", state.saveStatus[id] === "error");
}

/** Instruction field shown below one summary box after clicking "Anpassen". */
function adjustForm(scope, id, lang) {
    const key = `${id}:${lang}`;
    const busy = state.adjustBusy.has(key);
    const input = el("input", {
        type: "text", autocomplete: "off", disabled: busy,
        placeholder: t("adjustPlaceholder"), "aria-label": t("adjustPlaceholder"),
        "data-focus-key": `${scope}:adjust:${key}`, "data-adjust-input": key,
        oninput: (e) => { state.adjustText[key] = e.target.value; },
    });
    input.value = state.adjustText[key] || "";
    return el("form", {
        class: "adjust-form",
        onsubmit: (e) => { e.preventDefault(); onAdjust(id, lang); },
    },
        input,
        el("button", { type: "submit", class: "btn small", disabled: busy },
            busy && spinner(), t(busy ? "adjusting" : "adjustApply")),
        state.adjustError.has(key) && el("p", { class: "form-error", text: t("errorAdjust") }));
}

/** German and French summary next to each other, each editable and with its own "Anpassen";
 *  one save button for both. `scope` tells the detail panel ("res") from the database ("kb")
 *  so the caret can be restored after a re-render. */
function summaryEditor(scope, id) {
    const texts = currentTexts(id);
    const editor = el("div", { class: "summary-editor" });

    const columns = ["de", "fr"].map((lang) => {
        const key = `${id}:${lang}`;
        const busy = state.adjustBusy.has(key);
        const open = state.adjustOpen.has(key);
        const name = t(lang === "de" ? "langDe" : "langFr");
        const textarea = el("textarea", {
            class: "summary-textarea", lang, spellcheck: "true", disabled: busy,
            "aria-label": `${t("summary")} ${name}`,
            "data-focus-key": `${scope}:${key}`,
            oninput: (e) => onDraftInput(id, lang, e.target.value, editor),
        });
        textarea.value = texts[lang] || "";   // content must be set as a property
        return el("div", { class: "duo-col" },
            el("div", { class: "duo-head" },
                el("h4", { text: name }),
                el("button", {
                    type: "button", class: "btn small", text: t("adjust"),
                    "aria-expanded": String(open), "aria-pressed": String(open), disabled: busy,
                    onclick: () => toggleAdjust(key),
                })),
            textarea,
            open && adjustForm(scope, id, lang));
    });

    editor.append(
        el("div", { class: "duo" }, columns),
        el("div", { class: "row editor-footer" },
            el("button", { type: "button", class: "btn pri save-btn", text: t("save"), onclick: () => onSave(id) }),
            el("span", { class: "note save-status", "aria-live": "polite" })));
    updateEditorFooter(editor, id);
    return editor;
}

/** Re-renders every place a summary editor is shown. */
function renderEditors() {
    renderDetail();
    renderKb();
}

/* ---------- rendering: case detail ---------- */

function criteriaSection(item) {
    if (!item.criteria?.length) return null;
    const list = el("ul", { class: "crit", hidden: !state.criteriaOpen }, item.criteria.map((c) => {
        const status = QUOTE_STATUS[c.status];
        return el("li", {},
            el("strong", { text: `${c.id} ` }), criterionName(c.id), " ",
            status && el("span", { class: `tag ${status.tag}`, text: t(status.key) }),
            el("blockquote", { lang: item.lang }, `«${c.quote}»`, el("div", { class: "sub", text: formatRef(c.at) })));
    }));
    const toggle = el("button", {
        type: "button", class: "toggle", "aria-expanded": String(state.criteriaOpen),
        onclick: () => {
            state.criteriaOpen = !state.criteriaOpen;
            list.hidden = !state.criteriaOpen;
            toggle.setAttribute("aria-expanded", String(state.criteriaOpen));
        },
    }, t("criteria"), el("span", { class: "sub", text: `(${item.criteria.length})` }));
    return [el("h3", {}, toggle), list];
}

function summarySection(item) {
    const id = item.id;
    const heading = el("h3", { text: t("summary") });
    if (state.summaryLoading.has(id)) {
        return [heading, el("p", { class: "note loading-line" }, spinner(), t("summarizing"))];
    }
    if (state.summaries[id]) return [heading, summaryEditor("res", id)];
    // not relevant enough for an automatic summary (or it failed): offer it by hand
    return [
        heading,
        state.summaryError.has(id)
            ? el("p", { class: "form-error", text: t("errorSummary") })
            : el("p", { class: "note", text: t("notAutoSummarized", SUMMARY_THRESHOLD) }),
        el("div", { class: "row editor-footer" },
            el("button", { type: "button", class: "btn pri", text: t("summarize"), onclick: () => summarizeManually(id) })),
    ];
}

function similarSection(item) {
    if (!item.similarCases?.length) return null;
    return [
        el("h3", { text: t("similarCases") }),
        el("table", { class: "similar" }, el("tbody", {}, item.similarCases.map((c) => el("tr", {},
            el("td", { class: "nr", text: c.caseNumber }),
            el("td", { text: c.subject || "" }),
            el("td", { class: "pnum", text: t("similarity", c.similarity.toFixed(2)) }))))),
    ];
}

function correctionSection(item) {
    const level = itemLevel(item);
    const button = (target, label, extraClass) => el("button", {
        type: "button", class: `btn ${extraClass}`, text: t(label),
        "aria-pressed": String(level === target),
        onclick: () => setRelevance(item.id, target),
    });
    return [
        el("h3", { text: t("correction") }),
        el("p", { class: "note", text: t("correctionNote") }),
        el("div", { class: "row editor-footer" },
            button("high", "markRelevant", "relevant"),
            button("low", "markNotRelevant", "")),
    ];
}

function textSection(titleKey, field, lang) {
    if (!loc(field)) return null;
    return [el("h3", { text: t(titleKey) }), el("p", { class: "read", lang, text: loc(field) })];
}

function renderDetail() {
    const item = findItem(state.selectedId);
    $("#split").classList.toggle("has-detail", Boolean(item));
    $("#detail-pane").hidden = !item;
    const detail = $("#detail");
    if (!item) {
        detail.replaceChildren();
        return;
    }

    const meta = [formatDate(item.date), loc(item.court), areaName(item.area), loc(item.subject), outcomeName(item.outcome)];
    const relevant = itemLevel(item) === "high";
    preservingFocus(detail, () => detail.replaceChildren(...[
        el("button", {
            type: "button", class: "btn detail-close", text: "×",
            "aria-label": t("close"), title: t("close"), onclick: closeDetail,
        }),
        el("h2", { text: `${t("judgement")} ${item.caseNumber}` }),
        el("p", { class: "meta" }, meta.filter(Boolean).join(", "), ". ",
            item.url && el("a", { href: item.url, target: "_blank", rel: "noopener", text: t("openOriginal") })),
        textSection("question", item.question, currentLang),
        textSection(relevant ? "whyRelevant" : "whyNotRelevant", item.explanation, currentLang),
        textSection("rule", item.rule, currentLang),
        criteriaSection(item),
        summarySection(item),
        similarSection(item),
        correctionSection(item),
    ].flat(Infinity).filter(Boolean)));
}

/* ---------- rendering: database ---------- */

function renderKbEntry(entry) {
    return el("li", { class: "kb-entry", "data-id": entry.id },
        el("details", {},
            el("summary", {},
                el("span", { class: "nr", text: entry.caseNumber }),
                el("span", { class: "kb-entry-title", text: caseTitle(entry) }),
                el("span", { class: "sub", text: `${t("storedAt")} ${formatDate(entry.storedAt.slice(0, 10))}` })),
            el("div", { class: "kb-entry-body" }, summaryEditor("kb", entry.id))));
}

function renderKb() {
    const list = $("#kb-list");
    const openIds = new Set(
        [...list.querySelectorAll(".kb-entry")]
            .filter((li) => li.querySelector("details").open)
            .map((li) => li.dataset.id),
    );

    preservingFocus(list, () => list.replaceChildren(...state.kbEntries.map((entry) => {
        const node = renderKbEntry(entry);
        if (openIds.has(entry.id)) node.querySelector("details").open = true;
        return node;
    })));

    const n = state.kbEntries.length;
    const tabCount = $("#kb-tab-count");
    tabCount.hidden = !n || Boolean(state.kbQuery);
    tabCount.textContent = n ? String(n) : "";

    $("#kb-count").textContent = n ? t("kbCount", n) : "";
    const statusKey = n ? null : (state.kbQuery ? "kbNoMatch" : "kbEmpty");
    const status = $("#kb-status");
    status.hidden = !statusKey;
    status.textContent = statusKey ? t(statusKey) : "";
}

/* ---------- tabs, language ---------- */

function renderResults() {
    renderList();
    renderDetail();
}

function renderTabs() {
    document.querySelectorAll(".tabs [role=tab]").forEach((tab) => {
        const active = tab.dataset.tab === state.activeTab;
        tab.setAttribute("aria-selected", String(active));
        tab.tabIndex = active ? 0 : -1;
    });
    $("#panel-search").hidden = state.activeTab !== "search";
    $("#panel-kb").hidden = state.activeTab !== "kb";
}

function selectTab(tab) {
    closeAllDatePickers();
    state.activeTab = tab;
    localStorage.setItem("tab", tab);
    renderTabs();
    renderEditors();   // drafts may have changed in the other tab
}

function renderLanguage() {
    closeAllDatePickers();
    applyTranslations();
    document.querySelectorAll(".lang-switch button").forEach((btn) => {
        const active = btn.dataset.lang === currentLang;
        btn.classList.toggle("active", active);
        btn.setAttribute("aria-pressed", String(active));
    });
    refreshDateInputs();
    showFormError(formErrorKey);
    renderResults();
    renderKb();
}

/* ---------- init ---------- */

function init() {
    document.querySelectorAll(".lang-switch button").forEach((btn) => {
        btn.addEventListener("click", () => {
            setLanguage(btn.dataset.lang);
            renderLanguage();
        });
    });
    $("#theme-toggle").addEventListener("click", toggleTheme);

    const tabs = [...document.querySelectorAll(".tabs [role=tab]")];
    tabs.forEach((tab, index) => {
        tab.addEventListener("click", () => selectTab(tab.dataset.tab));
        tab.addEventListener("keydown", (e) => {   // arrow keys move between tabs
            if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
            const next = tabs[(index + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
            selectTab(next.dataset.tab);
            next.focus();
        });
    });
    renderTabs();

    const fromInput = $("#date-from");
    const toInput = $("#date-to");
    setupDateInput(fromInput);
    setupDateInput(toInput);

    // default: last 3 months (challenge step 1)
    const today = new Date();
    const threeMonthsAgo = new Date(today);
    threeMonthsAgo.setMonth(today.getMonth() - 3);
    setDateInput(fromInput, toIso(threeMonthsAgo));
    setDateInput(toInput, toIso(today));

    $("#search-form").addEventListener("submit", onSearch);
    for (const id of ["#filter-text", "#filter-min", "#filter-max"]) {
        $(id).addEventListener("input", onFilterChange);
    }
    $("#filter-reset").addEventListener("click", resetFilters);
    $("#kb-clear").addEventListener("click", onClearDb);

    let kbTimer;
    $("#kb-search").addEventListener("input", (e) => {
        clearTimeout(kbTimer);
        kbTimer = setTimeout(() => {
            state.kbQuery = e.target.value.trim();
            refreshKb();
        }, 200);
    });

    // Escape closes the detail panel (unless the user is typing or a calendar is open)
    document.addEventListener("keydown", (e) => {
        if (e.key !== "Escape" || !state.selectedId || openPickers.size) return;
        if (e.target.closest("input, textarea")) return;
        closeDetail();
    });

    renderLanguage();
    refreshKb();
}

init();
