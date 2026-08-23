const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const BASE_URL = 'http://localhost:8181';
const OUT_DIR = path.join(__dirname, 'screenshots');
const ADMIN_USER = 'admin';
const ADMIN_PASS = 'adminadmin';

async function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function closeAllModals(page) {
  // Press Escape to close any open modal
  await page.keyboard.press('Escape');
  await sleep(500);
  // Also try clicking modal overlay if present
  const overlays = await page.$$('.modal-overlay.active');
  for (const overlay of overlays) {
    try { await overlay.click(); } catch (e) {}
  }
  await sleep(300);
}

async function captureDashboard() {
  if (!fs.existsSync(OUT_DIR)) fs.mkdirSync(OUT_DIR, { recursive: true });

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
  });
  const page = await context.newPage();

  // ── Login ──
  console.log('Logging in as admin...');
  await page.goto(`${BASE_URL}/auth/admin/login`, { waitUntil: 'networkidle', timeout: 15000 });
  await page.fill('#username', ADMIN_USER);
  await page.fill('#password', ADMIN_PASS);
  await page.click('button[type="submit"]');
  await sleep(3000);

  // ── Dashboard ──
  console.log('Navigating to dashboard...');
  await page.goto(BASE_URL, { waitUntil: 'networkidle', timeout: 15000 });
  await sleep(5000);
  try { await page.waitForSelector('.stat-card', { timeout: 10000 }); } catch (e) {}
  await sleep(3000);

  // ── Full page ──
  console.log('Taking full page screenshot...');
  await page.screenshot({ path: path.join(OUT_DIR, '00-full-dashboard.png'), fullPage: true });

  // ── Stat cards (first viewport, scroll to top) ──
  await page.evaluate(() => window.scrollTo(0, 0));
  await sleep(500);
  const statsEl = await page.$('.stats');
  if (statsEl) {
    await statsEl.screenshot({ path: path.join(OUT_DIR, '01-header-stats.png') });
    console.log('Captured: 01-header-stats');
  }

  // ── Fleet map ──
  const mapSection = await page.$('.map-section');
  if (mapSection) {
    await mapSection.screenshot({ path: path.join(OUT_DIR, '02-fleet-map.png') });
    console.log('Captured: 02-fleet-map');
  }

  // ── Alerts panel ──
  const alertsPanel = await page.$('#alerts-panel');
  if (alertsPanel) {
    await alertsPanel.screenshot({ path: path.join(OUT_DIR, '03-alerts-panel.png') });
    console.log('Captured: 03-alerts-panel');
  }

  // ── Aegis section ──
  const aegisSection = await page.$('#aegis-section');
  if (aegisSection) {
    await aegisSection.screenshot({ path: path.join(OUT_DIR, '04-aegis-remediation.png') });
    console.log('Captured: 04-aegis-remediation');
  }

  // ── Predictive panel ──
  const predPanel = await page.$('#predictive-panel');
  if (predPanel) {
    await predPanel.screenshot({ path: path.join(OUT_DIR, '05-predictive-maintenance.png') });
    console.log('Captured: 05-predictive-maintenance');
  }

  // ── Schedules panel ──
  const schedPanel = await page.$('#schedules-panel');
  if (schedPanel) {
    await schedPanel.screenshot({ path: path.join(OUT_DIR, '06-scheduled-ota.png') });
    console.log('Captured: 06-scheduled-ota');
  }

  // ── Geofences panel ──
  const geoPanel = await page.$('#geofences-panel');
  if (geoPanel) {
    await geoPanel.screenshot({ path: path.join(OUT_DIR, '07-geofences.png') });
    console.log('Captured: 07-geofences');
  }

  // ── Device table ──
  const deviceTable = await page.$('#device-table');
  if (deviceTable) {
    await deviceTable.screenshot({ path: path.join(OUT_DIR, '08-device-table.png') });
    console.log('Captured: 08-device-table');
  }

  // ── OTA table ──
  const otaTable = await page.$('#ota-table');
  if (otaTable) {
    await otaTable.screenshot({ path: path.join(OUT_DIR, '09-ota-deployments.png') });
    console.log('Captured: 09-ota-deployments');
  }

  // ── Agent panels ──
  const agentPanels = await page.$('#agent-panels');
  if (agentPanels) {
    await agentPanels.screenshot({ path: path.join(OUT_DIR, '10-agent-recommendations.png') });
    console.log('Captured: 10-agent-recommendations');
  }

  // ── Firmware section ──
  const fwSection = await page.$('#firmware-section');
  if (fwSection) {
    await fwSection.screenshot({ path: path.join(OUT_DIR, '11-firmware-management.png') });
    console.log('Captured: 11-firmware-management');
  }

  // ── Device detail modal ──
  const deviceRows = await page.$$('.device-row');
  if (deviceRows.length > 0) {
    console.log('Opening device detail modal...');
    await deviceRows[0].click();
    await sleep(2000);
    try { await page.waitForSelector('#chart-signal', { timeout: 5000 }); } catch (e) {}
    await sleep(1000);

    await page.screenshot({ path: path.join(OUT_DIR, '12-device-detail-tabs.png'), fullPage: false });
    console.log('Captured: 12-device-detail-tabs');

    // Shadow tab
    const shadowTab = await page.$('.detail-tab:nth-child(2)');
    if (shadowTab) { await shadowTab.click(); await sleep(500);
      await page.screenshot({ path: path.join(OUT_DIR, '13-device-shadow-tab.png'), fullPage: false });
      console.log('Captured: 13-device-shadow-tab'); }

    // Lifecycle tab
    const lifecycleTab = await page.$('.detail-tab:nth-child(3)');
    if (lifecycleTab) { await lifecycleTab.click(); await sleep(500);
      await page.screenshot({ path: path.join(OUT_DIR, '14-device-lifecycle-tab.png'), fullPage: false });
      console.log('Captured: 14-device-lifecycle-tab'); }

    // Close modal
    await page.keyboard.press('Escape');
    await sleep(500);
  }

  // ── Onboard modal ──
  await closeAllModals(page);
  const onboardToolBtn = await page.locator('button[onclick="openOnboardModal()"]');
  if (await onboardToolBtn.count() > 0) {
    console.log('Opening onboard modal...');
    await onboardToolBtn.click();
    await sleep(500);
    await page.screenshot({ path: path.join(OUT_DIR, '15-onboard-modal.png'), fullPage: false });
    console.log('Captured: 15-onboard-modal');
    await page.keyboard.press('Escape'); await sleep(300);
  }

  // ── OTA Trigger modal ──
  await closeAllModals(page);
  const otaToolBtn = await page.locator('button[onclick="openOtaModal()"]');
  if (await otaToolBtn.count() > 0) {
    console.log('Opening OTA trigger modal...');
    await otaToolBtn.click();
    await sleep(500);
    await page.screenshot({ path: path.join(OUT_DIR, '16-ota-trigger-modal.png'), fullPage: false });
    console.log('Captured: 16-ota-trigger-modal');
    await page.keyboard.press('Escape'); await sleep(300);
  }

  await browser.close();
  const files = fs.readdirSync(OUT_DIR).filter(f => f.endsWith('.png'));
  const sizes = files.map(f => `${f} (${(fs.statSync(path.join(OUT_DIR, f)).size / 1024).toFixed(0)}KB)`);
  console.log(`\nDone! ${files.length} screenshots:`);
  sizes.sort().forEach(s => console.log(`  ${s}`));
}

captureDashboard().catch(console.error);
