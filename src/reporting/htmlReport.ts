export type StepStatus = 'PASS' | 'FAIL' | 'SKIPPED';
export type RunStatus = 'PASSED' | 'FAILED' | 'RUNNING';

export interface StepResult {
  step: string;
  folder?: string;
  module?: string;
  iteration?: string;
  status: StepStatus;
  startedAt: string;
  finishedAt: string;
  durationMs: number;
  error?: string;
  screenshot?: string;
}

export interface ToscaReport {
  testCase: string;
  folder?: string;
  sourceTsu?: string;
  startedAt: string;
  finishedAt: string;
  durationMs: number;
  status: RunStatus;
  passed: number;
  failed: number;
  skipped: number;
  steps: StepResult[];
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function statusClass(status: string): string {
  if (status === 'PASS' || status === 'PASSED') return 'pass';
  if (status === 'FAIL' || status === 'FAILED') return 'fail';
  return 'skip';
}

export function renderToscaHtml(report: ToscaReport): string {
  const rows = report.steps
    .map((step) => {
      const screenshot = step.screenshot
        ? `<a href="${escapeHtml(step.screenshot)}" target="_blank"><img src="${escapeHtml(step.screenshot)}" alt="failure screenshot"/></a>`
        : '';
      return `<tr class="${statusClass(step.status)}">
        <td>${escapeHtml(step.iteration || '')}</td>
        <td>${escapeHtml(step.folder || '')}</td>
        <td>${escapeHtml(step.step)}</td>
        <td>${escapeHtml(step.module || '')}</td>
        <td class="status">${escapeHtml(step.status)}</td>
        <td>${step.durationMs}</td>
        <td>${escapeHtml(step.error || '')}</td>
        <td class="shot">${screenshot}</td>
      </tr>`;
    })
    .join('\n');

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Execution Report - ${escapeHtml(report.testCase)}</title>
  <style>
    :root { --bg:#0f172a; --card:#1e293b; --text:#e2e8f0; --muted:#94a3b8; --pass:#16a34a; --fail:#dc2626; --skip:#ca8a04; --line:#334155; }
    body { font-family: Segoe UI, Arial, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 24px; }
    h1 { margin: 0 0 8px; }
    .meta, .summary { color: var(--muted); margin-bottom: 16px; }
    .cards { display: flex; gap: 12px; margin: 16px 0 24px; }
    .card { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 16px 20px; min-width: 120px; }
    .card b { display: block; font-size: 28px; }
    .card.pass b { color: var(--pass); }
    .card.fail b { color: var(--fail); }
    .card.skip b { color: var(--skip); }
    table { width: 100%; border-collapse: collapse; background: var(--card); }
    th, td { border-bottom: 1px solid var(--line); padding: 8px 10px; text-align: left; vertical-align: top; font-size: 13px; }
    th { background: #0b1220; position: sticky; top: 0; }
    tr.pass td.status { color: var(--pass); font-weight: 700; }
    tr.fail td.status { color: var(--fail); font-weight: 700; }
    tr.skip td.status { color: var(--skip); font-weight: 700; }
    tr.fail { background: rgba(220, 38, 38, 0.12); }
    img { max-width: 180px; border-radius: 4px; }
    .banner { display: inline-block; padding: 4px 10px; border-radius: 999px; font-weight: 700; }
    .banner.pass { background: var(--pass); color: white; }
    .banner.fail { background: var(--fail); color: white; }
  </style>
</head>
<body>
  <h1>Execution Report - ${escapeHtml(report.testCase)}</h1>
  <div class="meta">
    <span class="banner ${statusClass(report.status)}">${escapeHtml(report.status)}</span>
    ${report.folder ? `&nbsp; Folder: ${escapeHtml(report.folder)}` : ''}
    ${report.sourceTsu ? `&nbsp; Source: ${escapeHtml(report.sourceTsu)}` : ''}
  </div>
  <div class="summary">Started ${escapeHtml(report.startedAt)} · Finished ${escapeHtml(report.finishedAt)} · Duration ${report.durationMs} ms</div>
  <div class="cards">
    <div class="card pass"><span>Passed</span><b>${report.passed}</b></div>
    <div class="card fail"><span>Failed</span><b>${report.failed}</b></div>
    <div class="card skip"><span>Skipped</span><b>${report.skipped}</b></div>
  </div>
  <table>
    <thead>
      <tr>
        <th>Iteration</th>
        <th>Folder</th>
        <th>Step</th>
        <th>Module</th>
        <th>Status</th>
        <th>Duration (ms)</th>
        <th>Error</th>
        <th>Screenshot</th>
      </tr>
    </thead>
    <tbody>
      ${rows}
    </tbody>
  </table>
</body>
</html>`;
}
