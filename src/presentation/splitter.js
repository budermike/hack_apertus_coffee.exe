/* splitter.js — draggable divider between the result list and the case detail.
 * Depends on i18n.js (t).
 *
 * The detail pane's width is the CSS variable --detail-width on the split container.
 * style.css clamps it as well (.split.has-detail), so the layout stays valid when the
 * window shrinks; without the variable the default width applies. The pane can grow
 * over the result list (which keeps its own width underneath, class "detail-over-list"),
 * but only as far as its content still gets wider: the text column (#detail) has a
 * max-width, so beyond it the summary boxes would not grow (cap(), --detail-cap), and
 * never closer than SPLIT_EDGE px to the left edge. Keyboard: arrow keys move the divider (Shift: bigger
 * steps), Home / End give the detail pane its smallest / largest width; double-click
 * resets it.
 */

const SPLIT_LIST_MIN = 540;     // px the list keeps; a wider detail pane covers it (style.css)
const SPLIT_DETAIL_MIN = 420;   // px the detail pane keeps at least (same as style.css)
const SPLIT_EDGE = 32;          // px of the list that stay visible at the largest width (style.css)
const SPLIT_KEY_STEP = 32;      // px per arrow key press

function createSplitter(split, handle, pane) {
    let drag = null;   // { startX, startWidth, moved } while the pointer is down
    let frame = 0;
    let lastX = 0;

    /** Widest useful pane: the text column's max-width plus the pane's scrollbar gutter. */
    function cap() {
        const maxWidth = parseFloat(getComputedStyle(pane.firstElementChild).maxWidth);
        return Number.isFinite(maxWidth) ? maxWidth + (pane.offsetWidth - pane.clientWidth) : Infinity;
    }

    function bounds() {
        const width = split.getBoundingClientRect().width - handle.getBoundingClientRect().width;
        const max = Math.min(width - SPLIT_EDGE, cap());
        return { width, min: SPLIT_DETAIL_MIN, max: Math.max(SPLIT_DETAIL_MIN, max) };
    }

    function setWidth(px) {
        const { min, max } = bounds();
        split.style.setProperty("--detail-width", `${Math.round(Math.min(max, Math.max(min, px)))}px`);
        refresh();
    }

    /** Back to the default width (when the panel closes, or on double-click). */
    function reset() {
        split.style.removeProperty("--detail-width");
        refresh();
    }

    /** Keeps the separator's ARIA values in step with the real width (in % of the split). */
    function refresh() {
        if (handle.hidden) {
            split.classList.remove("detail-over-list");
            return;
        }
        const { width, min, max } = bounds();
        if (width <= 0) return;
        if (Number.isFinite(cap())) split.style.setProperty("--detail-cap", `${cap()}px`);
        const pct = (px) => Math.round((px / width) * 100);
        const paneWidth = pane.getBoundingClientRect().width;
        split.classList.toggle("detail-over-list", paneWidth > width - SPLIT_LIST_MIN + 0.5);
        const now = pct(paneWidth);
        handle.setAttribute("aria-valuemin", String(pct(min)));
        handle.setAttribute("aria-valuemax", String(pct(max)));
        handle.setAttribute("aria-valuenow", String(now));
        handle.setAttribute("aria-valuetext", t("resizeValue", now));
    }

    /** A drag that ends over a result row must not open that case. */
    function swallowNextClick() {
        const swallow = (e) => { e.stopPropagation(); e.preventDefault(); };
        window.addEventListener("click", swallow, { capture: true, once: true });
        setTimeout(() => window.removeEventListener("click", swallow, { capture: true }), 0);
    }

    handle.addEventListener("pointerdown", (e) => {
        if (e.button !== 0) return;
        e.preventDefault();   // no text selection while dragging
        handle.setPointerCapture(e.pointerId);
        drag = { startX: e.clientX, startWidth: pane.getBoundingClientRect().width, moved: false };
        document.body.classList.add("resizing");
    });

    handle.addEventListener("pointermove", (e) => {
        if (!drag) return;
        lastX = e.clientX;
        if (Math.abs(lastX - drag.startX) > 2) drag.moved = true;
        if (frame) return;
        frame = requestAnimationFrame(() => {   // at most one layout per frame
            frame = 0;
            if (drag) setWidth(drag.startWidth - (lastX - drag.startX));
        });
    });

    function endDrag(e) {
        if (!drag) return;
        if (drag.moved) swallowNextClick();
        drag = null;
        document.body.classList.remove("resizing");
        if (handle.hasPointerCapture(e.pointerId)) handle.releasePointerCapture(e.pointerId);
    }
    handle.addEventListener("pointerup", endDrag);
    handle.addEventListener("pointercancel", endDrag);
    handle.addEventListener("lostpointercapture", endDrag);

    handle.addEventListener("dblclick", reset);

    handle.addEventListener("keydown", (e) => {
        const current = pane.getBoundingClientRect().width;
        const step = e.shiftKey ? SPLIT_KEY_STEP * 4 : SPLIT_KEY_STEP;
        const { min, max } = bounds();
        const target = {
            ArrowLeft: current + step,    // divider moves left: the detail pane grows
            ArrowRight: current - step,
            Home: min,
            End: max,
        }[e.key];
        if (target === undefined) return;
        e.preventDefault();
        setWidth(target);
    });

    new ResizeObserver(refresh).observe(split);

    return { reset, refresh };
}
