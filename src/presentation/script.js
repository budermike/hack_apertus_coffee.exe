/* script.js — UI state and rendering. Depends on i18n.js and api.js (loaded before). */

const VOTE_STEP = 5;

const state = {
    items: [],                 // judgements from the last search (incl. backend `adjustment`)
    hasSearched: false,
    error: null,               // i18n key of a list-level error
    summaries: {},             // id -> { de, fr }
    summaryLoading: new Set(), // ids currently being summarized
    summaryError: new Set(),   // ids whose summary failed
    openPanels: {},            // id -> language shown in the open summary panel
    openExplanations: new Set(),
    selected: new Set(),       // ids selected for bulk summarize
    kbEntries: [],
    kbQuery: "",
    kbLang: {},                // kb entry id -> language shown
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

function toIso(date) {
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, "0");
    const d = String(date.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
}

/** Parse "TT.MM.JJJJ", "TT/MM/JJJJ" or "JJJJ-MM-TT" into an ISO date string, or null. */
function parseDate(text) {
    const s = text.trim();
    let y, m, d;
    let match = s.match(/^(\d{1,2})[./-](\d{1,2})[./-](\d{4})$/);
    if (match) [, d, m, y] = match.map(Number);
    else if ((match = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/))) [, y, m, d] = match.map(Number);
    else return null;
    const date = new Date(y, m - 1, d);
    if (date.getFullYear() !== y || date.getMonth() !== m - 1 || date.getDate() !== d) return null;
    return toIso(date);
}

/** Displayed score: base score + net adjustment, clamped to 0..100.
 *  The raw adjustment is kept unclamped, so 97 → (+5) 100 → (−5) 97 round-trips. */
function effectiveScore(item) {
    return Math.max(0, Math.min(100, item.score + (item.adjustment || 0)));
}

function scoreLevel(score) {
    if (score >= 70) return "high";
    if (score >= 40) return "medium";
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
    const picker = document.querySelector(`.native-picker[data-for="${textInput.id}"]`);
    const button = document.querySelector(`.calendar-btn[data-target="${textInput.id}"]`);

    button.addEventListener("click", () => {
        const iso = parseDate(textInput.value);
        if (iso) picker.value = iso;
        if (typeof picker.showPicker === "function") picker.showPicker();
        else picker.focus();
    });
    picker.addEventListener("change", () => {
        if (picker.value) {
            setDateInput(textInput, picker.value);
            textInput.classList.remove("invalid");
        }
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
}

/* ---------- voting ---------- */

function canVote(item, direction) {
    const score = effectiveScore(item);
    return direction > 0 ? score < 100 : score > 0;
}

function vote(id, direction) {
    const item = state.items.find((i) => i.id === id);
    if (!item || !canVote(item, direction)) return;
    item.adjustment = (item.adjustment || 0) + direction * VOTE_STEP;
    sendFeedback(id, item.adjustment, direction);   // backend persists it for future lookups
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
        if (!state.openPanels[id]) state.openPanels[id] = currentLang;
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
    state.selected = new Set(checked ? state.items.map((i) => i.id) : []);
    renderList();
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

function langTabs(activeLang, onSelect) {
    return el("div", { class: "seg small", role: "group", "aria-label": t("summaryLang") },
        ["de", "fr"].map((l) => el("button", {
            type: "button",
            class: l === activeLang ? "active" : "",
            "aria-pressed": String(l === activeLang),
            text: l.toUpperCase(),
            onclick: () => onSelect(l),
        })));
}

function renderSummaryPanel(item) {
    const lang = state.openPanels[item.id];
    const text = state.summaries[item.id]?.[lang];

    let body;
    if (state.summaryLoading.has(item.id)) {
        body = el("p", { class: "muted loading-line" }, [
            el("span", { class: "spinner dark", "aria-hidden": "true" }),
            t("summarizing"),
        ]);
    } else if (state.summaryError.has(item.id)) {
        body = el("p", { class: "form-error", text: t("errorSummary") });
    } else {
        body = el("div", { class: "summary-text", text: text || "" });
    }

    const tabs = langTabs(lang, (l) => {
        state.openPanels[item.id] = l;
        renderList();
    });
    return el("div", { class: "summary-panel" }, [tabs, body]);
}

function renderItem(item) {
    const score = effectiveScore(item);
    const shownAdjustment = score - item.score;
    const isOpen = Boolean(state.openPanels[item.id]);
    const isLoading = state.summaryLoading.has(item.id);

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

    const actions = el("div", { class: "item-actions" }, [
        el("div", { class: "votes" }, [
            el("button", {
                type: "button", class: `icon-btn vote up${shownAdjustment > 0 ? " active" : ""}`,
                "aria-label": t("upvote"), title: t("upvote"), text: "▲",
                disabled: !canVote(item, +1),
                onclick: () => vote(item.id, +1),
            }),
            el("button", {
                type: "button", class: `icon-btn vote down${shownAdjustment < 0 ? " active" : ""}`,
                "aria-label": t("downvote"), title: t("downvote"), text: "▼",
                disabled: !canVote(item, -1),
                onclick: () => vote(item.id, -1),
            }),
        ]),
        el("button", {
            type: "button", class: `secondary-btn${isOpen ? " active" : ""}`,
            "aria-expanded": String(isOpen),
            disabled: isLoading,
            text: isOpen ? t("hideSummary") : t("summarize"),
            onclick: () => toggleSummary(item.id),
        }),
    ]);

    return el("li", { class: "card result-item", "data-id": item.id }, [
        el("div", { class: "item-row" }, [checkbox, scoreBadge, main, actions]),
        isOpen ? renderSummaryPanel(item) : null,
    ]);
}

function renderSelectionToolbar() {
    const toolbar = $("#selection-toolbar");
    toolbar.hidden = state.items.length === 0;

    const selectAll = $("#select-all");
    const n = state.selected.size;
    selectAll.checked = n > 0 && n === state.items.length;
    selectAll.indeterminate = n > 0 && n < state.items.length;

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

    const sorted = [...state.items].sort((a, b) => effectiveScore(b) - effectiveScore(a));
    list.replaceChildren(...sorted.map(renderItem));

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

    $("#results-count").textContent = state.hasSearched && !state.error ? t("resultsCount", sorted.length) : "";
    if (state.error) setStatus(state.error);
    else if (!state.hasSearched) setStatus("emptyInitial");
    else if (sorted.length === 0) setStatus("emptyNoResults");
    else setStatus(null);

    renderSelectionToolbar();
}

/* ---------- rendering: knowledge base ---------- */

function renderKbEntry(entry) {
    const lang = state.kbLang[entry.id] || currentLang;
    const stored = formatDate(entry.storedAt.slice(0, 10));

    return el("li", { class: "kb-entry" }, [
        el("details", {}, [
            el("summary", {}, [
                el("span", { class: "case-number", text: entry.caseNumber }),
                el("span", { class: "kb-entry-title", text: entry.title[currentLang] }),
                el("span", { class: "muted kb-entry-date", text: `${t("storedAt")} ${stored}` }),
            ]),
            el("div", { class: "kb-entry-body" }, [
                langTabs(lang, (l) => {
                    state.kbLang[entry.id] = l;
                    renderKb();
                }),
                el("div", { class: "summary-text", text: entry[lang] }),
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

    list.replaceChildren(...state.kbEntries.map((entry) => {
        const node = renderKbEntry(entry);
        node.dataset.id = entry.id;
        if (openIds.has(entry.id)) node.querySelector("details").open = true;
        return node;
    }));

    $("#kb-count").textContent = state.kbEntries.length ? t("kbCount", state.kbEntries.length) : "";
    const status = $("#kb-status");
    const key = state.kbEntries.length ? null : (state.kbQuery ? "kbNoMatch" : "kbEmpty");
    status.hidden = !key;
    status.textContent = key ? t(key) : "";
}

/* ---------- language ---------- */

function renderLanguage() {
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
