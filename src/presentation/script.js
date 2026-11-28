/* script.js — UI state and rendering.
 * Depends on i18n.js, datepicker.js, mock.js and api.js (loaded before). */

/** From this relevance on a judgement counts as relevant and is summarized automatically.
 *  Defaults as in configs/triage.json; replaced by the backend's values (GET /api/config)
 *  before the first search. */
let SUMMARY_THRESHOLD = 60;
let MEDIUM_THRESHOLD = 40;
/** Summaries are requested one after the other (this many at a time), best relevance first:
 *  each one takes several Apertus calls on a shared gateway. */
const SUMMARY_CONCURRENCY = 2;

/** Relevance levels, highest first. */
const LEVELS = ["high", "medium", "low"];
const LEVEL_LABEL = { high: "levelHigh", medium: "levelMedium", low: "levelLow" };

const QUOTE_STATUS = {
    exakt: { key: "quoteExact", tag: "ok" },
    "ungefähr": { key: "quoteApprox", tag: "warn" },
    fehlt: { key: "quoteMissing", tag: "bad" },
};

const COLUMNS = [
    { key: "caseNumber", label: "colCase", cls: "col-case" },
    { key: "date", label: "colDate", cls: "col-date" },
    { key: "area", label: "colArea", cls: "col-area" },
    { key: "score", label: "colRelevance", cls: "col-score" },
    { key: null, label: "colStatus", cls: "col-status" },
];

const state = {
    items: [],                 // judgements from the last search
    hasSearched: false,
    searching: false,
    searchProgress: null,
    searchErrorArg: null,
    error: null,               // i18n key of a list-level error
    selectedId: null,          // judgement shown in the detail panel
    sort: { key: "score", dir: -1 },
    filter: { text: "", min: 0, max: 100, level: "all" },
    criteriaOpen: false,       // "Kriterien und Belege" expanded? (kept while switching cases)

    summaries: {},             // id -> summary entry as stored in the database (see normalizeEntry)
    summaryQueue: [],          // ids waiting to be summarized, next one first
    summaryLoading: new Set(), // ids being summarized right now
    summaryError: new Set(),   // ids whose summary failed
    drafts: {},                // id -> { de, fr } while the user edits a summary
    alignedDraft: {},          // id -> { lang, text }: unsaved text Apertus regenerated for that language
    saveStatus: {},            // id -> "saving" | "saved" | "approving" | "approved" | "error"

    kbEntries: [],
    kbError: false,            // the database could not be read
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

function icon(svgMarkup) {
    const tpl = document.createElement("template");
    tpl.innerHTML = svgMarkup;
    return tpl.content.firstChild;
}

const closeIcon = () => icon('<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 3.5l9 9M12.5 3.5l-9 9"/></svg>');

function findItem(id) {
    return state.items.find((i) => i.id === id);
}

/** Displayed score: base score + net manual adjustment, 0..100, one decimal at most.
 *  This is the ONE number that is shown, filtered, sorted and compared with the thresholds. */
function effectiveScore(item) {
    const score = item.score + (item.adjustment || 0);
    return Math.round(Math.max(0, Math.min(100, score)) * 10) / 10;
}

function scoreLevel(score) {
    if (score >= SUMMARY_THRESHOLD) return "high";
    if (score >= MEDIUM_THRESHOLD) return "medium";
    return "low";
}

const itemLevel = (item) => scoreLevel(effectiveScore(item));

/** Score a judgement is moved to when the user puts it into a level its own score is not in.
 *  Follows the thresholds, so the judgement really lands in that level. */
function levelTarget(level) {
    return level === "high" ? Math.max(85, SUMMARY_THRESHOLD) : Math.min(20, Math.max(0, MEDIUM_THRESHOLD - 1));
}

/** Level the user decided by hand ("high" | "low"), or null if the judgement is as triage scored it. */
function decidedLevel(item) {
    return item.userLevel || (item.adjustment ? itemLevel(item) : null);
}
/** Relevant = at or above the threshold = summarized automatically. */
const isRelevant = (item) => itemLevel(item) === "high";

const ALL_AREA_NAMES = new Set(
    Object.values(AREA_NAMES).flatMap((names) => Object.values(names)).map((name) => name.toLowerCase()),
);

/** Area as shown in the list: its name, or the part of the subject before the bracket. */
function areaLabel(entry) {
    return areaName(entry.area) || loc(entry.subject).split("(")[0].trim();
}

/** "Unfallversicherung (Invalidenrente, Revision)" -> "Invalidenrente, Revision" */
function subjectDetail(entry) {
    const subject = loc(entry.subject).trim();
    const bracket = subject.match(/^[^(]*\(([^()]*)\)\s*$/);
    if (bracket) return bracket[1];
    return ALL_AREA_NAMES.has(subject.toLowerCase()) || subject === areaLabel(entry) ? "" : subject;
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

function toast(message, ms = 4000) {
    const node = $("#toast");
    node.textContent = message;
    node.style.display = "block";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { node.style.display = "none"; }, ms);
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

/* ---------- thresholds ---------- */

let availableRange = null;

/** Takes the thresholds (and cached date span) from the backend; keeps defaults if it does not answer. */
async function loadThresholds() {
    const config = await fetchConfig();
    const high = toNumber(config?.thresholds?.high, NaN);
    const medium = toNumber(config?.thresholds?.medium, NaN);
    if (Number.isFinite(high) && Number.isFinite(medium) && medium <= high) {
        SUMMARY_THRESHOLD = high;
        MEDIUM_THRESHOLD = medium;
    }
    if (config?.availableRange?.from && config?.availableRange?.to) {
        availableRange = config.availableRange;
        renderQuickRangeButton();
    }
}

function renderQuickRangeButton() {
    const btn = $("#quick-range-btn");
    if (!btn) return;
    if (!availableRange) {
        btn.hidden = true;
        return;
    }
    btn.hidden = false;
    btn.textContent = `${t("quickRangePrefix")}${formatDate(availableRange.from)} – ${formatDate(availableRange.to)}`;
}

let thresholdsReady = Promise.resolve();

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

let periodSearchGeneration = 0;

async function onSearch(event) {
    event.preventDefault();
    const range = validateRange();
    showFormError(range.error || null);
    if (range.error || state.searching) return;

    const searchGen = ++periodSearchGeneration;
    const button = $("#search-btn");
    button.disabled = true;
    button.classList.add("loading");
    state.searching = true;
    state.searchProgress = null;
    state.searchErrorMsg = null;
    state.selectedId = null;
    state.summaryQueue = [];        // summaries still waiting belong to the previous search
    state.summaryError.clear();
    queueGeneration += 1;
    queuePollErrors = 0;
    trackedQueueIds.clear();
    clearTimeout(queuePollTimer);
    renderResults();

    try {
        await thresholdsReady;      // levels must be judged with the backend's thresholds
        if (!USE_MOCK) {
            let period = await ensurePeriod(range.from, range.to);
            let pollErrors = 0;
            while (period && period.status === "running") {
                if (searchGen !== periodSearchGeneration) return;
                state.searchProgress = period;
                renderList();
                await new Promise((resolve) => setTimeout(resolve, 1200));
                if (searchGen !== periodSearchGeneration) return;
                try {
                    period = await periodStatus(range.from, range.to);
                    pollErrors = 0;
                } catch (pollErr) {
                    console.warn("period status poll error", pollErr);
                    pollErrors += 1;
                    if (pollErrors >= 5) throw pollErr;
                }
            }
            if (period && period.status === "error") {
                state.items = [];
                state.error = "periodError";
                state.searchErrorArg = period.error || period.message || "";
            } else {
                state.items = await searchJudgements(range.from, range.to, currentLang);
                state.error = null;
                state.searchErrorArg = null;
            }
        } else {
            state.items = await searchJudgements(range.from, range.to, currentLang);
            state.error = null;
            state.searchErrorArg = null;
        }
    } catch (err) {
        console.error(err);
        state.items = [];
        state.error = "errorSearch";
        state.searchErrorArg = null;
    }
    if (searchGen !== periodSearchGeneration) return;
    state.hasSearched = true;
    state.searching = false;
    state.searchProgress = null;
    frReasonAttempted.clear();
    button.disabled = false;
    button.classList.remove("loading");
    renderResults();

    queueRelevantSummaries();
}

/* ---------- filters + sorting ---------- */

function isFilterActive() {
    const f = state.filter;
    return f.text !== "" || f.min > 0 || f.max < 100 || f.level !== "all";
}

/** Text filter: every word must appear in case number, area, subject, question or explanation. */
function matchesFilter(item) {
    const score = effectiveScore(item);
    const { text, min, max, level } = state.filter;
    if (score < min || score > max) return false;
    if (level !== "all" && itemLevel(item) !== level) return false;
    if (!text) return true;
    const haystack = [
        item.caseNumber, item.caseNumber.replace("_", " "), item.id, areaName(item.area),
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
    const value = digits === "" ? fallback : Math.min(100, parseInt(digits, 10));
    const shown = digits === "" ? "" : String(value);
    if (shown !== input.value) input.value = shown;
    return value;
}

function onFilterChange() {
    state.filter = {
        ...state.filter,
        text: $("#filter-text").value.trim(),
        min: readScoreInput($("#filter-min"), 0),
        max: readScoreInput($("#filter-max"), 100),
    };
    renderList();
}

function setLevelFilter(level) {
    state.filter.level = level;
    renderList();
    document.querySelector(`#filter-level button[data-level="${level}"]`)?.focus();
}

function resetFilters() {
    $("#filter-text").value = "";
    $("#filter-min").value = "0";
    $("#filter-max").value = "100";
    state.filter.level = "all";
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
    // the open case goes first: its automatic summary, or the one stored in an earlier session
    const item = findItem(id);
    const status = item && summaryStatus(item);
    if (status === "queued" || status === "missing" || (status === "manual" && item.summarized)) {
        requestSummary(id, { first: true });
    }
}

function closeDetail() {
    const id = state.selectedId;
    state.selectedId = null;
    renderList();
    renderDetail();
    document.querySelector(`#results-rows tr[data-id="${CSS.escape(id)}"]`)?.focus();
}

/* ---------- relevance correction ---------- */

/** "Für Suva relevant" / "Nicht relevant": the user's own decision for this judgement.
 *  It moves the judgement into the level `high` / `low` (or confirms the level it has);
 *  clicking the active decision again takes it back. Every click is sent as feedback. */
async function setRelevance(id, level) {
    const item = findItem(id);
    if (!item) return;
    const before = itemLevel(item);
    if (decidedLevel(item) === level) {
        item.userLevel = null;
        item.adjustment = 0;
    } else {
        item.userLevel = level;
        item.adjustment = scoreLevel(item.score) === level
            ? 0 : Math.round((levelTarget(level) - item.score) * 10) / 10;
    }

    const after = itemLevel(item);
    const direction = Math.sign(LEVELS.indexOf(before) - LEVELS.indexOf(after));   // +1 raised, -1 lowered, 0 same level
    // relevant now: summarize automatically; no longer relevant: stop waiting for a summary
    if (isRelevant(item)) requestSummary(id, { first: true });
    else {
        state.summaryQueue = state.summaryQueue.filter((queued) => queued !== id);
        trackedQueueIds.delete(id);
        if (!USE_MOCK) cancelQueuedSummary(id).catch((err) => console.warn(err));
    }
    renderList();
    renderDetail();

    const { stored } = await sendFeedback(id, item.adjustment, direction, after, item.userLevel);
    item.feedbackLocalOnly = !stored && Boolean(item.userLevel);
    refreshRow(id);
    if (id === state.selectedId) renderDetail();
    toast(t(stored ? "feedbackSaved" : "feedbackLocalOnly"), stored ? 4000 : 7000);
}

/* ---------- summaries: server-side queue (#68) + mock fallback ---------- */

const trackedQueueIds = new Set();
const fetchingStored = new Set();
let queuePollTimer = null;
let queueGeneration = 0;
let queuePollErrors = 0;

/** Where the summary of a judgement stands. List tags, detail panel and messages all ask
 *  here, so they cannot contradict each other:
 *    ready    stored summary is loaded          loading  being fetched / generated
 *    queued   waiting for its turn              error    the request failed
 *    missing  relevant, but nothing requested   manual   below the threshold: only on request */
function summaryStatus(item) {
    const id = item.id;
    if (state.summaries[id]) return "ready";
    if (state.summaryLoading.has(id)) return "loading";
    if (state.summaryQueue.includes(id)) return "queued";
    if (state.summaryError.has(id)) return "error";
    return isRelevant(item) ? "missing" : "manual";
}

function applyServerQueueStatus(status, gen) {
    if (!status || gen !== queueGeneration) return;
    const prevActive = new Set([...state.summaryLoading, ...state.summaryQueue, ...state.summaryError]);
    state.summaryLoading = new Set(status.running || []);
    state.summaryQueue = [...(status.queued || [])];
    state.summaryError = new Set(Object.keys(status.failed || {}));

    const touched = new Set([...prevActive, ...state.summaryLoading, ...state.summaryQueue, ...state.summaryError]);
    for (const id of status.done || []) {
        touched.add(id);
        if (!state.summaries[id] && !fetchingStored.has(id)) {
            fetchStoredIntoState(id, gen);
        }
    }
    for (const id of touched) onSummaryChange(id);

    clearTimeout(queuePollTimer);
    if (state.summaryLoading.size > 0 || state.summaryQueue.length > 0) {
        queuePollTimer = setTimeout(() => pollServerQueue(gen), 1500);
    }
}

async function fetchStoredIntoState(id, gen = queueGeneration) {
    if (state.summaries[id] || fetchingStored.has(id)) return;
    fetchingStored.add(id);
    try {
        const entry = await fetchStoredSummary(id);
        if (gen !== queueGeneration) return;
        state.summaries[id] = entry;
        const item = findItem(id);
        if (item) item.summarized = true;
        scheduleKbRefresh();
    } catch (err) {
        console.error(err);
    } finally {
        fetchingStored.delete(id);
        if (gen === queueGeneration) onSummaryChange(id);
    }
}

async function pollServerQueue(gen = queueGeneration) {
    if (USE_MOCK || gen !== queueGeneration || trackedQueueIds.size === 0) return;
    try {
        const status = await summaryQueueStatus([...trackedQueueIds]);
        if (gen !== queueGeneration) return;
        queuePollErrors = 0;
        applyServerQueueStatus(status, gen);
    } catch (err) {
        console.error(err);
        if (gen !== queueGeneration || trackedQueueIds.size === 0) return;
        queuePollErrors += 1;
        if (queuePollErrors < 5) {
            queuePollTimer = setTimeout(() => pollServerQueue(gen), 3000);
        }
    }
}

async function enqueueOnServer(ids, priority = false) {
    const clean = ids.filter((id) => id && !state.summaries[id]);
    if (!clean.length) return;
    const gen = queueGeneration;
    clean.forEach((id) => trackedQueueIds.add(id));
    try {
        const status = await queueSummaries(clean, priority);
        if (gen !== queueGeneration) return;
        // Refresh full trackedQueueIds snapshot so prior running/queued items are never dropped.
        if (trackedQueueIds.size > clean.length) {
            await pollServerQueue(gen);
        } else {
            applyServerQueueStatus(status, gen);
        }
    } catch (err) {
        console.error(err);
        if (gen !== queueGeneration) return;
        const cleanSet = new Set(clean);
        state.summaryQueue = state.summaryQueue.filter((id) => !cleanSet.has(id));
        clean.forEach((id) => {
            trackedQueueIds.delete(id);
            state.summaryError.add(id);
            onSummaryChange(id);
        });
    }
}

/** Puts a judgement into the summary queue (`first`: ahead of everything waiting). */
function requestSummary(id, { first = false } = {}) {
    if (state.summaries[id] || state.summaryLoading.has(id)) return;
    const item = findItem(id);
    if (!USE_MOCK) {
        if (item?.summarized) {
            fetchStoredIntoState(id);
            return;
        }
        state.summaryError.delete(id);
        if (first) state.summaryQueue = [id, ...state.summaryQueue.filter((q) => q !== id)];
        else if (!state.summaryQueue.includes(id)) state.summaryQueue.push(id);
        onSummaryChange(id);
        enqueueOnServer([id], first);
        return;
    }
    state.summaryError.delete(id);
    state.summaryQueue = state.summaryQueue.filter((queued) => queued !== id);
    if (first) state.summaryQueue.unshift(id);
    else state.summaryQueue.push(id);
    onSummaryChange(id);
    pumpSummaryQueue();
}

function pumpSummaryQueue() {
    while (state.summaryLoading.size < SUMMARY_CONCURRENCY && state.summaryQueue.length > 0) {
        loadSummary(state.summaryQueue.shift());
    }
}

async function loadSummary(id) {
    state.summaryLoading.add(id);
    onSummaryChange(id);
    try {
        state.summaries[id] = await summarizeJudgement(id);
        const item = findItem(id);
        if (item) item.summarized = true;
    } catch (err) {
        console.error(err);
        state.summaryError.add(id);
    } finally {
        state.summaryLoading.delete(id);
        onSummaryChange(id);
        pumpSummaryQueue();
        scheduleKbRefresh();
    }
}

/** Every judgement at or above the threshold gets its summary automatically, best first.
 *  Everything below gets a "Zusammenfassen" button in the detail panel. */
function queueRelevantSummaries() {
    const ids = state.items
        .filter(isRelevant)
        .sort((a, b) => effectiveScore(b) - effectiveScore(a))
        .map((item) => item.id);
    if (!ids.length) return;
    if (!USE_MOCK) {
        const storedIds = ids.filter((id) => findItem(id)?.summarized && !state.summaries[id]);
        const missingIds = ids.filter((id) => !findItem(id)?.summarized);
        storedIds.forEach((id) => fetchStoredIntoState(id));
        if (missingIds.length) {
            missingIds.forEach((id) => {
                if (!state.summaryQueue.includes(id)) state.summaryQueue.push(id);
                onSummaryChange(id);
            });
            enqueueOnServer(missingIds, false);
        }
        return;
    }
    ids.forEach((id) => requestSummary(id));
}

function onSummaryChange(id) {
    refreshRow(id);
    if (id === state.selectedId) renderDetail();
}

/* ---------- editing, saving and approving summaries ---------- */

/** Text currently in the editor: the draft if there is one, otherwise the stored summary. */
function currentTexts(id) {
    return state.drafts[id] || state.summaries[id];
}

function isDirty(id) {
    const draft = state.drafts[id];
    const stored = state.summaries[id];
    return Boolean(draft && stored && (draft.de !== stored.de || draft.fr !== stored.fr));
}

function onDraftInput(id, lang, value, editor) {
    const texts = currentTexts(id);
    state.drafts[id] = { de: texts.de, fr: texts.fr, [lang]: value };
    delete state.saveStatus[id];
    updateEditorFooter(editor, id);   // no full re-render while typing
}

async function performSave(id) {
    if (!isDirty(id) || state.saveStatus[id] === "saving") return false;
    const texts = state.drafts[id];
    state.saveStatus[id] = "saving";
    renderEditors();
    try {
        const entry = await saveSummary(id, texts);
        state.summaries[id] = entry;
        delete state.alignedDraft[id];
        // keep the draft if the user typed on while saving
        const draft = state.drafts[id];
        if (draft && draft.de === entry.de && draft.fr === entry.fr) delete state.drafts[id];
        state.saveStatus[id] = "saved";
        renderEditors();
        refreshKb();
        return true;
    } catch (err) {
        console.error(err);
        state.saveStatus[id] = "error";
        renderEditors();
        refreshKb();
        return false;
    }
}

async function onSave(id) {
    if (!isDirty(id) || state.saveStatus[id] === "saving") return;
    const draft = state.drafts[id];
    const stored = state.summaries[id];
    const changedDe = draft.de !== stored.de;
    const changedFr = draft.fr !== stored.fr;
    // A language counts as edited by hand unless it still holds, unchanged, the text Apertus
    // regenerated for it. Every save with exactly one hand-edited language offers to
    // regenerate the other one; saving only the untouched Apertus text does not ask.
    const aligned = state.alignedDraft[id];
    const byHand = (lang, changed) => changed && !(aligned?.lang === lang && draft[lang] === aligned.text);
    const handDe = byHand("de", changedDe);
    const handFr = byHand("fr", changedFr);
    if (handDe !== handFr) {
        const edited = handDe ? "de" : "fr";
        openAlignAfterEditDialog(id, edited, edited === "de" ? "fr" : "de");
        return;
    }
    await performSave(id);
}

/** The backend stores an edit as a version that waits for approval; approving makes it
 *  the shown version (and the one the database search finds). */
async function onApprove(id) {
    if (isDirty(id) || state.saveStatus[id] === "approving") return;
    state.saveStatus[id] = "approving";
    renderEditors();
    try {
        state.summaries[id] = await approveSummary(id);
        state.saveStatus[id] = "approved";
    } catch (err) {
        console.error(err);
        state.saveStatus[id] = "error";
    }
    renderEditors();
    refreshKb();
}

function setAlignDialogBusy(dialog, busy, message = "", isError = false) {
    const status = $("#align-status");
    status.hidden = !busy && !message;
    status.classList.toggle("form-error", isError);
    if (busy) status.replaceChildren(spinner(), message);
    else status.textContent = message;
    for (const id of ["#align-apply", "#align-keep", "#align-cancel"]) {
        $(id).disabled = busy;
    }
}

function showAlignDialog({ title, desc, applyLabel, keepLabel, onApply, onKeep }) {
    const dialog = $("#align-dialog");
    $("#align-title").textContent = title;
    $("#align-desc").textContent = desc;
    const applyBtn = $("#align-apply");
    const keepBtn = $("#align-keep");
    const cancelBtn = $("#align-cancel");
    applyBtn.textContent = applyLabel;
    keepBtn.textContent = keepLabel;
    setAlignDialogBusy(dialog, false, "");

    const close = () => { if (dialog.open) dialog.close(); };
    applyBtn.onclick = () => onApply(dialog, close);
    keepBtn.onclick = () => onKeep(dialog, close);
    cancelBtn.onclick = close;
    dialog.showModal();
    applyBtn.focus();
}

function openAlignAfterEditDialog(id, sourceLang, targetLang) {
    const targetName = t(targetLang === "de" ? "langDe" : "langFr");
    showAlignDialog({
        title: t("alignPromptTitle", targetLang),
        desc: t("alignPromptBody", sourceLang, targetLang),
        applyLabel: t("alignApply"),
        keepLabel: t("alignKeep"),
        onApply: async (dialog, close) => {
            const sourceText = currentTexts(id)[sourceLang];
            const baseTarget = currentTexts(id)[targetLang];
            setAlignDialogBusy(dialog, true, t("alignBusy"));
            try {
                if (isDirty(id) && !(await performSave(id))) {
                    setAlignDialogBusy(dialog, false, t("errorSave"), true);
                    return;
                }
                const aligned = await alignSummary(id, {
                    sourceLang,
                    sourceText,
                    targetLang,
                    targetText: baseTarget,
                });
                const latest = currentTexts(id);
                state.drafts[id] = { de: latest.de, fr: latest.fr, [targetLang]: aligned.text };
                state.alignedDraft[id] = { lang: targetLang, text: aligned.text };   // see onSave
                delete state.saveStatus[id];
                close();
                renderEditors();
                toast(t("alignDone", targetName), 6000);
            } catch (err) {
                console.error(err);
                setAlignDialogBusy(dialog, false, t("alignFailed"), true);
            }
        },
        onKeep: async (_dialog, close) => {
            close();
            await performSave(id);
        },
    });
}

function escapeHtml(text) {
    return String(text).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
}

/** Opens the summary as a page in the layout of Suva's own exports (judgements/Export_*.html):
 *  title, "Text DE", "Text FR", "Link zum Urteil". Built in the browser from the editor's texts. */
function openSuvaExport(id) {
    const entry = state.summaries[id];
    const texts = currentTexts(id);
    const item = findItem(id);
    const title = entry.title?.[item?.lang] || entry.title?.de || `${I18N.de.exportTitle} ${entry.caseNumber}`;
    const url = entry.url && entry.url !== "#" ? entry.url : "";
    const paragraphs = (text) => (text || "").split(/\n\s*\n/).filter((p) => p.trim())
        .map((p) => `<p>${escapeHtml(p.trim()).replace(/\n/g, "<br>")}</p>`).join("");
    const html = `<!DOCTYPE html><html lang="de"><head><meta charset="UTF-8"><title>${escapeHtml(title)}</title></head>`
        + `<body><hr><h1>${escapeHtml(title)}</h1>`
        + `<h2>Text DE</h2>${paragraphs(texts.de)}<h2>Text FR</h2>${paragraphs(texts.fr)}`
        + (url ? `<h2>Link zum Urteil</h2><p><a href="${escapeHtml(url)}">${escapeHtml(url)}</a></p>` : "")
        + "<hr></body></html>";
    const blobUrl = URL.createObjectURL(new Blob([html], { type: "text/html;charset=utf-8" }));
    window.open(blobUrl, "_blank", "noopener");
    setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
}

/* ---------- database ---------- */

let kbRequest = 0;

async function refreshKb() {
    const requestId = ++kbRequest;
    try {
        const entries = await searchKnowledgeBase(state.kbQuery, currentLang);
        if (requestId !== kbRequest) return;   // a newer query is in flight
        state.kbEntries = entries;
        state.kbError = false;
        for (const entry of entries) state.summaries[entry.id] = entry;
    } catch (err) {
        console.error(err);
        if (requestId !== kbRequest) return;
        state.kbEntries = [];
        state.kbError = true;
    }
    renderKb();
}

let kbRefreshTimer;

/** Several summaries may finish shortly after each other: refresh the database view once. */
function scheduleKbRefresh() {
    clearTimeout(kbRefreshTimer);
    kbRefreshTimer = setTimeout(refreshKb, 400);
}

/** Mock only (development): wipes the stored summaries and the relevance feedback. */
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
    state.summaryQueue = [];
    state.drafts = {};
    state.saveStatus = {};
    for (const item of state.items) {
        item.summarized = false;
        item.adjustment = 0;
        item.userLevel = null;
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
        el("span", { class: "pnum", text: String(score) }),
        // manual correction: show by how much the score was moved
        Boolean(item.adjustment) && el("span", {
            class: "sub delta", text: (item.adjustment > 0 ? "+" : "−") + Math.abs(item.adjustment),
        }));
}

function statusTags(item) {
    const status = summaryStatus(item);
    return [
        status === "loading" && el("span", { class: "tag blue", text: t("tagSummarizing") }),
        status === "queued" && el("span", { class: "tag", text: t("tagQueued") }),
        status === "error" && el("span", { class: "tag bad", text: t("tagFailed") }),
        item.summarized && el("span", { class: "tag ok", text: t("tagInDb") }),
        Boolean(decidedLevel(item)) && el("span", {
            class: "tag",
            text: t(item.adjustment ? "tagCorrected" : "tagConfirmed") + (item.feedbackLocalOnly ? t("localSuffix") : ""),
        }),
    ];
}

function renderRow(item) {
    return el("tr", {
        tabindex: "0",
        "data-id": item.id,
        "aria-selected": String(item.id === state.selectedId),
        onclick: () => selectCase(item.id),
        onkeydown: (e) => { if (e.key === "Enter" && e.target === e.currentTarget) selectCase(item.id); },
    },
        el("td", { class: "col-case nr" }, item.caseNumber, el("div", { class: "sub", text: (item.lang || "").toUpperCase() })),
        el("td", { class: "col-date pnum", text: item.date ? formatDate(item.date) : "" }),
        el("td", { class: "col-area" }, areaLabel(item),
            el("div", { class: "sub", text: subjectDetail(item) || outcomeName(item.outcome) }),
            el("div", { class: "row inline-tags" }, statusTags(item))),   // shown when the status column is hidden
        el("td", { class: "col-score" }, relevanceScale(item)),
        el("td", { class: "col-status" }, el("div", { class: "row" }, statusTags(item))));
}

/** Re-renders one row (e.g. when its summary arrives) without rebuilding the whole list. */
function refreshRow(id) {
    const row = document.querySelector(`#results-rows tr[data-id="${CSS.escape(id)}"]`);
    const item = findItem(id);
    if (!row || !item) return;
    const hadFocus = document.activeElement === row;
    const fresh = renderRow(item);
    row.replaceWith(fresh);
    if (hadFocus) fresh.focus({ preventScroll: true });
}

function renderHead() {
    const { key, dir } = state.sort;
    $("#results-head").replaceChildren(
        ...COLUMNS.map((col) => {
            if (!col.key) return el("th", { class: col.cls, text: t(col.label) });
            const sorted = col.key === key;
            return el("th", { class: col.cls, "aria-sort": sorted && (dir > 0 ? "ascending" : "descending") },
                el("button", {
                    type: "button", "data-key": col.key,
                    text: t(col.label) + (sorted ? (dir > 0 ? " ▲" : " ▼") : ""),
                    onclick: () => sortBy(col.key),
                }));
        }));
}

/** Level buttons with counts. */
function renderFilterControls() {
    const counts = { high: 0, medium: 0, low: 0 };
    for (const item of state.items) counts[itemLevel(item)] += 1;
    const levels = [...LEVELS.map((level) => [level, t(LEVEL_LABEL[level]), counts[level]]),
        ["all", t("levelAll"), state.items.length]];
    $("#filter-level").replaceChildren(...levels.map(([level, label, count]) => el("button", {
        type: "button", "data-level": level, "aria-pressed": String(state.filter.level === level),
        text: `${label} (${count})`, onclick: () => setLevelFilter(level),
    })));
}

function renderList() {
    renderFilterControls();
    const visible = visibleItems();
    const total = state.items.length;
    const searching = state.searching;

    $("#results-rows").replaceChildren(...visible.map(renderRow));
    renderHead();
    $("#results-table").hidden = searching || visible.length === 0;
    $("#filter-bar").hidden = searching || total === 0;
    $("#results-count").textContent = isFilterActive()
        ? t("resultsCountFiltered", visible.length, total)
        : t("resultsCount", total);

    const status = $("#results-status");
    if (searching && state.searchProgress) {
        const p = state.searchProgress;
        const pct = p.total > 0 ? Math.min(100, Math.round((p.completed / p.total) * 100)) : 0;
        status.hidden = false;
        status.replaceChildren(
            el("div", { class: "period-progress" },
                el("p", { class: "loading-line" }, spinner(), t("periodLoading", p.message)),
                el("progress", { max: String(Math.max(1, p.total || 1)), value: String(p.completed || 0) }),
                p.total > 0 && el("span", { class: "sub", text: `${p.completed || 0} / ${p.total} (${pct} %)` })));
        return;
    }
    let statusKey = null;
    if (searching) statusKey = "searching";
    else if (state.error) statusKey = state.error;
    else if (!state.hasSearched) statusKey = "emptyInitial";
    else if (total === 0) statusKey = "emptyNoResults";
    else if (visible.length === 0) statusKey = "emptyFiltered";
    status.hidden = !statusKey;
    status.textContent = statusKey ? t(statusKey, state.searchErrorArg || "") : "";
}

/* ---------- rendering: summary editor (detail panel and database) ---------- */

function saveStatusText(id) {
    const status = state.saveStatus[id];
    const pending = state.summaries[id].pendingLangs.length > 0;
    if (status === "saving") return t("saving");
    if (status === "approving") return t("approving");
    if (status === "error") return t("errorSave");
    if (isDirty(id)) return t("unsaved");
    if (status === "approved") return `✓ ${t("approved")}`;
    if (pending) return (status === "saved" ? `✓ ${t("saved")} · ` : "") + t("pendingApproval");
    if (status === "saved") return `✓ ${t("saved")}`;
    return "";
}

function updateEditorFooter(editor, id) {
    const busy = state.saveStatus[id] === "saving" || state.saveStatus[id] === "approving";
    const dirty = isDirty(id);
    editor.querySelector(".save-btn").disabled = !dirty || busy;
    const approve = editor.querySelector(".approve-btn");
    approve.hidden = state.summaries[id].pendingLangs.length === 0;
    approve.disabled = dirty || busy;
    const status = editor.querySelector(".save-status");
    status.textContent = saveStatusText(id);
    status.classList.toggle("error", state.saveStatus[id] === "error");
}

/** Two tags above one summary language: the language, and where its text comes from
 *  ("generiert" / "bearbeitet"). The origin is the database's (entry.origin, see
 *  summaryOrigin in api.js): typing does not change it, only a saved edit does. */
function summaryTags(entry, lang, name) {
    const origin = entry.origin?.[lang];
    const pending = entry.pendingLangs.includes(lang);
    const hint = origin === "generated" ? t("originGeneratedHint", entry.model?.[lang] || "?")
        : pending ? t("originPendingHint") : t("originEditedHint");
    return el("h4", { class: "duo-tags" },
        el("span", { class: "tag lang-tag", lang, text: name }),
        origin && el("span", {
            class: `tag origin-tag ${origin}`, title: hint, text: t(`origin_${origin}`),
        }));
}

/** German and French summary next to each other, each editable; one save button for both
 *  (saving an edit of one language offers to regenerate the other, see onSave). `scope` tells the detail panel ("res") from the database ("kb")
 *  so the caret can be restored after a re-render. */
function summaryEditor(scope, id) {
    const entry = state.summaries[id];
    const texts = currentTexts(id);
    const editor = el("div", { class: "summary-editor" });

    const columns = SUMMARY_LANGS.map((lang) => {
        const name = t(lang === "de" ? "langDe" : "langFr");
        const textarea = el("textarea", {
            class: "summary-textarea", lang, spellcheck: "true",
            "aria-label": `${t("summary")} ${name}`,
            "data-focus-key": `${scope}:${id}:${lang}`,
            oninput: (e) => onDraftInput(id, lang, e.target.value, editor),
        });
        textarea.value = texts[lang] || "";   // content must be set as a property
        return el("div", { class: "duo-col" },
            el("div", { class: "duo-head" }, summaryTags(entry, lang, name)),
            textarea);
    });

    editor.append(
        el("div", { class: "duo" }, columns),
        el("div", { class: "row editor-footer" },
            el("button", { type: "button", class: "btn pri save-btn", text: t("save"), onclick: () => onSave(id) }),
            el("button", {
                type: "button", class: "btn approve-btn", text: t("approve"), title: t("approveHint"),
                onclick: () => onApprove(id),
            }),
            el("span", { class: "note save-status", "aria-live": "polite" }),
            el("button", { type: "button", class: "btn export-btn", text: t("openSuvaFormat"), onclick: () => openSuvaExport(id) })));
    updateEditorFooter(editor, id);
    return editor;
}

/** Re-renders every place a summary editor is shown. */
function renderEditors() {
    renderDetail();
    renderKb();
}

/* ---------- rendering: case detail ---------- */

/** "Kriterien und Belege aus dem Urteil": always there, folded by clicking its title.
 *  Shows the criteria with their quotes; while the backend delivers none, the signals
 *  triage found, and says that the quotes are missing. */
function criteriaSection(item) {
    const count = item.criteria.length;
    const signals = [...item.signals, ...item.topics];
    const body = el("div", { class: "crit-body", hidden: !state.criteriaOpen },
        count > 0 && el("ul", { class: "crit" }, item.criteria.map((c) => {
            const status = QUOTE_STATUS[c.status];
            return el("li", {},
                el("strong", { text: `${c.id} ` }), criterionName(c.id), " ",
                status && el("span", { class: `tag ${status.tag}`, text: t(status.key) }),
                el("blockquote", { lang: item.lang }, `«${c.quote}»`, el("div", { class: "sub", text: formatRef(c.at) })));
        })),
        count === 0 && signals.length > 0 && el("p", { class: "signals" },
            el("span", { class: "sub", text: `${t("signals")}: ` }),
            signals.map((s) => [el("span", { class: "tag", text: s }), " "])),
        count === 0 && el("p", { class: "note", text: t("criteriaMissing") }));
    const toggle = el("button", {
        type: "button", class: "toggle", "aria-expanded": String(state.criteriaOpen),
        onclick: () => {
            state.criteriaOpen = !state.criteriaOpen;
            body.hidden = !state.criteriaOpen;
            toggle.setAttribute("aria-expanded", String(state.criteriaOpen));
        },
    }, t("criteria"), count > 0 && el("span", { class: "sub", text: `(${count})` }));
    return [el("h3", {}, toggle), body];
}

function summarizeButton(id, labelKey) {
    return el("div", { class: "row editor-footer" },
        el("button", { type: "button", class: "btn pri", text: t(labelKey), onclick: () => requestSummary(id, { first: true }) }));
}

function summaryBasisSection(entry) {
    const facts = entry?.facts;
    const laws = Array.isArray(facts?.gesetzesartikel) ? facts.gesetzesartikel.filter(Boolean) : [];
    const points = Array.isArray(facts?.kernerwaegungen)
        ? facts.kernerwaegungen.filter((k) => k && (k.aussage || k.erwaegung))
        : [];
    const examples = Array.isArray(entry?.examples) ? entry.examples.filter(Boolean) : [];
    if (!laws.length && !points.length && !examples.length) return null;
    return el("details", { class: "summary-basis" },
        el("summary", { text: t("summaryBasis") }),
        laws.length > 0 && el("p", { class: "signals" },
            el("span", { class: "sub", text: `${t("summaryBasisLaws")}: ` }),
            laws.map((law) => [el("span", { class: "tag", text: law }), " "])),
        points.length > 0 && el("div", {},
            el("p", { class: "sub", text: t("summaryBasisConsiderations") }),
            el("ul", { class: "basis-list" }, points.map((p) => el("li", {},
                p.erwaegung && el("strong", { text: `${t("refPrefix")}${p.erwaegung}: ` }),
                p.aussage || "")))),
        examples.length > 0 && el("p", { class: "signals" },
            el("span", { class: "sub", text: `${t("summaryBasisExamples")}: ` }),
            examples.map((ex) => [el("span", { class: "tag", text: ex }), " "])));
}

function summarySection(item) {
    const id = item.id;
    const heading = el("h3", { text: t("summary") });
    const busy = (key) => el("p", { class: "note loading-line" }, spinner(), t(key));
    switch (summaryStatus(item)) {
        case "ready": return [heading, summaryEditor("res", id), summaryBasisSection(state.summaries[id])].filter(Boolean);
        case "loading": return [heading, busy("summarizing")];
        case "queued": return [heading, busy("summaryQueued")];
        case "error": return [heading, el("p", { class: "form-error", text: t("errorSummary") }), summarizeButton(id, "retry")];
        case "missing": return [heading, el("p", { class: "note", text: t("noSummaryYet") }), summarizeButton(id, "summarize")];
        default:   // "manual": below the threshold, summarized only when asked
            return [
                heading,
                el("p", { class: "note", text: t("notAutoSummarized", SUMMARY_THRESHOLD, effectiveScore(item)) }),
                summarizeButton(id, "summarize"),
            ];
    }
}

/** Case number of a similar case; a link if that case is in the current result list. */
function similarCaseLink(caseNumber) {
    const target = state.items.find((i) => i.caseNumber === caseNumber);
    if (!target) return caseNumber;
    return el("a", { href: "#", text: caseNumber, onclick: (e) => { e.preventDefault(); selectCase(target.id); } });
}

function similarSection(item) {
    if (!item.similarCases.length) return null;
    return [
        el("h3", { text: t("similarCases") }),
        el("table", { class: "similar" }, el("tbody", {}, item.similarCases.map((c) => el("tr", {},
            el("td", { class: "nr" }, similarCaseLink(c.caseNumber)),
            el("td", {}, c.subject || "", c.suvaSelected && [" ", el("span", { class: "tag ok", text: t("suvaSelected") })]),
            el("td", { class: "pnum", text: t("similarity", c.similarity.toFixed(2)) }))))),
    ];
}

function correctionSection(item) {
    const decided = decidedLevel(item);
    const button = (target, label, extraClass) => el("button", {
        type: "button", class: `btn ${extraClass}`, text: t(label),
        "aria-pressed": String(decided === target),
        onclick: () => setRelevance(item.id, target),
    });
    return [
        el("h3", { text: t("correction") }),
        el("p", { class: "note", text: t("correctionNote") }),
        item.feedbackLocalOnly && el("p", { class: "note warn-note", text: t("feedbackLocalOnly") }),
        el("div", { class: "row editor-footer" },
            button("high", "markRelevant", "relevant"),
            button("low", "markNotRelevant", "")),
    ];
}

/** Section with a text from the backend; in the French UI it says so if only German exists. */
function textSection(titleKey, field) {
    const text = loc(field);
    if (!text) return null;
    const germanOnly = currentLang === "fr" && (!field.fr || field.fr === field.de);
    return [
        el("h3", { text: t(titleKey) }),
        germanOnly && el("p", { class: "note", text: t("germanOnly") }),
        el("p", { class: "read", lang: germanOnly ? "de" : currentLang, text }),
    ];
}

const frReasonLoading = new Set();
const frReasonAttempted = new Set();

async function ensureFrenchReason(item) {
    if (USE_MOCK || currentLang !== "fr" || !item || frReasonLoading.has(item.id) || frReasonAttempted.has(item.id)) {
        return;
    }
    const exp = item.explanation;
    if (!exp?.de || (exp.fr && exp.fr !== exp.de)) return;
    frReasonLoading.add(item.id);
    let updated = false;
    try {
        const res = await translateReasonFr(item.id);
        if (res?.explanation?.fr) { item.explanation = { ...item.explanation, fr: res.explanation.fr }; updated = true; }
        if (res?.question?.fr && item.question) { item.question = { ...item.question, fr: res.question.fr }; updated = true; }
        if (res?.rule?.fr && item.rule) { item.rule = { ...item.rule, fr: res.rule.fr }; updated = true; }
        frReasonAttempted.add(item.id);
    } catch (err) {
        console.warn("FR translation failed", err);
    } finally {
        frReasonLoading.delete(item.id);
        if (updated && state.selectedId === item.id) renderDetail();
    }
}

/** Divider between list and detail (splitter.js); created in init. */
let detailSplitter = null;

function renderDetail() {
    const item = findItem(state.selectedId);
    $("#split").classList.toggle("has-detail", Boolean(item));
    $("#detail-pane").hidden = !item;
    $("#split-handle").hidden = !item;
    const detail = $("#detail");
    if (!item) {
        detail.replaceChildren();
        detailSplitter?.reset();   // the next case opens at the default width again
        return;
    }
    detailSplitter?.refresh();
    ensureFrenchReason(item);

    const meta = [
        item.date && formatDate(item.date), loc(item.court), areaLabel(item), subjectDetail(item), outcomeName(item.outcome),
    ];
    const badges = [
        item.singleJudge === true && el("span", { class: "tag", text: t("tagSingleJudge") }),
        typeof item.principle === "number" && el("span", { class: "tag blue", text: t("principleLabel", item.principle) }),
        typeof item.uvUse === "number" && el("span", { class: "tag blue", text: t("uvUseLabel", item.uvUse) }),
    ].filter(Boolean);
    const url = item.url && (currentLang === "fr" ? item.url.replace("lang=de", "lang=fr") : item.url);
    preservingFocus(detail, () => detail.replaceChildren(...[
        el("button", {
            type: "button", class: "detail-close", "aria-label": t("close"), title: t("close"), onclick: closeDetail,
        }, closeIcon()),
        // as in the demo: the title of the summary once there is one
        el("h2", { text: loc(state.summaries[item.id]?.title) || `${t("judgement")} ${item.caseNumber}` }),
        el("p", { class: "meta" }, meta.filter(Boolean).join(", "), ". ",
            url && el("a", { href: url, target: "_blank", rel: "noopener", text: t("openOriginal") })),
        badges.length > 0 && el("div", { class: "row detail-badges" }, badges),
        textSection("question", item.question),
        // the explanation is triage's reasoning, so the heading follows triage's own score
        textSection(scoreLevel(item.score) === "high" ? "whyRelevant" : "whyNotRelevant", item.explanation),
        textSection("rule", item.rule),
        criteriaSection(item),
        summarySection(item),
        similarSection(item),
        correctionSection(item),
    ].flat(Infinity).filter(Boolean)));
}

/* ---------- rendering: database ---------- */

/** Title of a database entry: area and subject if known (from the entry or the current
 *  search), otherwise the title the summary was stored with. */
function kbTitle(entry) {
    const source = entry.subject ? entry : findItem(entry.id);
    if (source?.subject) return [areaLabel(source), subjectDetail(source)].filter(Boolean).join(" – ");
    return loc(entry.title);
}

function renderKbEntry(entry) {
    return el("li", { class: "kb-entry", "data-id": entry.id },
        el("details", {},
            el("summary", {},
                el("span", { class: "nr", text: entry.caseNumber }),
                el("span", { class: "kb-entry-title", text: kbTitle(entry) }),
                entry.pendingLangs.length > 0 && el("span", { class: "tag warn", text: t("tagPending") }),
                el("span", { class: "sub", text: entry.storedAt ? `${t("storedAt")} ${formatDate(entry.storedAt.slice(0, 10))}` : "" })),
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
        const node = renderKbEntry(state.summaries[entry.id] || entry);
        if (openIds.has(entry.id)) node.querySelector("details").open = true;
        return node;
    })));

    const n = state.kbEntries.length;
    const tabCount = $("#kb-tab-count");
    tabCount.hidden = !n || Boolean(state.kbQuery);
    tabCount.textContent = n ? String(n) : "";

    $("#kb-count").textContent = n ? t("kbCount", n) : "";
    const statusKey = state.kbError ? "errorKb" : n ? null : (state.kbQuery ? "kbNoMatch" : "kbEmpty");
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
    renderQuickRangeButton();
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
    $("#mock-badge").hidden = !USE_MOCK;
    detailSplitter = createSplitter($("#split"), $("#split-handle"), $("#detail-pane"));

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
    $("#quick-range-btn").addEventListener("click", () => {
        if (!availableRange) return;
        setDateInput(fromInput, availableRange.from);
        setDateInput(toInput, availableRange.to);
        $("#search-form").requestSubmit();
    });
    for (const id of ["#filter-text", "#filter-min", "#filter-max"]) {
        $(id).addEventListener("input", onFilterChange);
    }
    $("#filter-reset").addEventListener("click", resetFilters);
    $("#kb-clear").addEventListener("click", onClearDb);
    $("#kb-clear").hidden = !USE_MOCK;   // the real database keeps every version (append-only)

    let kbTimer;
    $("#kb-search").addEventListener("input", (e) => {
        clearTimeout(kbTimer);
        kbTimer = setTimeout(() => {
            state.kbQuery = e.target.value.trim();
            refreshKb();
        }, 200);
    });

    // Escape closes the detail panel (unless a dialog/calendar is open or the user is typing)
    document.addEventListener("keydown", (e) => {
        if (e.key !== "Escape" || !state.selectedId || openPickers.size || $("#align-dialog")?.open) return;
        if (e.target.closest("input, textarea, select")) return;
        closeDetail();
    });

    thresholdsReady = loadThresholds();
    renderLanguage();
    refreshKb();
}

init();
