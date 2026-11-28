/* datepicker.js — small translated calendar popup for a text date field.
 * Replaces the browser's native picker, which always uses the browser language.
 * Depends on i18n.js (currentLang, LOCALES, t, formatDate).
 *
 * Keyboard (day grid, as in the WAI-ARIA date picker dialog pattern):
 *   ← / →            previous / next day          ↑ / ↓        same weekday, previous / next week
 *   Home / End       first / last day of the week  PageUp / PageDown   previous / next month
 *   Shift+PageUp/Dn  previous / next year          Enter / Space       choose the focused day
 *   Escape           close, focus back on the calendar button
 *   Alt+↓ in the date field opens the calendar.
 * Only the focused day is in the tab order (roving tabindex); moving past the shown month
 * switches the month. Days after `maxIso` cannot be focused: focus stops at the last allowed day.
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

const fromIso = (iso) => new Date(iso + "T00:00:00");
const daysInMonth = (year, month) => new Date(year, month + 1, 0).getDate();

/** Same day of the month `delta` months later; the 31st becomes the last day of a shorter month. */
function addMonths(date, delta) {
    const year = date.getFullYear();
    const month = date.getMonth() + delta;
    return new Date(year, month, Math.min(date.getDate(), daysInMonth(year, month)));
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
    button.setAttribute("aria-haspopup", "dialog");
    button.setAttribute("aria-expanded", "false");

    let view;           // first day of the displayed month
    let focusIso;       // day that has the keyboard focus in the day grid (always in `view`'s month)
    let mode = "days";  // "days" | "months" | "years"
    const YEARS_PER_PAGE = 12;

    /** Latest allowed day, or null. */
    const max = () => (maxIso ? maxIso() : null);
    const clampToMax = (iso) => (max() && iso > max() ? max() : iso);

    function button_(className, label, text, onClick, disabled = false, role = null) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = className;
        if (label) b.setAttribute("aria-label", label);
        if (role) b.dataset.dpRole = role;
        b.textContent = text;
        b.disabled = disabled;
        b.addEventListener("click", onClick);
        return b;
    }

    function header(titleText, titleLabel, onTitle, prevLabel, onPrev, nextLabel, onNext, nextIso) {
        const h = document.createElement("div");
        h.className = "dp-header";
        const title = onTitle
            ? button_("dp-title dp-title-btn", titleLabel, titleText, onTitle, false, "title")
            : Object.assign(document.createElement("span"), { className: "dp-title", textContent: titleText });
        h.append(
            button_("icon-btn dp-nav", prevLabel, "‹", onPrev, false, "prev"),
            title,
            // no browsing into the future: "›" is off once the next period starts after max
            button_("icon-btn dp-nav", nextLabel, "›", onNext, Boolean(max() && nextIso > max()), "next"),
        );
        return h;
    }

    /** Re-draws the popup. If the focus was inside, it is put back on the same control
     *  (e.g. "‹" after a mouse click) or, if that is gone, on the focused day / cell. */
    function render() {
        const active = document.activeElement;
        const hadFocus = popup.contains(active);
        const role = hadFocus ? active.dataset.dpRole : null;

        popup.setAttribute("aria-label", t("calendar"));
        if (mode === "months") renderMonths();
        else if (mode === "years") renderYears();
        else renderDays();

        if (!hadFocus) return;
        const target = (role && popup.querySelector(`[data-dp-role="${role}"]:not(:disabled)`))
            || (mode === "days" ? dayButton(focusIso) : null)
            || popup.querySelector(".dp-cell.selected:not(:disabled)")
            || popup.querySelector(".dp-cell:not(:disabled)");
        target?.focus();
    }

    const dayButton = (iso) => popup.querySelector(`.dp-day[data-iso="${iso}"]`);

    /** Shows the month `date` is in, keeping the focused day (clamped to that month and to max). */
    function showMonth(date) {
        view = new Date(date.getFullYear(), date.getMonth(), 1);
        const day = Math.min(fromIso(focusIso).getDate(), daysInMonth(view.getFullYear(), view.getMonth()));
        focusIso = clampToMax(toIso(new Date(view.getFullYear(), view.getMonth(), day)));
        if (focusIso.slice(0, 7) !== toIso(view).slice(0, 7)) view = new Date(fromIso(focusIso).setDate(1));
    }

    /** Month grid for the displayed year; click the year to pick another year. */
    function renderMonths() {
        const locale = LOCALES[currentLang];
        const year = view.getFullYear();
        const selected = parseDate(input.value);
        const monthName = new Intl.DateTimeFormat(locale, { month: "short" });
        const monthLabel = new Intl.DateTimeFormat(locale, { month: "long", year: "numeric" });

        const grid = document.createElement("div");
        grid.className = "dp-grid dp-grid-wide";
        for (let m = 0; m < 12; m++) {
            const first = new Date(year, m, 1);
            const classes = ["dp-cell"];
            if (selected && selected.slice(0, 7) === toIso(first).slice(0, 7)) classes.push("selected");
            grid.append(button_(classes.join(" "), monthLabel.format(first),
                monthName.format(first).replace(".", ""),
                () => { showMonth(first); mode = "days"; render(); },
                Boolean(max() && toIso(first) > max())));
        }
        popup.replaceChildren(
            header(String(year), t("chooseYear"), () => { mode = "years"; render(); },
                t("prevYear"), () => shiftView(-12), t("nextYear"), () => shiftView(12), `${year + 1}-01-01`),
            grid,
        );
    }

    /** Page of 12 years. */
    function renderYears() {
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
                Boolean(max() && `${y}-01-01` > max())));
        }
        popup.replaceChildren(
            header(`${start} – ${start + YEARS_PER_PAGE - 1}`, null, null,
                t("prevYears"), () => shiftView(-12 * YEARS_PER_PAGE),
                t("nextYears"), () => shiftView(12 * YEARS_PER_PAGE), `${start + YEARS_PER_PAGE}-01-01`),
            grid,
        );
    }

    function renderDays() {
        const locale = LOCALES[currentLang];
        const selected = parseDate(input.value);
        const today = toIso(new Date());
        const fullDate = new Intl.DateTimeFormat(locale, { weekday: "long", day: "numeric", month: "long", year: "numeric" });

        const head = header(
            new Intl.DateTimeFormat(locale, { month: "long", year: "numeric" }).format(view),
            t("chooseMonth"), () => { mode = "months"; render(); },
            t("prevMonth"), () => shiftView(-1),
            t("nextMonth"), () => shiftView(1),
            toIso(new Date(view.getFullYear(), view.getMonth() + 1, 1)),
        );

        const grid = document.createElement("div");
        grid.className = "dp-grid";
        // weekday header, Monday first (2024-01-01 was a Monday)
        const weekday = new Intl.DateTimeFormat(locale, { weekday: "short" });
        for (let i = 0; i < 7; i++) {
            const cell = document.createElement("span");
            cell.className = "dp-weekday";
            cell.setAttribute("aria-hidden", "true");
            cell.textContent = weekday.format(new Date(2024, 0, 1 + i)).replace(".", "");
            grid.append(cell);
        }
        const offset = (view.getDay() + 6) % 7;   // Monday = 0
        for (let i = 0; i < offset; i++) grid.append(document.createElement("span"));

        for (let day = 1; day <= daysInMonth(view.getFullYear(), view.getMonth()); day++) {
            const date = new Date(view.getFullYear(), view.getMonth(), day);
            const iso = toIso(date);
            const classes = ["dp-day"];
            if (iso === today) classes.push("today");
            if (iso === selected) classes.push("selected");
            const b = button_(classes.join(" "), fullDate.format(date), String(day), () => pick(iso),
                Boolean(max() && iso > max()));
            b.dataset.iso = iso;
            b.tabIndex = iso === focusIso ? 0 : -1;
            if (iso === selected) b.setAttribute("aria-pressed", "true");
            if (iso === today) b.setAttribute("aria-current", "date");
            grid.append(b);
        }

        const footer = document.createElement("div");
        footer.className = "dp-footer";
        footer.append(button_("secondary-btn small", null, t("today"), () => pick(today)));

        popup.replaceChildren(head, grid, footer);
    }

    /** "‹" / "›": the previous / next month (or year, or page of years). */
    function shiftView(deltaMonths) {
        const target = new Date(view.getFullYear(), view.getMonth() + deltaMonths, 1);
        if (mode === "days") showMonth(target);
        else view = target;
        render();
    }

    /** Keyboard: focus another day, switching the month if it lies outside the shown one. */
    function moveFocus(date) {
        focusIso = clampToMax(toIso(date));
        if (focusIso.slice(0, 7) !== toIso(view).slice(0, 7)) view = new Date(fromIso(focusIso).setDate(1));
        renderDays();
        dayButton(focusIso)?.focus();
    }

    popup.addEventListener("keydown", (event) => {
        if (mode !== "days" || !event.target.classList.contains("dp-day")) return;
        const d = fromIso(focusIso);
        const weekdayIndex = (d.getDay() + 6) % 7;   // Monday = 0
        const byDays = (n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
        const target = {
            ArrowLeft: () => byDays(-1),
            ArrowRight: () => byDays(1),
            ArrowUp: () => byDays(-7),
            ArrowDown: () => byDays(7),
            Home: () => byDays(-weekdayIndex),
            End: () => byDays(6 - weekdayIndex),
            PageUp: () => addMonths(d, event.shiftKey ? -12 : -1),
            PageDown: () => addMonths(d, event.shiftKey ? 12 : 1),
        }[event.key];
        if (!target) return;   // Enter / Space: the day button's own click picks it
        event.preventDefault();
        event.stopPropagation();
        moveFocus(target());
    });

    // the day under the mouse becomes the keyboard position too
    popup.addEventListener("focusin", (event) => {
        const iso = event.target.dataset?.iso;
        if (!iso || iso === focusIso) return;
        dayButton(focusIso)?.setAttribute("tabindex", "-1");
        focusIso = iso;
        event.target.tabIndex = 0;
    });

    // Tab out of the calendar closes it (a re-render also moves the focus, but to nowhere)
    popup.addEventListener("focusout", (event) => {
        const next = event.relatedTarget;
        if (next && !popup.contains(next) && next !== button) close();
    });

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
        focusIso = clampToMax(parseDate(input.value) || toIso(new Date()));
        view = new Date(fromIso(focusIso).setDate(1));
        mode = "days";
        render();
        popup.hidden = false;
        button.setAttribute("aria-expanded", "true");
        openPickers.add(picker);
        document.addEventListener("mousedown", onOutside);
        document.addEventListener("keydown", onKey);
        dayButton(focusIso)?.focus();
    }

    function close() {
        popup.hidden = true;
        button.setAttribute("aria-expanded", "false");
        openPickers.delete(picker);
        document.removeEventListener("mousedown", onOutside);
        document.removeEventListener("keydown", onKey);
    }

    input.addEventListener("keydown", (event) => {
        if (event.altKey && event.key === "ArrowDown") {
            event.preventDefault();
            open();
        }
    });

    const picker = { open, close, render, isOpen: () => !popup.hidden };
    button.addEventListener("click", () => (picker.isOpen() ? close() : open()));
    return picker;
}
