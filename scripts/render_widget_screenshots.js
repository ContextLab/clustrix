// Drive the configuration widget in a real Jupyter session and photograph it.
//
// Started by scripts/render_widget_screenshots.py, which owns the Jupyter
// server and the notebooks; run that, not this. Usage, for reference:
//
//   node render_widget_screenshots.js <base-url> <out-dir> <with-cluster: 0|1>
//
// Each picture is the output area of the cell that displays the widget, at
// twice the device resolution so it stays sharp when the docs scale it.
// Needs the `playwright` package and its Chromium.

const { chromium } = require('playwright');
const path = require('path');

const [base, outDir, withCluster] = process.argv.slice(2);
const OUT = '.jp-Cell-outputArea';
const WIDTH = 900;

async function restartKernels() {
  // A kernel left over from the previous notebook still holds its widget.
  for (const kind of ['sessions', 'kernels']) {
    const items = await (await fetch(`${base}/api/${kind}`)).json();
    for (const item of items) await fetch(`${base}/api/${kind}/${item.id}`, { method: 'DELETE' });
  }
}

async function open(browser, notebook, scheme) {
  await restartKernels();
  const context = await browser.newContext({
    // Taller than the tallest picture (SSH with Advanced open): Notebook
    // scrolls its own panel, and a widget that does not fit is cut off.
    viewport: { width: WIDTH, height: 2000 },
    deviceScaleFactor: 2,
    colorScheme: scheme,
  });
  const page = await context.newPage();
  await page.goto(`${base}/notebooks/${notebook}`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.jp-Notebook', { timeout: 90000 });
  await page.waitForTimeout(4000);
  for (const button of await page.$$('.jp-Dialog button.jp-mod-accept')) {
    try { await button.click(); } catch (e) { /* the dialog closed by itself */ }
  }
  await page.click('.jp-Cell .cm-content');
  await page.keyboard.press('Control+Enter');
  await page.waitForSelector(`${OUT} .clustrix-widget`, { timeout: 120000 });
  await page.waitForTimeout(2500);

  const widget = {
    page,
    async snap(name) {
      // The pointer is parked so no button is photographed in its hover state.
      await page.mouse.move(2, 2);
      await page.waitForTimeout(400);
      const area = await page.$(`${OUT} .clustrix-widget`);
      await area.scrollIntoViewIfNeeded();
      const file = path.join(outDir, `${name}.png`);
      await area.screenshot({ path: file });
      console.log('wrote', file);
    },
    async tile(label) {
      await page.locator(`${OUT} .widget-toggle-buttons button`, { hasText: label }).first().click();
      await page.waitForTimeout(800);
    },
    async press(label) {
      await page.locator(`${OUT} button`, { hasText: label }).first().click();
      await page.waitForTimeout(800);
    },
    async type(label, text) {
      const input = page.locator(`${OUT} .widget-text`, { hasText: label }).first().locator('input');
      await input.fill(text);
      await input.press('Tab');
      await page.waitForTimeout(500);
    },
    async status(pattern, timeout) {
      // The pill next to "Result" says how the last action ended.
      await page.waitForFunction(
        ([selector, source]) => {
          const pill = document.querySelector(`${selector} .cx-result-bar`);
          return pill && new RegExp(source).test(pill.textContent);
        },
        [OUT, pattern.source],
        { timeout },
      );
      await page.waitForTimeout(1200);
    },
    async close() { await context.close(); },
  };
  return widget;
}

async function firstRun(browser) {
  // A profile store that has never been written: the built-in templates.
  const w = await open(browser, 'first_run.ipynb', 'light');
  await w.snap('first-run');
  await w.tile('Hugging Face');
  await w.snap('huggingface');
  await w.tile('SSH');
  await w.press(/^Apply$/);
  await w.status(/invalid/, 20000);
  await w.snap('validation');
  await w.press(/Advanced/);
  await w.snap('advanced');
  await w.close();
}

async function cluster(browser) {
  // The form filled in for the test cluster, whose host key is not yet known.
  let w = await open(browser, 'cluster.ipynb', 'light');
  await w.snap('ssh');
  await w.press(/^Test connection$/);
  await w.status(/unknown host/, 60000);
  await w.snap('unknown-host');
  await w.press(/^Trust this host$/);
  await w.status(/(?<!not )connected/, 60000);
  await w.snap('trusted');
  await w.tile('SLURM');
  await w.type('Partition', 'debug');
  await w.snap('slurm');
  await w.press(/^Run test job$/);
  await w.status(/job completed/, 300000);
  await w.snap('test-job');
  await w.press(/^Apply$/);
  await w.status(/Result\s*applied/, 30000);
  await w.snap('applied');
  await w.close();

  // The same form in the dark theme; the host is trusted by now.
  w = await open(browser, 'cluster.ipynb', 'dark');
  await w.press(/^Test connection$/);
  await w.status(/(?<!not )connected/, 60000);
  await w.snap('dark');
  await w.close();
}

(async () => {
  const browser = await chromium.launch();
  try {
    await firstRun(browser);
    if (withCluster === '1') await cluster(browser);
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
