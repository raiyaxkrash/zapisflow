export function icon(name) {
  const paths = {
    home: "M3 10 12 3 21 10v10H3z M9 20v-7h6v7",
    calendar: "M4 5h16v16H4z M4 10h16 M8 3v4 M16 3v4",
    users:
      "M8 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M2 21v-3a6 6 0 0 1 12 0v3 M17 4a4 4 0 0 1 0 8 M18 15a5 5 0 0 1 4 5",
    more: "M4 12h1 M11 12h1 M18 12h1",
    scissors:
      "M4 4 20 20 M4 20 20 4 M6 6a3 3 0 1 0-6 0 3 3 0 0 0 6 0 M6 18a3 3 0 1 0-6 0 3 3 0 0 0 6 0",
  };
  return `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name] || paths.more}"/></svg>`;
}
