/* script.js — UI state and rendering. Depends on i18n.js and api.js (loaded before). */

const threshold_high_relevance = 70;
const threshold_medium_relevance = 40;

/** Relevance levels, top to bottom as shown in the traffic light. */
const LEVELS = ["high", "medium", "low"];
const LEVEL_LABEL_KEY = { high: "levelHigh", medium: "levelMedium", low: "levelLow" };
/** Score a judgement is moved to when the user picks a level its base score is not in. */
const LEVEL_TARGET_SCORE = { high: 85, medium: 55, low: 20 };

const state = {
    items: [],                 // judgements from the last search (incl. backend `adjustment`)
    hasSearched: false,
    error: null,               // i18n key of a list-level error
    summaries: {},             // id -> { de, fr }
    summaryLoading: new Set(), // ids currently being summarized
    summaryError: new Set(),   // ids whose summary failed
    openPanels: {},            // id -> languages shown in the open panel, e.g. ["de"] or ["de", "fr"]
    openExplanations: new Set(),
    selected: new Set(),       // ids selected for bulk summarize
    filter: { text: "", min: 0, max: 100 },
    drafts: {},                // id -> { de, fr } while the user edits a summary
    saveStatus: {},            // id -> "saving" | "saved" | "error"
    kbEntries: [],
    kbQuery: "",
    kbLang: {},                // kb entry id -> languages shown
    activeTab: localStorage.getItem("tab") === "kb" ? "kb" : "search",
};

/* ---------- helpers ---------- */

const $ = (sel) => document.querySelector(sel);

function el(tag, props = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
        if (key === "class") node.className = value;
        else if (key === "text") node.textContent = value;
        else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
        else if (typeof value === "boolean") node[key] = value;
        else node.setAttribute(key, value);
    }
    node.append(...[].concat(children).filter(Boolean));
    return node;
}

/** Displayed score: base score + net manual adjustment (from the traffic light), clamped to 0..100. */
function effectiveScore(item) {
    return Math.max(0, Math.min(100, item.score + (item.adjustment || 0)));
}

/* ---------- filters ---------- */

function isFilterActive() {
    const f = state.filter;
    return f.text !== "" || f.min > 0 || f.max < 100;
}

/** Text filter: every word must appear in case number, title or explanation (DE or FR). */
function matchesFilter(item) {
    const score = effectiveScore(item);
    const { text, min, max } = state.filter;
    if (score < min || score > max) return false;
    if (!text) return true;
    const haystack = [
        item.caseNumber, item.title.de, item.title.fr, item.explanation.de, item.explanation.fr,
    ].join(" ").toLowerCase();
    return text.toLowerCase().split(/\s+/).every((term) => haystack.includes(term));
}

/** Items currently shown: filtered and sorted by score (highest first). */
function visibleItems() {
    return state.items
        .filter(matchesFilter)
        .sort((a, b) => effectiveScore(b) - effectiveScore(a));
}

function readScoreInput(input, fallback) {
    const value = parseInt(input.value, 10);
    return Number.isNaN(value) ? fallback : Math.max(0, Math.min(100, value));
}

function onFilterChange() {
    state.filter = {
        text: $("#filter-text").value.trim(),
        min: readScoreInput($("#filter-min"), 0),
        max: readScoreInput($("#filter-max"), 100),
    };
    // keep only selections that are still visible
    const visible = new Set(visibleItems().map((i) => i.id));
    state.selected = new Set([...state.selected].filter((id) => visible.has(id)));
    renderList();
}

function resetFilters() {
    $("#filter-text").value = "";
    $("#filter-min").value = 0;
    $("#filter-max").value = 100;
    onFilterChange();
}

function scoreLevel(score) {
    if (score >= threshold_high_relevance) return "high";
    if (score >= threshold_medium_relevance) return "medium";
    return "low";
}

/* ---------- theme ---------- */

function setTheme(theme) {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("theme", theme);
}

function toggleTheme() {
    setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
}

/* ---------- date inputs (typing + calendar) ---------- */

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

function setDateInput(textInput, iso) {
    textInput.value = formatDate(iso);
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
    if (range.error) {
        showFormError(range.error);
        return;
    }
    showFormError(null);

    const button = $("#search-btn");
    button.disabled = true;
    button.classList.add("loading");
    setStatus("searching");

    try {
        state.items = await searchJudgements(range.from, range.to, currentLang);
        state.error = null;
    } catch (err) {
        console.error(err);
        state.items = [];
        state.error = "errorSearch";
    } finally {
        state.hasSearched = true;
        state.selected.clear();
        button.disabled = false;
        button.classList.remove("loading");
        renderList();
    }

    autoSummarizeHighRelevance();
}

/** Highly relevant judgements are summarized right away (in the background, panels stay closed). */
async function autoSummarizeHighRelevance() {
    const ids = state.items
        .filter((i) => scoreLevel(effectiveScore(i)) === "high")
        .map((i) => i.id);
    if (ids.length === 0) return;
    await Promise.all(ids.map(loadSummary));
    refreshKb();
}

/* ---------- relevance correction (traffic light) ---------- */

/** Moves a judgement to another relevance level. If its base score already lies in that
 *  level, the manual adjustment is removed; otherwise it is set to the level's target score. */
function setLevel(id, level) {
    const item = state.items.find((i) => i.id === id);
    if (!item) return;
    const current = scoreLevel(effectiveScore(item));
    if (level === current) return;

    item.adjustment = scoreLevel(item.score) === level ? 0 : LEVEL_TARGET_SCORE[level] - item.score;
    const direction = LEVELS.indexOf(level) < LEVELS.indexOf(current) ? +1 : -1;
    sendFeedback(id, item.adjustment, direction, level);   // backend persists it for future lookups
    renderList();
}

/* ---------- summaries + knowledge base ---------- */

async function loadSummary(id) {
    if (state.summaries[id] || state.summaryLoading.has(id)) return;
    state.summaryLoading.add(id);
    state.summaryError.delete(id);
    renderList();
    try {
        const entry = await summarizeJudgement(id);   // generates DE + FR, stores in KB
        state.summaries[id] = { de: entry.de, fr: entry.fr };
        const item = state.items.find((i) => i.id === id);
        if (item) item.summarized = true;
    } catch (err) {
        console.error(err);
        state.summaryError.add(id);
    } finally {
        state.summaryLoading.delete(id);
        renderList();
    }
}

async function summarize(ids) {
    for (const id of ids) {
        if (!state.openPanels[id]) state.openPanels[id] = [currentLang];
    }
    renderList();
    await Promise.all(ids.map(loadSummary));
    refreshKb();
}

function toggleSummary(id) {
    if (state.openPanels[id]) {
        delete state.openPanels[id];
        renderList();
        return;
    }
    summarize([id]);
}

function summarizeSelected() {
    const ids = [...state.selected];
    state.selected.clear();
    summarize(ids);
}

function toggleSelected(id, checked) {
    if (checked) state.selected.add(id);
    else state.selected.delete(id);
    renderSelectionToolbar();
}

function toggleSelectAll(checked) {
    state.selected = new Set(checked ? visibleItems().map((i) => i.id) : []);
    renderList();
}

/* ---------- editing summaries ---------- */

function isDirty(id, stored) {
    const draft = state.drafts[id];
    return Boolean(draft && stored && (draft.de !== stored.de || draft.fr !== stored.fr));
}

function onDraftInput(id, lang, value, stored, editor) {
    state.drafts[id] = { ...(state.drafts[id] || stored), [lang]: value };
    delete state.saveStatus[id];
    updateEditorFooter(editor, id, stored);   // no full re-render while typing
}

async function onSave(id, stored) {
    if (!isDirty(id, stored) || state.saveStatus[id] === "saving") return;
    const texts = state.drafts[id];
    state.saveStatus[id] = "saving";
    renderAll();
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
    renderList();
    refreshKb();
}

/** TEMPORARY (development): wipes the knowledge base and the relevance feedback. */
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
    state.openPanels = {};
    state.kbLang = {};
    for (const item of state.items) {
        item.summarized = false;
        item.adjustment = 0;
    }
    renderList();
    refreshKb();
}

let kbRequest = 0;

async function refreshKb() {
    const requestId = ++kbRequest;
    try {
        const entries = await searchKnowledgeBase(state.kbQuery, currentLang);
        if (requestId !== kbRequest) return;   // a newer query is in flight
        state.kbEntries = entries;
    } catch (err) {
        console.error(err);
        state.kbEntries = [];
    }
    renderKb();
}

/* ---------- rendering: results ---------- */

let statusKey = "emptyInitial";

function setStatus(key) {
    statusKey = key;
    const status = $("#results-status");
    status.hidden = !key;
    status.textContent = key ? t(key) : "";
}

/** DE / FR toggle; both can be active at once (then shown side by side), never neither. */
function langToggle(activeLangs, onChange) {
    return el("div", { class: "seg small", role: "group", "aria-label": t("summaryLang") },
        ["de", "fr"].map((l) => {
            const active = activeLangs.includes(l);
            return el("button", {
                type: "button",
                class: active ? "active" : "",
                "aria-pressed": String(active),
                text: l.toUpperCase(),
                onclick: () => {
                    if (active && activeLangs.length === 1) return;   // keep at least one
                    onChange(active ? activeLangs.filter((x) => x !== l) : ["de", "fr"].filter((x) => x === l || activeLangs.includes(x)));
                },
            });
        }));
}

function saveStatusText(id, stored) {
    const status = state.saveStatus[id];
    if (status === "saving") return t("saving");
    if (status === "error") return t("errorSave");
    if (isDirty(id, stored)) return t("unsaved");
    if (status === "saved") return `✓ ${t("saved")}`;
    return "";
}

function updateEditorFooter(editor, id, stored) {
    editor.querySelector(".save-btn").disabled = !isDirty(id, stored) || state.saveStatus[id] === "saving";
    const status = editor.querySelector(".save-status");
    status.textContent = saveStatusText(id, stored);
    status.classList.toggle("error", state.saveStatus[id] === "error");
}

/** Editable summary in one or two languages with a save button (lower left).
 *  `scope` distinguishes the results list from the knowledge base for focus restoring;
 *  `footerExtra` is appended to the footer row (e.g. the relevance signal). */
function summaryEditor(scope, id, stored, langs, footerExtra = null) {
    const texts = state.drafts[id] || stored;
    const editor = el("div", { class: "summary-editor" });

    const columns = el("div", { class: `summary-columns cols-${langs.length}` }, langs.map((lang) =>
        el("label", { class: "summary-column" }, [
            el("span", { class: "summary-lang", text: t(lang === "de" ? "langDe" : "langFr") }),
            el("textarea", {
                class: "summary-textarea",
                lang,
                rows: "10",
                spellcheck: "true",
                "data-focus-key": `${scope}:${id}:${lang}`,
                oninput: (e) => onDraftInput(id, lang, e.target.value, stored, editor),
            }),
        ])));
    // textarea content must be set as a property, not via the text helper
    columns.querySelectorAll("textarea").forEach((ta) => { ta.value = texts[ta.lang] || ""; });

    const footer = el("div", { class: "editor-footer" }, [
        el("button", {
            type: "button", class: "primary-btn small save-btn", text: t("save"),
            onclick: () => onSave(id, stored),
        }),
        el("span", { class: "save-status muted", "aria-live": "polite" }),
        footerExtra,
    ]);

    editor.append(columns, footer);
    updateEditorFooter(editor, id, stored);
    return editor;
}

/** Relevance signal (bottom right of the panel): green / yellow / red = high / medium / low,
 *  the current level is ringed; clicking another light moves the judgement there. */
function relevanceSignal(item) {
    const current = scoreLevel(effectiveScore(item));
    return el("div", { class: "signal" }, [
        el("span", { class: "signal-caption" }, [
            `${t("adjustRelevance")}: `,
            el("strong", { text: t(LEVEL_LABEL_KEY[current]) }),
        ]),
        el("div", { class: "signal-housing", role: "radiogroup", "aria-label": t("adjustRelevance") },
            LEVELS.map((level) => {
                const label = t(LEVEL_LABEL_KEY[level]);
                const on = level === current;
                return el("button", {
                    type: "button",
                    role: "radio",
                    class: `signal-light ${level}${on ? " on" : ""}`,
                    "aria-checked": String(on),
                    "aria-label": label,
                    title: on ? label : t("setLevel", label),
                    onclick: () => setLevel(item.id, level),
                });
            })),
    ]);
}

function renderSummaryPanel(item) {
    const langs = state.openPanels[item.id];
    const stored = state.summaries[item.id];
    const signal = relevanceSignal(item);

    let body;
    if (state.summaryLoading.has(item.id)) {
        body = [
            el("p", { class: "muted loading-line" }, [
                el("span", { class: "spinner dark", "aria-hidden": "true" }),
                t("summarizing"),
            ]),
            el("div", { class: "editor-footer" }, [signal]),
        ];
    } else if (state.summaryError.has(item.id) || !stored) {
        body = [
            el("p", { class: "form-error", text: t("errorSummary") }),
            el("div", { class: "editor-footer" }, [signal]),
        ];
    } else {
        body = summaryEditor("res", item.id, stored, langs, signal);
    }

    const toggle = langToggle(langs, (next) => {
        state.openPanels[item.id] = next;
        renderList();
    });
    return el("div", { class: "summary-panel" }, [toggle, body]);
}

function renderItem(item) {
    const score = effectiveScore(item);
    const shownAdjustment = score - item.score;
    const isOpen = Boolean(state.openPanels[item.id]);

    const details = el("details", { class: "explanation" }, [
        el("summary", { text: t("whyRelevant") }),
        el("p", { text: item.explanation[currentLang] }),
    ]);
    if (state.openExplanations.has(item.id)) details.open = true;
    details.addEventListener("toggle", () => {
        if (details.open) state.openExplanations.add(item.id);
        else state.openExplanations.delete(item.id);
    });

    const checkbox = el("input", {
        type: "checkbox",
        class: "select-box",
        "aria-label": `${t("selectItem")} ${item.caseNumber}`,
        checked: state.selected.has(item.id),
        onchange: (e) => toggleSelected(item.id, e.target.checked),
    });

    const scoreBadge = el("div", {
        class: `score ${scoreLevel(score)}`,
        title: `${t("relevance")}: ${score}`,
        "aria-label": `${t("relevance")}: ${score}`,
    }, [
        el("span", { class: "score-value", text: String(score) }),
        shownAdjustment ? el("span", {
            class: "score-adj",
            text: shownAdjustment > 0 ? `+${shownAdjustment}` : String(shownAdjustment),
        }) : null,
    ]);

    const main = el("div", { class: "item-main" }, [
        el("div", { class: "item-meta" }, [
            el("span", { class: "case-number", text: item.caseNumber }),
            el("span", { class: "dot", "aria-hidden": "true", text: "·" }),
            el("time", { datetime: item.date, text: formatDate(item.date) }),
            item.summarized ? el("span", { class: "kb-badge", text: t("inKb") }) : null,
            item.url ? el("a", {
                class: "original-link", href: item.url, target: "_blank", rel: "noopener",
                text: t("original") + " ↗",
            }) : null,
        ]),
        el("h3", { class: "item-title", text: item.title[currentLang] }),
        details,
    ]);

    const isLoading = state.summaryLoading.has(item.id);
    const hasSummary = Boolean(state.summaries[item.id]) || item.summarized;
    const actions = el("div", { class: "item-actions" }, [
        el("button", {
            type: "button", class: `secondary-btn${isOpen ? " active" : ""}`,
            "aria-expanded": String(isOpen),
            "aria-busy": String(isLoading),
            text: isOpen ? t("hideSummary") : (hasSummary || isLoading ? t("open") : t("summarize")),
            onclick: () => toggleSummary(item.id),
        }, isLoading && !isOpen ? [el("span", { class: "spinner dark", "aria-hidden": "true" })] : []),
    ]);

    return el("li", { class: "card result-item", "data-id": item.id }, [
        el("div", { class: "item-row" }, [checkbox, scoreBadge, main, actions]),
        isOpen ? renderSummaryPanel(item) : null,
    ]);
}

function renderSelectionToolbar() {
    const visibleCount = visibleItems().length;
    $("#selection-toolbar").hidden = visibleCount === 0;

    const selectAll = $("#select-all");
    const n = state.selected.size;
    selectAll.checked = n > 0 && n === visibleCount;
    selectAll.indeterminate = n > 0 && n < visibleCount;

    const button = $("#summarize-selected");
    button.textContent = t("summarizeSelected", n);
    button.disabled = n === 0;
}

function renderList() {
    const list = $("#results-list");

    // FLIP: remember old positions so re-sorting animates smoothly
    const before = new Map();
    list.querySelectorAll(".result-item").forEach((node) => {
        before.set(node.dataset.id, node.getBoundingClientRect().top);
    });

    const visible = visibleItems();
    preservingFocus(list, () => list.replaceChildren(...visible.map(renderItem)));

    list.querySelectorAll(".result-item").forEach((node) => {
        const oldTop = before.get(node.dataset.id);
        if (oldTop === undefined) return;
        const delta = oldTop - node.getBoundingClientRect().top;
        if (delta) {
            node.animate(
                [{ transform: `translateY(${delta}px)` }, { transform: "translateY(0)" }],
                { duration: 250, easing: "ease-out" },
            );
        }
    });

    const total = state.items.length;
    let count = "";
    if (state.hasSearched && !state.error) {
        count = isFilterActive() ? t("resultsCountFiltered", visible.length, total) : t("resultsCount", total);
    }
    $("#results-count").textContent = count;
    $("#filter-bar").hidden = total === 0;

    if (state.error) setStatus(state.error);
    else if (!state.hasSearched) setStatus("emptyInitial");
    else if (total === 0) setStatus("emptyNoResults");
    else if (visible.length === 0) setStatus("emptyFiltered");
    else setStatus(null);

    renderSelectionToolbar();
}

/* ---------- rendering: knowledge base ---------- */

/** Re-renders `container` and puts the caret back into the textarea the user was typing in. */
function preservingFocus(container, render) {
    const active = document.activeElement;
    const key = active && container.contains(active) ? active.dataset.focusKey : null;
    const saved = key ? { start: active.selectionStart, end: active.selectionEnd, scroll: active.scrollTop } : null;
    render();
    if (!key) return;
    const node = container.querySelector(`[data-focus-key="${CSS.escape(key)}"]`);
    if (!node) return;
    node.focus({ preventScroll: true });
    node.setSelectionRange(saved.start, saved.end);
    node.scrollTop = saved.scroll;
}

function fileIcon() {
    const tpl = document.createElement("template");
    tpl.innerHTML = '<svg class="file-icon" viewBox="0 0 24 24" aria-hidden="true">'
        + '<path d="M7 3h7l5 5v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/>'
        + '<path d="M14 3v5h5M9 13h6M9 17h6"/></svg>';
    return tpl.content.firstChild;
}

function renderKbEntry(entry) {
    const langs = state.kbLang[entry.id] || [currentLang];
    const stored = formatDate(entry.storedAt.slice(0, 10));

    return el("li", { class: "kb-entry" }, [
        el("details", {}, [
            el("summary", {}, [
                fileIcon(),
                el("span", { class: "case-number", text: entry.caseNumber }),
                el("span", { class: "kb-entry-title", text: entry.title[currentLang] }),
                el("span", { class: "muted kb-entry-date", text: `${t("storedAt")} ${stored}` }),
            ]),
            el("div", { class: "kb-entry-body" }, [
                langToggle(langs, (next) => {
                    state.kbLang[entry.id] = next;
                    renderKb();
                }),
                summaryEditor("kb", entry.id, { de: entry.de, fr: entry.fr }, langs),
            ]),
        ]),
    ]);
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
        node.dataset.id = entry.id;
        if (openIds.has(entry.id)) node.querySelector("details").open = true;
        return node;
    })));

    const n = state.kbEntries.length;
    const tabCount = $("#kb-tab-count");
    tabCount.hidden = !n || Boolean(state.kbQuery);
    tabCount.textContent = n ? String(n) : "";

    $("#kb-count").textContent = n ? t("kbCount", n) : "";
    const status = $("#kb-status");
    const key = state.kbEntries.length ? null : (state.kbQuery ? "kbNoMatch" : "kbEmpty");
    status.hidden = !key;
    status.textContent = key ? t(key) : "";
}

/* ---------- folder tabs ---------- */

function renderTabs() {
    document.querySelectorAll(".folder-tab").forEach((tab) => {
        const active = tab.dataset.tab === state.activeTab;
        tab.classList.toggle("active", active);
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
}

function renderAll() {
    renderList();
    renderKb();
}

/* ---------- language ---------- */

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
    setStatus(statusKey);
    renderList();
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

    const tabs = [...document.querySelectorAll(".folder-tab")];
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
    $("#select-all").addEventListener("change", (e) => toggleSelectAll(e.target.checked));
    $("#summarize-selected").addEventListener("click", summarizeSelected);

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

    renderLanguage();
    refreshKb();
}

init();
