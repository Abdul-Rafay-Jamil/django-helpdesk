import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import AxeBuilder from '@axe-core/playwright';
import lighthouse from 'lighthouse';
import desktopConfig from 'lighthouse/core/config/desktop-config.js';
import { chromium } from 'playwright';

const categories = ['accessibility', 'best-practices', 'seo'];
process.stdin.setEncoding('utf8');
let input = '';
for await (const chunk of process.stdin) input += chunk;
const { executablePath, screens, output } = JSON.parse(input);
const server = createServer();
const port = await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
        const { port } = server.address();
        server.close(() => resolve(port));
    });
});
const profile = await mkdtemp(join(tmpdir(), 'sqe-chromium-'));
let context;

async function scanAxe(page, name) {
    const result = await new AxeBuilder({ page }).analyze();
    await writeFile(join(output, name + '.axe.json'), JSON.stringify(result, null, 2));
    return result.violations.length;
}

try {
    context = await chromium.launchPersistentContext(profile, {
        executablePath,
        locale: 'en-US',
        viewport: { width: 1350, height: 940 },
        args: ['--remote-debugging-address=127.0.0.1', '--remote-debugging-port=' + port],
    });
    const page = context.pages()[0];
    page.setDefaultTimeout(8000);
    const failures = [];
    for (const { name, url, selector, button, respond, cookie } of screens) {
      try {
        await context.clearCookies();
        if (cookie) await context.addCookies([cookie]);
        const response = await page.goto(url);
        assert.equal(response.status(), 200);
        assert.equal(page.url(), url);
        const marker = button ? page.getByRole('button', { name: button, exact: true }) : page.locator(selector);
        await marker.waitFor({ state: 'visible' });
        await page.evaluate(() => document.fonts.ready.then(() => true));
        if (respond) {
            await page.getByRole('tab', { name: 'Respond', exact: true }).click();
            await page.locator('#ticket-respond-form').waitFor({ state: 'visible' });
            await page.waitForFunction(() => {
                const pane = document.querySelector('#respond-tab-pane.show');
                return pane && getComputedStyle(pane).opacity === '1';
            });
        }
        const violations = await scanAxe(page, name);
        await page.screenshot({ path: join(output, name + '.png'), fullPage: true });
        if (respond) {
            console.log(name, 'axe violations:', violations);
            continue;
        }
        const run = await lighthouse(url, {
            hostname: '127.0.0.1',
            port,
            logLevel: 'error',
            onlyCategories: categories,
            disableStorageReset: true,
            output: ['json', 'html'],
        }, desktopConfig);
        assert.ok(!run.lhr.runtimeError, run.lhr.runtimeError?.message);
        assert.equal(run.lhr.finalDisplayedUrl, url);
        for (const category of categories) assert.equal(typeof run.lhr.categories[category].score, 'number');
        await writeFile(join(output, name + '.lighthouse.json'), run.report[0]);
        await writeFile(join(output, name + '.lighthouse.html'), run.report[1]);
        console.log(name, categories.map(id => run.lhr.categories[id].score * 100), 'axe violations:', violations);
      } catch (error) {
        console.error(name, error);
        failures.push(name);
      }
    }
    assert.equal(failures.length, 0, 'Incomplete screens: ' + failures.join(', '));
} finally {
    await context?.close();
    await rm(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 200 });
}
