/**
 * P4 (①) — the discovery picker in Create Post.
 *
 * The picker replaced the reference-file UI deleted with the reference
 * subsystem. What matters here is not the rendering but the ownership split:
 * the component displays the choice, while the exemplar itself stays on `app`
 * so serialize()/hydrate() carry it. A picker holding that state privately
 * would drop the selection on every autosave and every draft switch — which is
 * the bug this suite exists to prevent.
 *
 * Run: node tests/js/test_exemplar_picker.mjs
 */

import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { bootApp, editorComponents, check, equal, report } from './dom_stub.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const APP_JS = path.join(here, '..', '..', 'app', 'static', 'js', 'app.js');

const settle = () => new Promise(r => setTimeout(r, 0));

const POSTS = [
    {
        id: 5, post_url: 'https://example.invalid/a', author_name: 'Dana Lin',
        content_text: 'A readable post about shipping.', has_content: true,
        purged_at: null, post_type_slug: 'story', reactions: 12, comments: 3,
    },
    {
        id: 9, post_url: 'https://example.invalid/b', author_name: 'Sam Reed',
        content_text: 'Another readable post.', has_content: true,
        purged_at: null, post_type_slug: null, reactions: null, comments: null,
    },
];

function boot(api = {}) {
    return bootApp(APP_JS, {
        listDrafts: async () => [],
        listPosts: async () => [],
        listDiscoveredPosts: async () => POSTS,
        ...api,
    }, editorComponents());
}

function select(picker, post) {
    // What the component emits: a CustomEvent carrying the whole post.
    picker._listeners['exemplar-select'].forEach(fn => fn({ detail: { post } }));
}

async function testSelectingAnExemplarBindsItToTheDraft() {
    const { app, sel } = boot();
    select(sel('exemplar-picker'), POSTS[0]);

    equal('the chosen post becomes the exemplar', app.exemplarId, 5);
    equal('its URL is carried', app.exemplarUrl, 'https://example.invalid/a');
    equal('its author is carried', app.exemplarAuthor, 'Dana Lin');
}

async function testTheSelectionMakesTheDraftDirty() {
    const { app, sel } = boot();
    await settle();
    app.lastSaved = JSON.stringify(app.serialize());
    check('a freshly-baselined draft is clean', !app.isDirty());

    select(sel('exemplar-picker'), POSTS[0]);

    // No explicit dirty flag exists — exemplarId is in serialize(), so choosing
    // one has to move the comparison by itself or autosave would skip it.
    check('choosing an exemplar marks the draft dirty', app.isDirty());
}

async function testClearingRemovesIt() {
    const { app, sel } = boot();
    const picker = sel('exemplar-picker');
    select(picker, POSTS[0]);
    equal('selected first', app.exemplarId, 5);

    picker._listeners['exemplar-clear'].forEach(fn => fn({}));
    equal('cleared', app.exemplarId, null);
    equal('the URL goes with it', app.exemplarUrl, null);
    equal('and the author', app.exemplarAuthor, null);
}

async function testTheSelectionSurvivesARoundTrip() {
    const { app, sel } = boot();
    select(sel('exemplar-picker'), POSTS[1]);

    const saved = JSON.parse(JSON.stringify(app.serialize()));
    app.setExemplar(null);
    equal('cleared before rehydrating', app.exemplarId, null);

    app.hydrate(saved);
    equal('hydrate restores the exemplar', app.exemplarId, 9);
    equal('and its author', app.exemplarAuthor, 'Sam Reed');
}

async function testThePickerIsToldAboutEveryExemplarChange() {
    const { app, sel } = boot();
    const picker = sel('exemplar-picker');
    picker.selections.length = 0;

    // A remix from the Discover tab sets the exemplar without touching the
    // picker directly; the picker still has to show it.
    app.setExemplar(5, 'https://example.invalid/a', 'Dana Lin');
    const last = picker.selections[picker.selections.length - 1];
    equal('the picker is updated from setExemplar, not only from clicks', last.id, 5);
}

async function testOpeningTheBrowserLoadsTheOptions() {
    let calls = 0;
    const { sel } = boot({
        listDiscoveredPosts: async (keyword, sort, includePurged) => {
            calls += 1;
            check('history is requested, not just this search', includePurged === true);
            return POSTS;
        },
    });

    const picker = sel('exemplar-picker');
    check('nothing is fetched before the picker is opened', picker.posts === null);

    picker._listeners['exemplar-browse'].forEach(fn => fn({}));
    await settle();

    equal('opening the browser fetches once', calls, 1);
    equal('the options reach the picker', picker.posts.length, 2);
}

const run = async () => {
    await testSelectingAnExemplarBindsItToTheDraft();
    await testTheSelectionMakesTheDraftDirty();
    await testClearingRemovesIt();
    await testTheSelectionSurvivesARoundTrip();
    await testThePickerIsToldAboutEveryExemplarChange();
    await testOpeningTheBrowserLoadsTheOptions();
    report('P4 exemplar picker');
};

run();
