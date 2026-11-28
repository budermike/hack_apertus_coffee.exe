/* script.js — UI state and rendering.
 * Depends on i18n.js, datepicker.js, mock.js and api.js (loaded before). */

/** From this relevance on a judgement counts as relevant and is summarized automatically.
 *  Defaults as in configs/triage.json; replaced by the backend's values (GET /api/config)
 *  before the first search. */
let SUMMARY_THRESHOLD = 60;
let MEDIUM_THRESHOLD = 40;
/** Why the thresholds sit where they do ({ de: [...], fr: [...] } from GET /api/config); null = not known. */
let THRESHOLD_BASIS = null;
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
    // relevance before the wide subject column: stays in view next to an open case
    { key: "score", label: "colRelevance", cls: "col-score" },
    { key: "area", label: "colArea", cls: "col-area" },
    { key: null, label: "colStatus", cls: "col-status" },
];

const state = {
    items: [],                 // judgements from the last search
    hasSearched: false,
    lastRange: null,           // { from, to } of the last search, to fetch the scores again
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
    saveStatus: {},            // id -> "saving" | "aligning" | "saved" | "resetting" | "reset"
                               //       | "error" | "resetError"

    kbEntries: [],
    kbError: false,            // the database could not be read
    kbQuery: "",
    activeTab: ["search", "kb"].includes(localStorage.getItem("tab"))
        ? localStorage.getItem("tab")
        : "search",
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

/** Displayed score: the relevance the backend delivers, 0..100, one decimal at most.
 *  Nothing is added by hand any more (28.11.): a lawyer's decision is a training example on
 *  the server (triage/feedback_learning.py) and comes back in `score` of every judgement it
 *  says something about; `baseScore` is the value without the decisions.
 *  This is the ONE number that is shown, filtered, sorted and compared with the thresholds. */
function effectiveScore(item) {
    let score = Math.max(0, Math.min(100, item.score));
    // the lawyer's own decision is final for this judgement (same rule as the backend,
    // feedback_learning.apply_decision): relevant = at least green, not relevant = red
    if (item.userLevel === "high") score = Math.max(score, SUMMARY_THRESHOLD);
    else if (item.userLevel === "low") score = Math.min(score, Math.round((MEDIUM_THRESHOLD - 0.1) * 10) / 10);
    return Math.round(score * 10) / 10;
}

/** Score before any decision: the stored relevance. */
function baseScoreOf(item) {
    return Number.isFinite(item.baseScore) ? item.baseScore : item.score;
}

/** By how much the lawyers' decisions moved this judgement's score (0 = not at all). */
function learnedDelta(item) {
    return Math.round((effectiveScore(item) - baseScoreOf(item)) * 10) / 10;
}

function scoreLevel(score) {
    if (score >= SUMMARY_THRESHOLD) return "high";
    if (score >= MEDIUM_THRESHOLD) return "medium";
    return "low";
}

/** Level of a judgement: the lawyer's own decision if there is one, otherwise by its score. */
const itemLevel = (item) => item.userLevel || scoreLevel(effectiveScore(item));

/** Level the user decided by hand ("high" | "low"), or null if the judgement is as triage scored it. */
function decidedLevel(item) {
    return item.userLevel || null;
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

/** Takes the thresholds (and default cached date span) from the backend; keeps defaults if it does not answer. */
async function loadThresholds() {
    const config = await fetchConfig();
    const high = toNumber(config?.thresholds?.high, NaN);
    const medium = toNumber(config?.thresholds?.medium, NaN);
    if (Number.isFinite(high) && Number.isFinite(medium) && medium <= high) {
        SUMMARY_THRESHOLD = high;
        MEDIUM_THRESHOLD = medium;
    }
    if (config?.availableRange?.from && config?.availableRange?.to && !state.hasSearched && !state.searching) {
        const fromInput = $("#date-from");
        const toInput = $("#date-to");
        if (fromInput && toInput) {
            setDateInput(fromInput, config.availableRange.from);
            setDateInput(toInput, config.availableRange.to);
        }
    }
    if (config?.thresholdBasis && typeof config.thresholdBasis === "object") THRESHOLD_BASIS = config.thresholdBasis;
}

/** Sentences that explain the thresholds in the current language; empty when the backend sent none. */
function thresholdBasisText() {
    const basis = THRESHOLD_BASIS?.[currentLang] || THRESHOLD_BASIS?.de;
    return Array.isArray(basis) ? basis.filter((line) => typeof line === "string") : [];
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
    const todayIso = toIso(new Date());
    if (to > todayIso) {
        toInput.classList.add("invalid");
        return { error: "errFuture" };
    }
    if (from >= todayIso) {
        fromInput.classList.add("invalid");
        return { error: "errToday" };
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
    if (range.error) return;

    const searchGen = ++periodSearchGeneration;
    const button = $("#search-btn");
    button.classList.add("loading");
    state.searching = true;
    state.searchProgress = null;
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
                await new Promise((resolve) => setTimeout(resolve, 1000));
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
                state.searchErrorArg = period;   // worded in the UI language (i18n periodError)
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
        if (searchGen !== periodSearchGeneration) return;
        console.error(err);
        state.items = [];
        state.error = "errorSearch";
        state.searchErrorArg = null;
    }
    if (searchGen !== periodSearchGeneration) return;
    state.hasSearched = true;
    state.lastRange = { from: range.from, to: range.to };
    state.searching = false;
    state.searchProgress = null;
    frReasonAttempted.clear();
    button.classList.remove("loading");
    renderResults();

    if (USE_MOCK) {
        queueRelevantSummaries();
    }
    localizeListSubjects();
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
    if (!item || state.summaries[id]) return;
    if (item.summarized) {
        fetchStoredIntoState(id);
        return;
    }
    const status = summaryStatus(item);
    if (status === "queued" || status === "missing") {
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

/** After a decision the backend has learned from it: fetch the scores again, because other
 *  judgements may have moved too, and keep everything else of the list as it is.
 *  Returns how many scores changed. */
async function refreshScores() {
    if (USE_MOCK || !state.lastRange) return 0;   // the demo backend does not learn
    try {
        const fresh = await searchJudgements(state.lastRange.from, state.lastRange.to, currentLang);
        const byId = new Map(fresh.map((item) => [item.id, item]));
        let moved = 0;
        for (const item of state.items) {
            const next = byId.get(item.id);
            if (!next) continue;
            if (next.score !== item.score) moved += 1;
            item.score = next.score;
            item.baseScore = next.baseScore;
        }
        return moved;
    } catch (err) {
        console.warn("scores not refreshed after the decision", err);
        return 0;
    }
}

/** "Für Suva relevant" / "Nicht relevant": the user's own decision for this judgement.
 *  It puts the judgement into the level `high` / `low` (or confirms the level it has);
 *  clicking the active decision again takes it back. Every click is sent as feedback and
 *  the backend learns from it: no fixed number is added to the score. */
async function setRelevance(id, level) {
    const item = findItem(id);
    if (!item) return;
    const before = itemLevel(item);
    item.userLevel = decidedLevel(item) === level ? null : level;

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

    const { stored } = await sendFeedback(id, 0, direction, after, item.userLevel);
    item.feedbackLocalOnly = !stored && Boolean(item.userLevel);
    const moved = stored ? await refreshScores() : 0;
    renderList();
    if (id === state.selectedId) renderDetail();
    if (moved) queueRelevantSummaries();   // learning may have lifted judgements over the threshold
    // a judgement Apertus did not score has nothing the system could learn from: say so
    const notLearnable = stored && Boolean(item.userLevel) && item.scored === false;
    const message = !stored ? t("feedbackLocalOnly")
        : notLearnable ? t("feedbackNotLearnable")
        : moved ? t("feedbackLearned", moved) : t("feedbackSaved");
    toast(message, stored && !moved && !notLearnable ? 4000 : 8000);
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

/* ---------- editing, saving and resetting summaries ---------- */

/** Text currently in the editor: the draft if there is one, otherwise the stored summary. */
function currentTexts(id) {
    return state.drafts[id] || state.summaries[id];
}

/** The database holds an edited version of this summary (typed by a lawyer, or brought in
 *  line by Apertus), or an edit from before 28.11. that never got approved. */
function hasStoredEdit(id) {
    const stored = state.summaries[id];
    return stored.pendingLangs.length > 0
        || SUMMARY_LANGS.some((lang) => stored.origin[lang] && stored.origin[lang] !== "generated");
}

/** Something to save: the user typed, or an edit saved before the approval step was dropped
 *  (28.11.) still waits in the database. "Speichern" takes that one over as well. */
function isDirty(id) {
    const draft = state.drafts[id];
    const stored = state.summaries[id];
    if (!stored) return false;
    if (stored.pendingLangs.length > 0) return true;
    return Boolean(draft && (draft.de !== stored.de || draft.fr !== stored.fr));
}

const canReset = (id) => isDirty(id) || hasStoredEdit(id);
const isSaveBusy = (id) => ["saving", "aligning", "resetting"].includes(state.saveStatus[id]);

function onDraftInput(id, lang, value, editor) {
    const texts = currentTexts(id);
    state.drafts[id] = { de: texts.de, fr: texts.fr, [lang]: value };
    delete state.saveStatus[id];
    updateEditorFooter(editor, id);   // no full re-render while typing
}

/** Saves the editor's texts. They are shown at once: there is no approval step, and the
 *  database keeps one edited version per language next to the generated one.
 *  If only one language was changed, the backend lets Apertus bring the other one in line
 *  (never a text a lawyer typed); `entry.alignment` says what came of it. */
/** Popup on "Speichern": regenerate the other language? Resolves to "apply", "keep" or
 *  "cancel" (Escape and "Abbrechen"). The dialog closes at once; the save runs afterwards,
 *  shown in the editor's status line. */
function askAlign(source, target) {
    const dialog = $("#align-dialog");
    $("#align-title").textContent = t("alignPromptTitle", target);
    $("#align-desc").textContent = t("alignPromptBody", source, target);
    dialog.returnValue = "";
    dialog.showModal();
    $("#align-apply").focus();
    return new Promise((resolve) => {
        dialog.addEventListener("close", () => resolve(dialog.returnValue || "cancel"), { once: true });
    });
}

/** "Speichern". If exactly one language was changed and Apertus may rewrite the other one
 *  (generated or "angeglichen", not typed by hand), the popup asks first: "neu erstellen"
 *  saves and lets Apertus regenerate the other language, "nur speichern" saves the edit
 *  alone (align: false), "Abbrechen" saves nothing. A language typed by hand is never
 *  rewritten by Apertus (backend rule), so then there is nothing to ask. */
async function onSave(id) {
    if (!isDirty(id) || isSaveBusy(id) || $("#align-dialog").open) return;
    const stored = state.summaries[id];
    const texts = currentTexts(id);
    const sent = { de: texts.de, fr: texts.fr };
    const changed = SUMMARY_LANGS.filter((lang) => sent[lang] !== stored[lang] || stored.pendingLangs.includes(lang));
    const other = changed.length === 1 ? SUMMARY_LANGS.find((lang) => lang !== changed[0]) : null;
    let align = true;
    if (other && ["generated", "aligned"].includes(stored.origin[other])) {
        const answer = await askAlign(changed[0], other);
        if (answer === "cancel") return;
        align = answer === "apply";
    }
    state.saveStatus[id] = align && other && stored.origin[other] !== "edited" ? "aligning" : "saving";
    renderEditors();
    try {
        const entry = await saveSummary(id, sent, { align });
        state.summaries[id] = entry;
        // Keep only what the user typed on while saving; everything else follows the saved
        // entry (Apertus may have rewritten the other language).
        const draft = state.drafts[id];
        if (draft) {
            const merged = Object.fromEntries(
                SUMMARY_LANGS.map((lang) => [lang, draft[lang] === sent[lang] ? entry[lang] : draft[lang]]));
            if (merged.de === entry.de && merged.fr === entry.fr) delete state.drafts[id];
            else state.drafts[id] = merged;
        }
        state.saveStatus[id] = "saved";
        const alignment = entry.alignment;
        if (alignment?.ok) toast(t("alignedToast", alignment.lang), 8000);
        else if (alignment?.reason === "edited_by_hand") toast(t("alignSkippedToast", alignment.lang), 8000);
        else if (alignment) toast(t("alignFailedToast", alignment.lang), 8000);
    } catch (err) {
        console.error(err);
        state.saveStatus[id] = "error";
    }
    renderEditors();
    refreshKb();
}

/** Drops the edited version of both languages, and what is typed but not saved: the text
 *  Apertus generated is shown again. That one is never deleted, so this always works. */
async function onReset(id) {
    if (!canReset(id) || isSaveBusy(id)) return;
    if (!confirm(t("resetConfirm"))) return;
    if (!hasStoredEdit(id)) {        // only typed, nothing saved: nothing to ask the backend
        delete state.drafts[id];
        delete state.saveStatus[id];
        renderEditors();
        return;
    }
    state.saveStatus[id] = "resetting";
    renderEditors();
    try {
        state.summaries[id] = await resetSummary(id);
        delete state.drafts[id];
        state.saveStatus[id] = "reset";
    } catch (err) {
        console.error(err);
        state.saveStatus[id] = "resetError";
    }
    renderEditors();
    refreshKb();
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
        el("div", { class: `scale ${scoreLevel(score)}`, title: [t("scaleTitle", score, SUMMARY_THRESHOLD), ...thresholdBasisText()].join("\n\n") },
            el("i", { style: `left:${SUMMARY_THRESHOLD}%` }),
            el("b", { style: `left:${score}%` })),
        el("span", { class: "pnum", text: String(score) }),
        // learned from the lawyers' decisions: show by how much that moved the score
        Boolean(learnedDelta(item)) && el("span", {
            class: "sub delta", title: t(decidedLevel(item) ? "decidedDeltaHint" : "learnedDeltaHint", baseScoreOf(item)),
            text: (learnedDelta(item) > 0 ? "+" : "−") + Math.abs(learnedDelta(item)),
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
            // "korrigiert": the decision differs from what the score says; "bestätigt": it agrees
            text: t(scoreLevel(baseScoreOf(item)) === item.userLevel ? "tagConfirmed" : "tagCorrected")
                + (item.feedbackLocalOnly ? t("localSuffix") : ""),
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
        el("td", { class: "col-score" }, relevanceScale(item)),
        el("td", { class: "col-area" }, areaLabel(item),
            el("div", { class: "sub", text: subjectDetail(item) || outcomeName(item.outcome) }),
            el("div", { class: "row inline-tags" }, statusTags(item))),   // shown when the status column is hidden
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
                el("p", { class: "loading-line" }, spinner(), t("periodLoading", p)),
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
    if (status === "saving") return t("saving");
    if (status === "aligning") return t("savingAligning");
    if (status === "resetting") return t("resetting");
    if (status === "error") return t("errorSave");
    if (status === "resetError") return t("errorReset");
    if (!state.drafts[id] && state.summaries[id].pendingLangs.length > 0) return t("pendingLegacy");
    if (isDirty(id)) return t("unsaved");
    if (status === "saved") return `✓ ${t("saved")}`;
    if (status === "reset") return `✓ ${t("resetDone")}`;
    return "";
}

function updateEditorFooter(editor, id) {
    const busy = isSaveBusy(id);
    editor.querySelector(".save-btn").disabled = !isDirty(id) || busy;
    editor.querySelector(".reset-btn").disabled = !canReset(id) || busy;
    const status = editor.querySelector(".save-status");
    status.textContent = saveStatusText(id);
    status.classList.toggle("error", ["error", "resetError"].includes(state.saveStatus[id]));
}

/** Two tags above one summary language: the language, and where its text comes from:
 *  "generiert" (Apertus), "bearbeitet" (typed by a lawyer) or "angeglichen" (written by
 *  Apertus to follow the lawyer's edit of the other language). The origin is the database's
 *  (entry.origin, see summaryOrigin in api.js): typing does not change it, only saving does. */
function summaryTags(entry, lang, name) {
    const origin = entry.origin?.[lang];
    const hint = origin === "generated" ? t("originGeneratedHint", entry.model?.[lang] || "?")
        : origin === "aligned" ? t("originAlignedHint", entry.alignedBy?.[lang] || "Apertus")
        : t("originEditedHint");
    return el("h4", { class: "duo-tags" },
        el("span", { class: "tag lang-tag", lang, text: name }),
        origin && el("span", {
            class: `tag origin-tag ${origin}`, title: hint, text: t(`origin_${origin}`),
        }));
}

/** German and French summary next to each other, each editable; one "Speichern" and one
 *  "Zurücksetzen" for both. `scope` tells the detail panel ("res") from the database ("kb")
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
                type: "button", class: "btn reset-btn", text: t("resetSummary"), title: t("resetSummaryHint"),
                onclick: () => onReset(id),
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
        count > 0 && el("ul", { class: "crit" }, item.criteria.map((c, index) => {
            const status = QUOTE_STATUS[c.status];
            // The court's wording exists in the language of the judgement only. In the other
            // UI language Apertus' rendering is shown and marked; the original stays one click away.
            const rendered = item.lang !== currentLang ? item.localized?.[currentLang]?.quotes?.[index] : null;
            return el("li", {},
                el("strong", { text: `${c.id} ` }), criterionName(c.id), " ",
                status && el("span", { class: `tag ${status.tag}`, text: t(status.key) }),
                el("blockquote", { lang: rendered ? currentLang : item.lang }, `«${rendered || c.quote}»`,
                    rendered && el("div", { class: "sub rendered-note", text: t("quoteRendered", item.lang) }),
                    el("div", { class: "sub", text: formatRef(c.at) }),
                    rendered && el("details", { class: "quote-original" },
                        el("summary", { text: t("quoteOriginal") }),
                        el("p", { lang: item.lang, text: `«${c.quote}»` }))));
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

/** Points where DE and FR still disagree after the automatic correction (summary/crosscheck.py). */
function langConflictNotice(entry) {
    const issues = entry?.issues?.[currentLang] || entry?.issues?.de || [];
    const conflicts = Array.isArray(issues)
        ? issues.filter((i) => i && (i.kind === "de_fr_conflict" || i.kind === "length_mismatch") && i.detail) : [];
    if (!conflicts.length) return null;
    return el("div", { class: "warn-note", role: "note" },
        el("strong", { text: t("langConflictTitle") }),
        el("ul", { class: "basis-list" }, conflicts.map((i) => el("li", { text: i.detail }))));
}

function summaryBasisSection(entry) {
    const facts = entry?.facts;
    // the facts are stored in German; `basis` holds Apertus' rendering in the other language
    const rendered = currentLang !== "de" ? entry?.basis?.[currentLang] : null;
    const laws = rendered?.laws?.length
        ? rendered.laws
        : (Array.isArray(facts?.gesetzesartikel) ? facts.gesetzesartikel.filter(Boolean) : []);
    const points = rendered?.considerations?.length
        ? rendered.considerations
        : (Array.isArray(facts?.kernerwaegungen) ? facts.kernerwaegungen.filter((k) => k && (k.aussage || k.erwaegung)) : []);
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
        case "ready": return [heading, langConflictNotice(state.summaries[id]), summaryEditor("res", id), summaryBasisSection(state.summaries[id])].filter(Boolean);
        case "loading": return [heading, busy("summarizing")];
        case "queued": return [heading, busy("summaryQueued")];
        case "error": return [heading, el("p", { class: "form-error", text: t("errorSummary") }), summarizeButton(id, "retry")];
        case "missing": return [heading, el("p", { class: "note", text: t("noSummaryYet") }), summarizeButton(id, "summarize")];
        default:   // "manual": below the threshold, summarized only when asked
            return [
                heading,
                el("p", { class: "note", text: t("notAutoSummarized", SUMMARY_THRESHOLD, effectiveScore(item)) }),
                ...(thresholdBasisText().length > 0 ? [el("p", { class: "note", text: thresholdBasisText().join(" ") })] : []),
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
        el("p", { class: "note", text: t(item.scored === false ? "correctionNoteUnscored" : "correctionNote") }),
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

/* ---------- one language throughout: subjects, quotes, summary basis ---------- */

let subjectRun = 0;

/** Subjects of the list in the language of the UI. The backend delivers the stored ones;
 *  the missing ones are asked for in portions (Apertus), best judgements first, and each
 *  row is updated as soon as its subject is there. Stops when Apertus cannot be reached:
 *  the originals stay. */
async function localizeListSubjects() {
    if (USE_MOCK) return;
    const run = ++subjectRun;
    const lang = currentLang;
    const ids = state.items.filter((item) => item.subjectMissing.includes(lang)).map((item) => item.id);
    for (let start = 0; start < ids.length; start += 40) {
        let answer;
        try {
            answer = await localizeSubjects(lang, ids.slice(start, start + 40));
        } catch (err) {
            console.warn("subjects not carried over", err);
            return;
        }
        if (run !== subjectRun) return;   // a newer search or another language took over
        for (const [id, text] of Object.entries(answer?.subjects || {})) {
            const item = findItem(id);
            if (!item) continue;
            item.subject = { ...item.subject, [lang]: text };
            item.subjectMissing = item.subjectMissing.filter((missing) => missing !== lang);
            refreshRow(id);
        }
        if (state.selectedId && ids.slice(start, start + 40).includes(state.selectedId)) renderDetail();
        if (state.activeTab === "kb") renderKb();
        if (answer?.complete === false) return;
    }
}

const localizeLoading = new Set();
const localizeDone = new Set();

/** Quotes under the criteria and the basis of the summary in the language of the UI, for
 *  the judgement on show. Asked once per judgement, language and state (with or without a
 *  summary); the backend stores every text after the first time. */
async function ensureLocalized(item) {
    if (USE_MOCK || !item) return;
    const lang = currentLang;
    const entry = state.summaries[item.id];
    const wantsQuotes = item.lang !== lang && item.criteria.length > 0 && !item.localized?.[lang];
    const wantsBasis = lang !== "de" && Boolean(entry?.facts) && !entry.basis?.[lang];
    const key = `${item.id}:${lang}:${entry ? "summary" : "list"}`;
    if ((!wantsQuotes && !wantsBasis) || localizeLoading.has(key) || localizeDone.has(key)) return;
    localizeLoading.add(key);
    try {
        const answer = await localizeJudgement(item.id, lang);
        if (!answer) return;
        item.localized = { ...item.localized, [lang]: { quotes: answer.quotes || {} } };
        if (answer.subject) {
            item.subject = { ...item.subject, [lang]: answer.subject };
            item.subjectMissing = item.subjectMissing.filter((missing) => missing !== lang);
            refreshRow(item.id);
        }
        const current = state.summaries[item.id];
        if (current && answer.basis) current.basis = { ...current.basis, [lang]: answer.basis };
        if (answer.complete !== false) localizeDone.add(key);
        if (state.selectedId === item.id) renderDetail();
    } catch (err) {
        console.warn("judgement not carried over", err);
    } finally {
        localizeLoading.delete(key);
    }
}

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
    ensureLocalized(item);

    const meta = [
        item.date && formatDate(item.date), loc(item.court), areaLabel(item), subjectDetail(item), outcomeName(item.outcome),
    ];
    const badges = [
        item.singleJudge === true && el("span", { class: "tag", text: t("tagSingleJudge") }),
        typeof item.principle === "number" && el("span", { class: "tag blue", text: t("principleLabel", item.principle) }),
        typeof item.uvUse === "number" && el("span", { class: "tag blue", text: t("uvUseLabel", item.uvUse) }),
    ].filter(Boolean);
    const url = item.url && (currentLang === "fr" ? item.url.replace("lang=de", "lang=fr") : item.url);
    // the text the crawler stored, served by our own backend: readable without leaving the installation
    const localUrl = USE_MOCK ? "" : originalPageUrl(item.id, currentLang);
    preservingFocus(detail, () => detail.replaceChildren(...[
        el("button", {
            type: "button", class: "detail-close", "aria-label": t("close"), title: t("close"), onclick: closeDetail,
        }, closeIcon()),
        // as in the demo: the title of the summary once there is one
        el("h2", { text: loc(state.summaries[item.id]?.title) || `${t("judgement")} ${item.caseNumber}` }),
        el("p", { class: "meta" }, meta.filter(Boolean).join(", "), ". ",
            localUrl && el("a", { href: localUrl, target: "_blank", rel: "noopener", class: "local-original", text: t("openLocal") }),
            localUrl && url && " · ",
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
    showFormError(formErrorKey);
    renderResults();
    renderKb();
    localizeListSubjects();   // subjects of the list in the language just chosen
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

    // Escape closes the detail panel (unless a calendar is open or the user is typing)
    document.addEventListener("keydown", (e) => {
        if (e.key !== "Escape" || !state.selectedId || openPickers.size || $("#align-dialog").open) return;
        if (e.target.closest("input, textarea, select")) return;
        closeDetail();
    });

    thresholdsReady = loadThresholds();
    renderLanguage();
    refreshKb();
}

init();
