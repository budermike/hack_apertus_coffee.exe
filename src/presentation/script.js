/* script.js — UI state and rendering. Depends on i18n.js and api.js (loaded before). */

const VOTE_STEP = 5;

const state = {
    items: [],                 // judgements from the last search
    hasSearched: false,
    error: null,               // i18n key of a list-level error
    votes: JSON.parse(localStorage.getItem("votes") || "{}"),   // id -> score adjustment
    summaries: {},             // id -> { de?: text, fr?: text }
    summaryLoading: new Set(), // "id:lang"
    summaryError: new Set(),   // "id:lang"
    openPanels: {},            // id -> language shown in the open summary panel
    openExplanations: new Set(),
};

/* ---------- helpers ---------- */

const $ = (sel) => document.querySelector(sel);

function el(tag, props = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
        if (key === "class") node.className = value;
        else if (key === "text") node.textContent = value;
        else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
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

function effectiveScore(item) {
    const adjusted = item.score + (state.votes[item.id] || 0);
    return Math.max(0, Math.min(100, adjusted));
}

function scoreLevel(score) {
    if (score >= 70) return "high";
    if (score >= 40) return "medium";
    return "low";
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
            textInput.value = formatDate(picker.value);
            textInput.dataset.iso = picker.value;
            textInput.classList.remove("invalid");
        }
    });
    textInput.addEventListener("blur", () => {
        const iso = parseDate(textInput.value);
        if (iso) {
            textInput.value = formatDate(iso);
            textInput.dataset.iso = iso;
        }
    });
}

function setDateInput(textInput, iso) {
    textInput.value = formatDate(iso);
    textInput.dataset.iso = iso;
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
        button.disabled = false;
        button.classList.remove("loading");
        renderList();
    }
}

/* ---------- voting ---------- */

function vote(id, direction) {
    const item = state.items.find((i) => i.id === id);
    if (!item) return;
    const current = state.votes[id] || 0;
    // clamp so the effective score stays within 0..100
    const next = Math.max(-item.score, Math.min(100 - item.score, current + direction * VOTE_STEP));
    if (next === current) return;
    state.votes[id] = next;
    localStorage.setItem("votes", JSON.stringify(state.votes));
    sendFeedback(id, next);
    renderList();
}

/* ---------- summaries ---------- */

async function loadSummary(id, lang) {
    const key = `${id}:${lang}`;
    if (state.summaries[id]?.[lang] || state.summaryLoading.has(key)) return;
    state.summaryLoading.add(key);
    state.summaryError.delete(key);
    renderList();
    try {
        const result = await summarizeJudgement(id, lang);
        state.summaries[id] = { ...state.summaries[id], [lang]: result.text };
    } catch (err) {
        console.error(err);
        state.summaryError.add(key);
    } finally {
        state.summaryLoading.delete(key);
        renderList();
    }
}

function toggleSummary(id) {
    if (state.openPanels[id]) {
        delete state.openPanels[id];
        renderList();
        return;
    }
    state.openPanels[id] = currentLang;
    loadSummary(id, currentLang);
    renderList();
}

function switchSummaryLang(id, lang) {
    state.openPanels[id] = lang;
    loadSummary(id, lang);
    renderList();
}

/* ---------- rendering ---------- */

let statusKey = "emptyInitial";

function setStatus(key) {
    statusKey = key;
    const status = $("#results-status");
    status.hidden = !key;
    status.textContent = key ? t(key) : "";
}

function renderSummaryPanel(item) {
    const lang = state.openPanels[item.id];
    const key = `${item.id}:${lang}`;
    const text = state.summaries[item.id]?.[lang];

    let body;
    if (state.summaryLoading.has(key)) {
        body = el("p", { class: "muted loading-line" }, [
            el("span", { class: "spinner dark", "aria-hidden": "true" }),
            t("summarizing"),
        ]);
    } else if (state.summaryError.has(key)) {
        body = el("p", { class: "form-error", text: t("errorSummary") });
    } else {
        body = el("div", { class: "summary-text", text: text || "" });
    }

    const tabs = el("div", { class: "seg small", role: "group", "aria-label": t("summaryLang") },
        ["de", "fr"].map((l) => el("button", {
            type: "button",
            class: l === lang ? "active" : "",
            "aria-pressed": String(l === lang),
            text: l.toUpperCase(),
            onclick: () => switchSummaryLang(item.id, l),
        })));

    return el("div", { class: "summary-panel" }, [tabs, body]);
}

function renderItem(item) {
    const score = effectiveScore(item);
    const adjustment = state.votes[item.id] || 0;
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

    const scoreBadge = el("div", {
        class: `score ${scoreLevel(score)}`,
        title: `${t("relevance")}: ${score}`,
        "aria-label": `${t("relevance")}: ${score}`,
    }, [
        el("span", { class: "score-value", text: String(score) }),
        adjustment ? el("span", {
            class: "score-adj",
            text: adjustment > 0 ? `+${adjustment}` : String(adjustment),
        }) : null,
    ]);

    const main = el("div", { class: "item-main" }, [
        el("div", { class: "item-meta" }, [
            el("span", { class: "case-number", text: item.caseNumber }),
            el("span", { class: "dot", "aria-hidden": "true", text: "·" }),
            el("time", { datetime: item.date, text: formatDate(item.date) }),
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
                type: "button", class: `icon-btn vote up${adjustment > 0 ? " active" : ""}`,
                "aria-label": t("upvote"), title: t("upvote"), text: "▲",
                onclick: () => vote(item.id, +1),
            }),
            el("button", {
                type: "button", class: `icon-btn vote down${adjustment < 0 ? " active" : ""}`,
                "aria-label": t("downvote"), title: t("downvote"), text: "▼",
                onclick: () => vote(item.id, -1),
            }),
        ]),
        el("button", {
            type: "button", class: `secondary-btn${isOpen ? " active" : ""}`,
            "aria-expanded": String(isOpen),
            text: isOpen ? t("hideSummary") : t("summarize"),
            onclick: () => toggleSummary(item.id),
        }),
    ]);

    return el("li", { class: "card result-item", "data-id": item.id }, [
        el("div", { class: "item-row" }, [scoreBadge, main, actions]),
        isOpen ? renderSummaryPanel(item) : null,
    ]);
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
}

/* ---------- init ---------- */

function init() {
    document.querySelectorAll(".lang-switch button").forEach((btn) => {
        btn.addEventListener("click", () => {
            setLanguage(btn.dataset.lang);
            renderLanguage();
        });
    });

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
    renderLanguage();
}

init();
