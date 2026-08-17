/**
 * Runs every frontend test. `node tests/js/run.mjs`
 *
 * Each suite is a separate process because they all boot app.js into their own
 * sandbox, and a suite that crashes should not take the others with it.
 */

import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const suites = fs.readdirSync(here)
    .filter(f => f.startsWith('test_') && f.endsWith('.mjs'))
    .sort();

let failed = 0;
for (const suite of suites) {
    const result = spawnSync(process.execPath, [path.join(here, suite)], { stdio: 'inherit' });
    if (result.status !== 0) failed += 1;
}

if (failed) {
    console.error(`\n${failed} of ${suites.length} suite(s) failed`);
    process.exit(1);
}
console.log(`\n${suites.length} suites passed`);
