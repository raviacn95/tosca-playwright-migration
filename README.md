# Tosca to Playwright migration hub

Open-source, vendor-neutral toolkit for converting Tosca `.tsu` exports into Playwright tests, Jira-style manual cases, and Allure results.

Any organization may use, copy, modify, and distribute this software under the [MIT License](LICENSE). It is not affiliated with, endorsed by, or sponsored by the owners of Tosca, Playwright, Jira, Allure, or any other product named here.

## Setup

```bash
npm install
npx playwright install chromium
```

Python 3 is required for the converter and local hub.

## Import and convert

Keep customer exports on your machine. `.tsu` files, generated specs, and reports are gitignored and must not be committed.

1. Drop `.tsu` files into `imports/tsu`, or start the hub and upload them there:

   ```bash
   npm start
   ```

   The hub listens on `http://127.0.0.1:8765` (localhost only).

2. Convert from the hub (**Convert all**) or the CLI:

   ```bash
   npm run convert
   ```

Generated Playwright specs land in `tests/generated/`. Manual catalog and Jira markdown land in `reports/`. Allure JSON lands in `allure-results/`.

```bash
npx playwright test tests/generated
npm run allure:generate
npm run allure:open
```

Optional environment variables:

| Variable | Purpose |
| --- | --- |
| `TOSCA_EXCEL` | Path to the Excel data file (default `data/Users.xlsx`) |
| `APP_URL` | Overrides the application URL from the Tosca export |
| `TOSCA_TSU_DIR` | Alternate import folder |
| `TOSCA_HUB_PORT` | Hub port (default `8765`) |

Sample columns live in `data/Users.xlsx` (`user01` / `user02`). Replace them with your own test data.

## Layout

| Path | Purpose |
| --- | --- |
| `imports/tsu/` | Drop zone for Tosca exports (not committed) |
| `tools/` | Converter, hub, and report publishers |
| `src/reporting/` | Playwright step reporter (JSON + HTML) |
| `tests/generated/` | Generated specs (not committed) |
| `reports/` | Manual catalog and run reports (not committed) |

## License

MIT. Free for commercial and non-commercial use, including internal enterprise use.

Product names mentioned in this project are trademarks of their respective owners.
