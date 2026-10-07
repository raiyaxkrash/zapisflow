import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
// Run against a local production preview. All auth/API fixtures stay in this QA process.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const publicId = '11111111-2222-3333-4444-555555555555';
(async () => {
  const browser = await chromium.launch({headless: true});
  const runs = [];
  try {
    for (const width of [320, 375, 430, 768]) for (const dark of [false, true]) {
      const page = await browser.newPage({viewport: {width, height: 812}, colorScheme: dark ? 'dark' : 'light'});
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(({dark}) => {
        window.Telegram = {WebApp: {initData: 'LOCAL_QA_ONLY', colorScheme: dark ? 'dark' : 'light',
          safeAreaInset: {bottom: 34}, contentSafeAreaInset: {bottom: 34},
          ready(){}, expand(){}, onEvent(){}, BackButton: {show(){},hide(){},onClick(){}}}};
      }, {dark});
      await page.route('https://telegram.org/**', route => route.fulfill({status: 200, body: ''}));
      await page.route('**/api/miniapp/**', route => {
        const path = new URL(route.request().url()).pathname;
        let data;
        if (path.endsWith('/auth')) data = {csrf_token: 'LOCAL_QA_ONLY'};
        else if (path.endsWith('/context')) data = {project: {name: 'Focus QA', timezone: 'Europe/Moscow'},
          user: {first_name: 'Client'}, contacts: {}, today: '2026-10-06', can_book: true,
          booking_horizon_days: 14, capabilities: {can_manage: false},
          branding: {accent_color: '#1D72FE', show_staff: true}};
        else if (path.endsWith('/services')) data = [{id: 1, title: 'Service', price: '1500', duration_min: 60, is_active: true}];
        else if (path.endsWith('/staff')) data = [{id: 2, display_name: 'Specialist', is_active: true}];
        else if (path.endsWith('/appointments')) data = [];
        else if (path.includes('/calendar')) data = {year: 2026, month: 10, today: '2026-10-06',
          min_date: '2026-10-06', max_date: '2026-10-20', days: Array.from({length: 31}, (_, i) => ({
            date: `2026-10-${String(i + 1).padStart(2, '0')}`, available: i >= 5 && i < 20,
            reason: i < 5 ? 'past' : i >= 20 ? 'horizon' : null}))};
        else if (path.endsWith('/slots')) data = {slots: ['2026-10-06T10:00:00+03:00']};
        else throw new Error(`Unexpected QA endpoint: ${path}`);
        return route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(data)});
      });
      await page.goto(`${process.env.MINIAPP_QA_URL || 'http://127.0.0.1:5185'}/b/${publicId}`);
      const click = async (action, id) => {
        await page.locator(`[data-action="${action}"][data-id="${id}"]`).first().click();
        await page.waitForLoadState('networkidle');
      };
      await click('choose-service', 1);
      await click('staff', 2);
      await page.locator('main').focus();
      let slotChecked = false;
      for (let i = 0; i < 45; i++) {
        await page.keyboard.press('Tab');
        const focus = await page.evaluate(() => {
          const element = document.activeElement, nav = document.querySelector('.app-nav');
          if (!element || element === document.body || nav.contains(element)) return null;
          const bounds = element.getBoundingClientRect(), navigation = nav.getBoundingClientRect();
          return {slot: element.dataset.action === 'slot', top: bounds.top, bottom: bounds.bottom, navTop: navigation.top};
        });
        if (!focus) continue;
        slotChecked ||= focus.slot;
        assert.ok(focus.bottom > 0 && focus.top < focus.navTop, `Focus entirely obscured: ${JSON.stringify(focus)}`);
      }
      assert.ok(slotChecked, 'Time slot must be reachable by keyboard');
      assert.deepEqual(errors, []);
      runs.push({width, dark, safeBottom: 34, pass: true});
      await page.close();
    }
  } finally { await browser.close(); }
  console.log(JSON.stringify({runs}));
})().catch(error => {console.error(error); process.exitCode = 1;});
