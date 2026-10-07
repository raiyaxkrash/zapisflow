const escapeICS = (value) =>
  String(value ?? "")
    .replace(/\\/g, "\\\\")
    .replace(/\r?\n|\r/g, "\n")
    .replace(/;/g, "\;")
    .replace(/,/g, "\,");
const stamp = (value) =>
  new Date(value)
    .toISOString()
    .replace(/[-:]/g, "")
    .replace(/\.\d{3}/, "");
export function calendarEvent(a, scope = "zapisflow", now = new Date()) {
  if (
    a.status !== "CONFIRMED" ||
    !Number.isFinite(Number(a.duration_min)) ||
    Number(a.duration_min) <= 0 ||
    !Number.isFinite(Date.parse(a.start_time))
  )
    throw new Error("Событие пока недоступно");
  const uid = String(scope).replace(/[^a-zA-Z0-9-]/g, "");
  const lines = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//ZapisFlow//Booking//RU",
    "BEGIN:VEVENT",
    `UID:${Number(a.id)}-${uid}@zapisflow`,
    `DTSTAMP:${stamp(now)}`,
    `DTSTART:${stamp(a.start_time)}`,
    `DTEND:${stamp(new Date(Date.parse(a.start_time) + Number(a.duration_min) * 60000))}`,
    `SUMMARY:${escapeICS(a.service)}`,
    `DESCRIPTION:${escapeICS(a.staff)}`,
    "END:VEVENT",
    "END:VCALENDAR",
  ];
  return (
    lines
      .map((line) => {
        const chunks = [""];
        let bytes = 0;
        for (const c of line) {
          const count = new TextEncoder().encode(c).length;
          if (bytes + count > 75) {
            chunks.push(" ");
            bytes = 1;
          }
          chunks[chunks.length - 1] += c;
          bytes += count;
        }
        return chunks.join("\r\n");
      })
      .join("\r\n") + "\r\n"
  );
}
export function downloadCalendar(a, scope, win = window) {
  const url = win.URL.createObjectURL(
    new Blob([calendarEvent(a, scope)], {
      type: "text/calendar;charset=utf-8",
    }),
  );
  const link = win.document.createElement("a");
  link.href = url;
  link.download = "zapisflow-appointment.ics";
  link.click();
  win.setTimeout(() => win.URL.revokeObjectURL(url), 1000);
}
