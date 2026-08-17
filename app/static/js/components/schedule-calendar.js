/**
 * app/static/js/components/schedule-calendar.js
 *
 * <schedule-calendar> — month view of scheduled posts.
 *
 * There was no calendar before this, only a `datetime-local` input, so this is
 * a build rather than a restyle. Read-only by design: clicking a day filters
 * the list beside it. Drag-to-reschedule is a materially larger feature and is
 * not pretended at here — a grid that looks draggable but is not is worse than
 * one that plainly is not.
 *
 * Dates arrive as naive UTC (the driver drops tzinfo on write), so every value
 * is read as UTC and rendered in the viewer's local zone. Doing it the other
 * way round silently shifts a post by the offset — and in the wrong direction
 * near midnight, which is exactly when scheduling matters.
 */

import { Component, html, raw } from './base.js';

// Nested html`` inside html`` gets escaped twice — the inner call returns a
// string and the outer treats it as untrusted. Second time this has bitten in
// this codebase; markup composed into a template must be marked raw().
const dot = (n) => raw(`<span class="cal-dot">${Number(n)}</span>`);

const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function asLocalDate(value) {
    if (!value) return null;
    return new Date(value.endsWith('Z') ? value : `${value}Z`);
}

function ymd(date) {
    const pad = (n) => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export class ScheduleCalendar extends Component {
    constructor() {
        super();
        const now = new Date();
        this.year = now.getFullYear();
        this.month = now.getMonth();
        this.posts = [];
        this.selected = null;
    }

    template() {
        return `
            <div class="cal-head">
                <button type="button" class="btn-icon" data-move="-1" aria-label="Previous month">
                    <i class="fa-solid fa-chevron-left"></i>
                </button>
                <div class="cal-title">
                    <select class="cal-select" data-month></select>
                    <select class="cal-select" data-year></select>
                </div>
                <button type="button" class="btn-icon" data-move="1" aria-label="Next month">
                    <i class="fa-solid fa-chevron-right"></i>
                </button>
                <button type="button" class="btn btn-secondary btn-sm" data-today>Today</button>
            </div>
            <div class="cal-grid" data-grid></div>
            <div class="cal-day-list" data-daylist></div>
        `;
    }

    mounted() {
        this.on('click', '[data-move]', (_e, el) => this.shift(Number(el.dataset.move)));
        this.on('click', '[data-today]', () => {
            const now = new Date();
            this.year = now.getFullYear();
            this.month = now.getMonth();
            this.selected = ymd(now);
            this.draw();
        });
        this.on('click', '[data-day]', (_e, el) => {
            this.selected = this.selected === el.dataset.day ? null : el.dataset.day;
            this.draw();
        });
        this.$('[data-month]').addEventListener('change', (e) => {
            this.month = Number(e.target.value);
            this.draw();
        });
        this.$('[data-year]').addEventListener('change', (e) => {
            this.year = Number(e.target.value);
            this.draw();
        });
        this.draw();
    }

    setPosts(posts) {
        this.posts = (posts || []).filter(p => p.scheduled_time);
        this.draw();
    }

    shift(delta) {
        const d = new Date(this.year, this.month + delta, 1);
        this.year = d.getFullYear();
        this.month = d.getMonth();
        this.draw();
    }

    byDay() {
        const map = new Map();
        this.posts.forEach((post) => {
            const when = asLocalDate(post.scheduled_time);
            if (!when) return;
            const key = ymd(when);
            if (!map.has(key)) map.set(key, []);
            map.get(key).push({ post, when });
        });
        return map;
    }

    draw() {
        this.drawSelectors();
        this.drawGrid();
        this.drawDayList();
    }

    drawSelectors() {
        const months = Array.from({ length: 12 }, (_, i) =>
            new Date(2000, i, 1).toLocaleString(undefined, { month: 'long' }));
        this.$('[data-month]').innerHTML = months.map((name, i) =>
            html`<option value="${i}" ${i === this.month ? 'selected' : ''}>${name}</option>`).join('');

        const current = new Date().getFullYear();
        const years = Array.from({ length: 7 }, (_, i) => current - 2 + i);
        if (!years.includes(this.year)) years.push(this.year);
        this.$('[data-year]').innerHTML = years.sort().map(y =>
            html`<option value="${y}" ${y === this.year ? 'selected' : ''}>${y}</option>`).join('');
    }

    drawGrid() {
        const first = new Date(this.year, this.month, 1);
        // getDay() is Sunday-first; the grid starts on Monday.
        const lead = (first.getDay() + 6) % 7;
        const days = new Date(this.year, this.month + 1, 0).getDate();
        const scheduled = this.byDay();
        const today = ymd(new Date());

        const cells = DAY_LABELS.map(d => html`<div class="cal-dow">${d}</div>`);
        for (let i = 0; i < lead; i++) cells.push('<div class="cal-cell cal-blank"></div>');

        for (let day = 1; day <= days; day++) {
            const key = ymd(new Date(this.year, this.month, day));
            const items = scheduled.get(key) || [];
            const classes = ['cal-cell'];
            if (key === today) classes.push('cal-today');
            if (key === this.selected) classes.push('cal-selected');
            if (items.length) classes.push('cal-has');

            cells.push(html`
                <button type="button" class="${classes.join(' ')}" data-day="${key}">
                    <span class="cal-num">${day}</span>
                    ${items.length ? dot(items.length) : ''}
                </button>
            `);
        }
        this.$('[data-grid]').innerHTML = cells.join('');
    }

    drawDayList() {
        const box = this.$('[data-daylist]');
        const scheduled = this.byDay();

        // With no day selected, show what is coming rather than nothing — an
        // empty panel below a full calendar reads as broken.
        const entries = this.selected
            ? (scheduled.get(this.selected) || [])
            : [...scheduled.entries()]
                .filter(([key]) => key >= ymd(new Date()))
                .sort()
                .flatMap(([, items]) => items)
                .slice(0, 5);

        if (!entries.length) {
            box.innerHTML = html`<p class="cal-empty">${this.selected
                ? 'Nothing scheduled for this day.'
                : 'Nothing scheduled yet.'}</p>`;
            return;
        }

        box.innerHTML =
            html`<span class="cal-list-label">${this.selected ? 'That day' : 'Coming up'}</span>`
            + entries.sort((a, b) => a.when - b.when).map(({ post, when }) => html`
                <div class="cal-item">
                    <span class="cal-time">${when.toLocaleString(undefined, {
                        day: 'numeric', month: 'short',
                        hour: 'numeric', minute: '2-digit',
                    })}</span>
                    <span class="cal-text">${(post.content || '').slice(0, 70)}</span>
                </div>
            `).join('');
    }
}

customElements.define('schedule-calendar', ScheduleCalendar);
