import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { calendarEvent } from "../src/client/calendar-event.js";
import * as V from "../src/client/views.js";
const record = {
  id: 7,
  status: "CONFIRMED",
  service: "Haircut",
  staff: "Anna",
  duration_min: 60,
  start_time: "2026-10-08T14:30:00+03:00",
  price: "1500",
  deposit: "0",
  payment: [],
  status_label: "Подтверждена",
};
test("Calendar event uses UTC, folds UTF-8 lines and prevents event injection", () => {
  const text = calendarEvent(
    { ...record, service: "Услуга".repeat(30) + "\nBEGIN:VEVENT;evil" },
    "tenant-id",
    new Date("2026-10-07T10:00:00Z"),
  );
  assert.ok(text.includes("DTSTART:20261008T113000Z"));
  assert.ok(text.includes("DTEND:20261008T123000Z"));
  assert.equal(
    text.split("\r\n").filter((line) => line === "BEGIN:VEVENT").length,
    1,
  );
  assert.ok(
    text
      .split("\r\n")
      .every((line) => new TextEncoder().encode(line).length <= 75),
  );
  assert.throws(() => calendarEvent({ ...record, status: "WAITING_PAYMENT" }));
});
test("Client empty states, slots and safe errors remain accessible", () => {
  const dom = new JSDOM(
    V.bookings([], "2026-10-07") +
      V.slots([record.start_time], record.start_time),
  );
  assert.ok(dom.window.document.querySelector("[data-id=services]"));
  assert.equal(
    dom.window.document.querySelector(".slot").getAttribute("aria-pressed"),
    "true",
  );
  assert.ok(
    dom.window.document.querySelector(".slot").textContent.includes("Выбрано"),
  );
  assert.equal(
    V.errorMessage(Error("Internal Server Error")),
    "Не удалось выполнить действие. Попробуйте ещё раз.",
  );
  dom.window.close();
});
test("Client cards escape tenant text and hide missing contact rows", () => {
  const dom = new JSDOM(
    V.serviceCards([
      {
        id: 1,
        title: "<script>x</script>",
        price: "500",
        duration_min: 30,
        description: "A&B",
      },
    ]),
  );
  assert.equal(dom.window.document.querySelector("script"), null);
  assert.ok(dom.window.document.body.textContent.includes("<script>"));
  dom.window.close();
});

test("Studio date stays stable across browser timezones and contacts reject unsafe URLs", () => {
  assert.ok(V.dateLabel("2026-10-08T00:30:00+14:00").includes("8 октября"));
  const dom = new JSDOM(
    V.contactPage({
      project: { name: "Studio" },
      contacts: {
        vk_profile: "javascript:alert(1)",
        studio_phone: "+79991234567",
        studio_address: "<script>x</script>",
      },
    }),
  );
  assert.equal(dom.window.document.querySelector("script"), null);
  assert.equal(
    dom.window.document.querySelector('[href^="javascript:"]'),
    null,
  );
  assert.equal(
    dom.window.document.querySelector("a").getAttribute("href"),
    "tel:+79991234567",
  );
  dom.window.close();
});
