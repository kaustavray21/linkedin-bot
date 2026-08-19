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

async function testGenerateDraftClonesTheChosenPost() {
    const calls = [];
    const { app, el, sel } = boot({
        remixPost: async (...args) => {
            calls.push(args);
            // Mirrors RemixResponse, nulls included — the real endpoint always
            // sends every key.
            return { text: 'cloned', full_text: 'cloned', hashtags: [], notes: [],
                     image_url: null, image_style_note: null,
                     exemplar_id: 5, exemplar_url: null, exemplar_author: null,
                     similarity_jaccard: null, similarity_longest_run: null,
                     similarity_band: null };
        },
        generateText: async () => { throw new Error('the plain path must not run'); },
        generateStyledImage: async () => ({ image_url: null }),
        listPostTypes: async () => [{ slug: 'story', label: 'Story' }],
    });

    select(sel('exemplar-picker'), POSTS[0]);
    el('ai-text-prompt').value = 'shipping';
    el('create-para-count').value = '3';
    el('create-post-type').value = 'story';

    el('btn-generate-text').dispatchEvent({ type: 'click' });
    await settle(); await settle(); await settle();

    equal('the exemplar path was used', calls.length, 1);
    const [topic, exemplarId, notes, withImage, numParagraphs, postType] = calls[0];
    equal('the topic is passed', topic, 'shipping');
    equal('the chosen exemplar is passed', exemplarId, 5);
    check('the image is deferred to stage two', withImage === false);
    equal('the paragraph count is passed', numParagraphs, 3);
    equal('the chosen post type is passed', postType, 'story');
}

async function testWithoutAnExemplarThePlainPathStillWorks() {
    let plain = 0;
    const { el } = boot({
        generateText: async () => { plain += 1; return { content: 'a plain draft' }; },
        remixPost: async () => { throw new Error('the exemplar path must not run'); },
    });

    el('ai-text-prompt').value = 'shipping';
    el('btn-generate-text').dispatchEvent({ type: 'click' });
    await settle(); await settle();

    // Discovery can legitimately have found nothing yet, so this path stays.
    equal('the plain generator ran', plain, 1);
}

async function testTheTypeControlAppearsWithTheExemplar() {
    const { app, el, sel } = boot({ listPostTypes: async () => [{ slug: 'story', label: 'Story' }] });
    const group = el('post-type-group');

    // The stub has no markup, so the starting class comes from the app rather
    // than from index.html — establish it the way startNewPost() does.
    app.setExemplar(null);
    check('hidden with no exemplar', group.hidden);
    select(sel('exemplar-picker'), POSTS[0]);
    check('shown once an exemplar is chosen', !group.hidden);

    app.setExemplar(null);
    check('hidden again when cleared', group.hidden);
}

// ------------------------------------------------- where a generation lands --

const DRAFT_RESULT = {
    text: 'cloned', full_text: 'cloned', hashtags: [], notes: [],
    image_url: null, image_style_note: null,
    exemplar_id: 5, exemplar_url: null, exemplar_author: null,
    similarity_jaccard: null, similarity_longest_run: null, similarity_band: null,
};

function landingBoot() {
    return boot({
        remixPost: async () => DRAFT_RESULT,
        generateText: async () => ({ content: 'a plain draft' }),
        generateStyledImage: async () => ({ image_url: null }),
        listPostTypes: async () => [],
    });
}

async function testTheExemplarPathLandsOnTheDraft() {
    const { app, el, sel } = landingBoot();
    select(sel('exemplar-picker'), POSTS[0]);
    el('ai-text-prompt').value = 'shipping';

    el('btn-generate-text').dispatchEvent({ type: 'click' });
    await settle(); await settle(); await settle();

    equal('exemplar path lands on the body section', sel('create-sections').active, 'body');
    check('exemplar path shows the editor', el('create-launcher').hidden);
}

async function testThePlainPathAlsoLandsOnTheDraft() {
    // The gap this was written for: the plain generator wrote its text into a
    // body section the user was never moved to, so a generation that worked
    // looked like one that did nothing.
    const { app, el, sel } = landingBoot();
    el('ai-text-prompt').value = 'shipping';

    el('btn-generate-text').dispatchEvent({ type: 'click' });
    await settle(); await settle();

    equal('plain path lands on the body section', sel('create-sections').active, 'body');
    check('plain path shows the editor', el('create-launcher').hidden);
}

async function testAHandoffFromDiscoveryLandsOnTheDraft() {
    const { app, el, sel } = landingBoot();
    await app.runStagedHandoff(() => Promise.resolve(DRAFT_RESULT));
    await settle(); await settle();

    equal('discovery handoff lands on the body section', sel('create-sections').active, 'body');
    check('discovery handoff shows the editor', el('create-launcher').hidden);
}

// ------------------------------------------------- library collapse memory --

async function testCollapsingTheLibraryIsRemembered() {
    // The largest width lever the editor has is 242px of library. Having to
    // reclaim it after every reload is why it goes unused.
    const { app, storage } = boot();

    app.toggleLibrary(false);
    equal('collapsing is written down', storage.get('library_collapsed'), '1');

    app.toggleLibrary(true);
    equal('reopening is written down too', storage.get('library_collapsed'), '0');
}

async function testAStoredCollapseIsAppliedOnBoot() {
    const { app, el, sel } = bootApp(APP_JS, {
        listDrafts: async () => [], listPosts: async () => [],
        listDiscoveredPosts: async () => [],
    }, editorComponents(), { library_collapsed: '1' });

    check('the library starts collapsed', sel('draft-library').hidden);
    check('the workspace reserves no column for it',
          sel('.create-workspace').classList.contains('library-hidden'));
}

// ----------------------------------------------------------- deep think --

async function testDeepThinkRunsBeforeAnythingIsWritten() {
    const order = [];
    const { app, el, sel } = boot({
        researchTopic: async () => {
            order.push('research');
            return { ok: true, notes: '- a finding [1]', sources: [], pages_read: 2 };
        },
        remixPost: async (...args) => {
            order.push('generate');
            return { ...DRAFT_RESULT, research: args[6] };
        },
        generateText: async () => { order.push('generate'); return { content: 'x' }; },
        generateStyledImage: async () => ({ image_url: null }),
        listPostTypes: async () => [],
    });

    app.setDeepThink(true);
    el('ai-text-prompt').value = 'kubernetes';
    el('btn-generate-text').dispatchEvent({ type: 'click' });
    await settle(); await settle(); await settle();

    // Findings must be on screen before a word is written, so they can be judged.
    equal('research runs before generation', order.join(','), 'research,generate');
}

async function testDeepThinkOffSkipsResearchEntirely() {
    let researched = 0;
    const { el } = boot({
        researchTopic: async () => { researched += 1; return { ok: true, notes: 'x' }; },
        generateText: async () => ({ content: 'a plain draft' }),
    });

    el('ai-text-prompt').value = 'kubernetes';
    el('btn-generate-text').dispatchEvent({ type: 'click' });
    await settle(); await settle();

    equal('no research when the toggle is off', researched, 0);
}

async function testFindingsThatFailAreShownNotSwallowed() {
    const { app, el } = boot({
        researchTopic: async () => ({ ok: false, reason: 'the sources did not address this topic' }),
        generateText: async () => ({ content: 'a plain draft' }),
    });

    app.setDeepThink(true);
    el('ai-text-prompt').value = 'kubernetes';
    el('btn-generate-text').dispatchEvent({ type: 'click' });
    await settle(); await settle();

    // A draft written without findings must not look like one written with them.
    check('the empty result is rendered', !el('research-notes').hidden);
    check('and it says why', el('research-notes').innerHTML.includes('did not address'));
}

async function testTurningItOffClearsPriorFindings() {
    const { app, el } = boot();
    app.setDeepThink(true);
    app.setResearch({ ok: true, notes: '- old finding [1]', sources: [], pages_read: 1 });
    check('findings are shown', !el('research-notes').hidden);

    app.setDeepThink(false);
    // Otherwise a later draft silently inherits research it never ran.
    check('turning it off clears them', el('research-notes').hidden);
    equal('and drops the held notes', app.research, null);
}

const run = async () => {
    await testSelectingAnExemplarBindsItToTheDraft();
    await testTheSelectionMakesTheDraftDirty();
    await testClearingRemovesIt();
    await testTheSelectionSurvivesARoundTrip();
    await testThePickerIsToldAboutEveryExemplarChange();
    await testOpeningTheBrowserLoadsTheOptions();
    await testGenerateDraftClonesTheChosenPost();
    await testWithoutAnExemplarThePlainPathStillWorks();
    await testTheTypeControlAppearsWithTheExemplar();
    await testTheExemplarPathLandsOnTheDraft();
    await testThePlainPathAlsoLandsOnTheDraft();
    await testAHandoffFromDiscoveryLandsOnTheDraft();
    await testCollapsingTheLibraryIsRemembered();
    await testAStoredCollapseIsAppliedOnBoot();
    await testDeepThinkRunsBeforeAnythingIsWritten();
    await testDeepThinkOffSkipsResearchEntirely();
    await testFindingsThatFailAreShownNotSwallowed();
    await testTurningItOffClearsPriorFindings();
    report('P4 exemplar picker');
};

run();
