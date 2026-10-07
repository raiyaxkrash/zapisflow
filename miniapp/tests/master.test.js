import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import * as W from "../src/master/workspace.js";
const ctx = {
  project: { name: "Studio" },
  today: "2026-10-08",
  server_now: "2026-10-08T09:00:00+03:00",
  capabilities: { can_edit_project: true, role: "owner" },
};
const a = {
  id: 7,
  client: "<script>x</script>",
  service: "Стрижка",
  staff: "Анна",
  price: "500",
  payment: [],
  status: "CONFIRMED",
  status_label: "Подтверждена",
  start_time: "2026-10-08T10:00:00+03:00",
  cancel_allowed: true,
  master_client_id: 3,
};
test("Today shows real appointments, quick actions and next visit without fake revenue", () => {
  const dom = new JSDOM(W.today(ctx, [a], "", "", "2026-10-08"));
  assert.equal(dom.window.document.querySelector("script"), null);
  assert.ok(dom.window.document.body.textContent.includes("Следующая запись"));
  assert.ok(dom.window.document.querySelector('[data-id="payments"]'));
  assert.ok(!dom.window.document.body.textContent.includes("Выручка"));
  dom.window.close();
});
test("Appointment details expose only supported actions and phone links stay safe", () => {
  const dom = new JSDOM(W.details(ctx, { ...a, phone: "javascript:alert(1)" }));
  assert.equal(dom.window.document.querySelector("a"), null);
  assert.ok(dom.window.document.querySelector('[data-action="master-cancel"]'));
  assert.ok(dom.window.document.querySelector('[data-action="client"]'));
  dom.window.close();
});
test("Weekly editor restores hours and breaks, missing days have explicit day off", () => {
  const dom = new JSDOM(
    W.weeklyEditor(
      [
        {
          weekday: 1,
          is_day_off: false,
          work_start: "09:00:00",
          work_end: "18:00:00",
          breaks: [["12:00:00", "13:00:00"]],
        },
      ],
      2,
      1,
    ),
  );
  assert.equal(
    dom.window.document.querySelector("[name=work_start]").value,
    "09:00",
  );
  assert.equal(
    dom.window.document.querySelector("[name=breaks]").value,
    "12:00-13:00",
  );
  assert.equal(
    dom.window.document.querySelectorAll('[data-action="weekly-day"]').length,
    7,
  );
  dom.window.close();
});
test("Payments and broadcasts explain empty state; analytics use supplied domain values", () => {
  const dom = new JSDOM(
    W.payments(ctx, []) +
      W.broadcasts(ctx, []) +
      W.analytics(ctx, {
        total: 0,
        completed: 0,
        unique_clients: 0,
        revenue: "0",
        top_services: [],
      }),
  );
  assert.ok(
    dom.window.document.body.textContent.includes("Нет оплат для проверки"),
  );
  assert.ok(dom.window.document.body.textContent.includes("Рассылок пока нет"));
  assert.ok(
    dom.window.document.body.textContent.includes("Пока недостаточно данных"),
  );
  dom.window.close();
});
