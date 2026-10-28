# ZapisFlow — Web UI concept (Figma → code)

Standalone responsive frontend demo matching the ZapisFlow Figma design:
https://www.figma.com/design/AepnBgWtPyHLhA8sFbfcma

## Quick start

No build tools or dependencies are required.

\`\`\`bash
cd frontend
python3 -m http.server 4173
\`\`\`

Open http://localhost:4173. Navigation uses hash routes, so static hosting works without rewrite rules.

## Pages

- \`#/\` — public marketing home page
- \`#/login\`, \`#/signup\` — demonstration forms
- \`#/client\`, \`#/client/visits\`, \`#/booking\`, \`#/success\` — client UI
- \`#/master\`, \`#/master/calendar\`, \`#/master/services\`, \`#/master/clients\`, \`#/master/settings\` — master workspace

## Demo behavior and safety

- Client and master flows are interactive.
- Services and client records can be added/edited/deleted **in this browser only**.
- New appointment selects a service/day/time and avoids time overlaps for the same sample master.
- All demo state is stored in localStorage under \`zapisflow_frontend_demo_v1\`.
- Demo authentication accepts an arbitrary email and password and **does not authenticate**; no password is persisted.
- No network request is made to the real ZapisFlow API; no payment is processed, no Telegram notification is sent.
- App uses illustrative/sample data; revenue figures are **not** production analytics.

## Production integration checklist (not yet implemented)

1. Define a tenant-scoped authenticated web API on the existing FastAPI backend (or a separate BFF). Do not expose Telegram bot tokens.
2. Integrate a real session / OAuth flow, CSRF handling, authorization and tenant membership checks.
3. Connect services, customers, appointments, calendar and availability to backend API with strict server-side validation.
4. Preserve existing double-booking / hold and prepayment semantics; never trust browser availability or prices.
5. Add integration/E2E tests, CI, Caddy routing and deployment staging before replacing the public site.

**IMPORTANT:** This directory is intentionally isolated from \`docker-compose.yml\` and the existing webhook engine, to avoid modifying production behavior.
