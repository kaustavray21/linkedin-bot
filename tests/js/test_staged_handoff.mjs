/**
 * R1 — the staged handoff.
 *
 * The property under test is ORDERING, not output: the editor must be open and
 * showing a drafting state *before* the first request resolves, and the body
 * must be editable *before* the image request resolves. Both were previously
 * true only after everything finished.
 *
 * Run: node tests/js/test_staged_handoff.mjs
 */

import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { bootApp, hashtagEditorStub, check, equal, report } from './dom_stub.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const APP_JS = path.join(here, '..', '..', 'app', 'static', 'js', 'app.js');

// A promise whose resolution the test controls, so it can look at the DOM while
// the request is still in flight.
function deferred() {
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
}

const REMIX_RESULT = {
    text: 'The body of the draft.',
    full_text: 'The body of the draft.\n\n#one #two',
    hashtags: ['#one', '#two'],
    image_url: null,
    exemplar_id: 42,
    exemplar_url: 'https://www.linkedin.com/posts/someone_activity-1234',
    exemplar_author: 'Someone Real',
    similarity_band: 'green',
    similarity_jaccard: 0.12,
    notes: [],
};

const settle = () => new Promise(r => setTimeout(r, 0));

async function testHappyPath() {
    const stage1 = deferred();
    const stage2 = deferred();
    const calls = [];

    const api = {
        remixPost: (...args) => { calls.push(['remixPost', args]); return stage1.promise; },
        generateStyledImage: (...args) => { calls.push(['generateStyledImage', args]); return stage2.promise; },
        listPosts: async () => [],
        getDiscoveryStatus: async () => ({}),
        listDiscoveredPosts: async () => [],
    };
    const tagEditor = hashtagEditorStub();
    const { app, el, sel } = bootApp(APP_JS, api, { '#hashtag-editor': tagEditor });

    // Exactly how the Discover card calls it: with_image=false.
    const running = app.runStagedHandoff(() => api.remixPost('topic', 7, '', false));

    await settle();

    // --- stage 1 in flight -------------------------------------------------
    equal('tab switched before the first response', app.currentTab, 'create');
    check('editor visible while drafting', !sel('.create-main').hidden);
    check('launcher hidden while drafting', el('create-launcher').hidden);
    check('drafting banner visible', !el('draft-staging').hidden);
    equal('banner says it is writing', el('draft-staging-label').textContent, 'Writing the draft…');
    check('body skeleton visible', !el('body-skeleton').hidden);
    check('textarea hidden behind the skeleton', sel('.textarea-container').hidden);
    equal('no image yet', el('generated-image-url').value, '');

    stage1.resolve(REMIX_RESULT);
    await settle();

    // --- stage 1 done, stage 2 in flight -----------------------------------
    equal('body landed', el('post-text-content').value, REMIX_RESULT.text);
    check('skeleton gone once the body lands', el('body-skeleton').hidden);
    check('textarea editable while the image is still coming', !sel('.textarea-container').hidden);
    check('banner still visible for stage 2', !el('draft-staging').hidden);
    equal('banner switched to the image stage', el('draft-staging-label').textContent,
        'Draft ready — generating an image…');
    equal('image has NOT arrived yet', el('generated-image-url').value, '');

    // The bug this replaces: exemplarId was assigned and then immediately
    // nulled, which silently disabled the similarity gate, the reference
    // hashtags and lineage on save.
    equal('exemplar retained', app.exemplarId, 42);
    equal('draft id cleared — a remix is a new post', app.draftId, null);
    check('attribution rendered', el('exemplar-attribution').renderedText.includes('Someone Real'),
        el('exemplar-attribution').renderedText);
    check('attribution visible', !el('exemplar-attribution').hidden);

    stage2.resolve({ image_url: '/static/uploads/generated.png' });
    await running;

    // --- both stages done ---------------------------------------------------
    equal('image applied', el('generated-image-url').value, '/static/uploads/generated.png');
    check('banner cleared', el('draft-staging').hidden);
    check('textarea still visible', !sel('.textarea-container').hidden);

    const remixCall = calls.find(c => c[0] === 'remixPost');
    check('remix asked for no image', remixCall && remixCall[1][3] === false,
        JSON.stringify(remixCall));
    check('image was a separate second call', calls.some(c => c[0] === 'generateStyledImage'));
}

async function testImageFailureLeavesAnEditableDraft() {
    const stage1 = deferred();
    const stage2 = deferred();

    const { app, el, sel } = bootApp(APP_JS, {
        generateStyledImage: () => stage2.promise,
        listPosts: async () => [],
    }, { '#hashtag-editor': hashtagEditorStub() });

    const running = app.runStagedHandoff(() => stage1.promise);
    await settle();
    stage1.resolve(REMIX_RESULT);
    await settle();

    stage2.reject(new Error('fal.ai is down'));
    const result = await running;

    equal('body survived the image failure', el('post-text-content').value, REMIX_RESULT.text);
    check('textarea still editable', !sel('.textarea-container').hidden);
    check('drafting state cleared', el('draft-staging').hidden);
    check('skeleton cleared', el('body-skeleton').hidden);
    equal('no image', el('generated-image-url').value, '');
    check('the draft is still returned — a missing image is a downgrade, not an error',
        result !== null);
    equal('exemplar still retained after an image failure', app.exemplarId, 42);
}

async function testStageOneFailureClearsTheDraftingState() {
    const stage1 = deferred();

    const { app, el, sel } = bootApp(APP_JS, {
        generateStyledImage: async () => { throw new Error('should never be called'); },
        listPosts: async () => [],
    }, { '#hashtag-editor': hashtagEditorStub() });

    const running = app.runStagedHandoff(() => stage1.promise);
    await settle();
    stage1.reject(new Error('Gemini refused'));
    const result = await running;

    equal('nothing returned', result, null);
    check('drafting banner cleared', el('draft-staging').hidden);
    check('skeleton cleared — the editor is not left behind a shimmer',
        el('body-skeleton').hidden);
    check('textarea restored', !sel('.textarea-container').hidden);
}

async function testStartNewPostClearsAHandoffInProgress() {
    const { app, el, sel } = bootApp(APP_JS, { listPosts: async () => [] },
        { '#hashtag-editor': hashtagEditorStub() });

    app.setDraftStage('writing');
    check('precondition: skeleton up', !el('body-skeleton').hidden);

    app.startNewPost();

    check('skeleton cleared by a new post', el('body-skeleton').hidden);
    check('textarea restored by a new post', !sel('.textarea-container').hidden);
    check('attribution cleared', el('exemplar-attribution').hidden);
    equal('exemplar cleared', app.exemplarId, null);
}

await testHappyPath();
await testImageFailureLeavesAnEditableDraft();
await testStageOneFailureClearsTheDraftingState();
await testStartNewPostClearsAHandoffInProgress();
report('R1 staged handoff');
