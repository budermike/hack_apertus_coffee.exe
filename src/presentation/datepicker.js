/* datepicker.js — small translated calendar popup for a text date field.
 * Replaces the browser's native picker, which always uses the browser language.
 * Depends on i18n.js (currentLang, LOCALES, t, formatDate).
 */

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

const openPickers = new Set();

function closeAllDatePickers() {
    openPickers.forEach((picker) => picker.close());
}

/**
 * Attach a calendar popup to `input`, opened by `button`.
 * `onPick(iso)` is called when a day is chosen. Days after `maxIso` are disabled.
 */
function createDatePicker(input, button, { onPick, maxIso } = {}) {
    const popup = document.createElement("div");
    popup.className = "datepicker";
    popup.setAttribute("role", "dialog");
    popup.hidden = true;
    input.parentElement.append(popup);

    let view;           // first day of the displayed month
    let mode = "days";  // "days" | "months" | "years"
    const YEARS_PER_PAGE = 12;

    function button_(className, label, text, onClick, disabled = false) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = className;
        if (label) b.setAttribute("aria-label", label);
        b.textContent = text;
        b.disabled = disabled;
        b.addEventListener("click", onClick);
        return b;
    }

    function header(titleText, titleLabel, onTitle, prevLabel, onPrev, nextLabel, onNext) {
        const h = document.createElement("div");
        h.className = "dp-header";
        const title = onTitle
            ? button_("dp-title dp-title-btn", titleLabel, titleText, onTitle)
            : Object.assign(document.createElement("span"), { className: "dp-title", textContent: titleText });
        h.append(
            button_("icon-btn dp-nav", prevLabel, "‹", onPrev),
            title,
            button_("icon-btn dp-nav", nextLabel, "›", onNext),
        );
        return h;
    }

    function render() {
        if (mode === "months") renderMonths();
        else if (mode === "years") renderYears();
        else renderDays();
    }

    /** Month grid for the displayed year; click the year to pick another year. */
    function renderMonths() {
        const locale = LOCALES[currentLang];
        const max = maxIso ? maxIso() : null;
        const year = view.getFullYear();
        const selected = parseDate(input.value);
        const monthName = new Intl.DateTimeFormat(locale, { month: "short" });

        const grid = document.createElement("div");
        grid.className = "dp-grid dp-grid-wide";
        for (let m = 0; m < 12; m++) {
            const firstDay = toIso(new Date(year, m, 1));
            const classes = ["dp-cell"];
            if (selected && selected.slice(0, 7) === firstDay.slice(0, 7)) classes.push("selected");
            grid.append(button_(classes.join(" "), null,
                monthName.format(new Date(year, m, 1)).replace(".", ""),
                () => { view = new Date(year, m, 1); mode = "days"; render(); },
                Boolean(max && firstDay > max)));
        }
        popup.replaceChildren(
            header(String(year), t("chooseYear"), () => { mode = "years"; render(); },
                t("prevYear"), () => shiftMonth(-12), t("nextYear"), () => shiftMonth(12)),
            grid,
        );
    }

    /** Page of 12 years. */
    function renderYears() {
        const max = maxIso ? maxIso() : null;
        const year = view.getFullYear();
        const start = year - (year % YEARS_PER_PAGE);
        const selectedYear = parseDate(input.value)?.slice(0, 4);

        const grid = document.createElement("div");
        grid.className = "dp-grid dp-grid-wide";
        for (let y = start; y < start + YEARS_PER_PAGE; y++) {
            const classes = ["dp-cell"];
            if (String(y) === selectedYear) classes.push("selected");
            grid.append(button_(classes.join(" "), null, String(y),
                () => { view = new Date(y, view.getMonth(), 1); mode = "months"; render(); },
                Boolean(max && `${y}-01-01` > max)));
        }
        popup.replaceChildren(
            header(`${start} – ${start + YEARS_PER_PAGE - 1}`, null, null,
                t("prevYears"), () => shiftMonth(-12 * YEARS_PER_PAGE),
                t("nextYears"), () => shiftMonth(12 * YEARS_PER_PAGE)),
            grid,
        );
    }

    function renderDays() {
        const locale = LOCALES[currentLang];
        const selected = parseDate(input.value);
        const today = toIso(new Date());
        const max = maxIso ? maxIso() : null;

        const head = header(
            new Intl.DateTimeFormat(locale, { month: "long", year: "numeric" }).format(view),
            t("chooseMonth"), () => { mode = "months"; render(); },
            t("prevMonth"), () => shiftMonth(-1),
            t("nextMonth"), () => shiftMonth(1),
        );

        const grid = document.createElement("div");
        grid.className = "dp-grid";
        // weekday header, Monday first (2024-01-01 was a Monday)
        const weekday = new Intl.DateTimeFormat(locale, { weekday: "short" });
        for (let i = 0; i < 7; i++) {
            const cell = document.createElement("span");
            cell.className = "dp-weekday";
            cell.textContent = weekday.format(new Date(2024, 0, 1 + i)).replace(".", "");
            grid.append(cell);
        }
        const offset = (view.getDay() + 6) % 7;   // Monday = 0
        for (let i = 0; i < offset; i++) grid.append(document.createElement("span"));

        const daysInMonth = new Date(view.getFullYear(), view.getMonth() + 1, 0).getDate();
        for (let day = 1; day <= daysInMonth; day++) {
            const iso = toIso(new Date(view.getFullYear(), view.getMonth(), day));
            const classes = ["dp-day"];
            if (iso === today) classes.push("today");
            if (iso === selected) classes.push("selected");
            const b = button_(classes.join(" "), formatDate(iso), String(day), () => pick(iso), Boolean(max && iso > max));
            if (iso === selected) b.setAttribute("aria-pressed", "true");
            grid.append(b);
        }

        const footer = document.createElement("div");
        footer.className = "dp-footer";
        footer.append(button_("secondary-btn small", null, t("today"), () => pick(today)));

        popup.replaceChildren(head, grid, footer);
    }

    function shiftMonth(delta) {
        view = new Date(view.getFullYear(), view.getMonth() + delta, 1);
        render();
    }

    function pick(iso) {
        if (onPick) onPick(iso);
        close();
        input.focus();
    }

    function onOutside(event) {
        if (!popup.contains(event.target) && !button.contains(event.target)) close();
    }

    function onKey(event) {
        if (event.key === "Escape") {
            close();
            button.focus();
        }
    }

    function open() {
        closeAllDatePickers();
        const iso = parseDate(input.value);
        const base = iso ? new Date(iso + "T00:00:00") : new Date();
        view = new Date(base.getFullYear(), base.getMonth(), 1);
        mode = "days";
        render();
        popup.hidden = false;
        button.setAttribute("aria-expanded", "true");
        openPickers.add(picker);
        document.addEventListener("mousedown", onOutside);
        document.addEventListener("keydown", onKey);
        (popup.querySelector(".dp-day.selected") || popup.querySelector(".dp-day:not(:disabled)"))?.focus();
    }

    function close() {
        popup.hidden = true;
        button.setAttribute("aria-expanded", "false");
        openPickers.delete(picker);
        document.removeEventListener("mousedown", onOutside);
        document.removeEventListener("keydown", onKey);
    }

    const picker = { open, close, render, isOpen: () => !popup.hidden };
    button.addEventListener("click", () => (picker.isOpen() ? close() : open()));
    return picker;
}
