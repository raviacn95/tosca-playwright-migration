# Tosca to Playwright migration hub

Generic converter for Tosca `.tsu` exports. It produces Playwright specs, Jira-style manuals, and Allure results without shipping any application-specific test data.

## Setup

```bash
npm install
npx playwright install chromium
```

Python 3 is required for the converter and hub.

## Import and convert

1. Drop `.tsu` files into `imports/tsu`, or start the hub and upload them there:

   ```bash
   npm start
   ```

   Hub: `http://127.0.0.1:8765`

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

Override the Excel file with `TOSCA_EXCEL` if needed. Sample columns live in `data/Users.xlsx`.

## Layout

| Path | Purpose |
| --- | --- |
| `imports/tsu/` | Drop zone for Tosca exports (not committed) |
| `tools/` | Converter, hub, and report publishers |
| `src/reporting/` | Playwright step reporter (JSON + HTML) |
| `tests/generated/` | Generated specs (not committed) |
| `reports/` | Manual catalog and run reports (not committed) |
