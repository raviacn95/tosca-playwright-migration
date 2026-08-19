import * as fs from 'fs';
import * as path from 'path';
import type { Page } from '@playwright/test';
import { renderToscaHtml, type RunStatus, type StepResult, type ToscaReport } from './htmlReport';

export interface ToscaReporterOptions {
  testCase: string;
  folder?: string;
  sourceTsu?: string;
  outputDir?: string;
  captureScreenshotsOnFailure?: boolean;
}

export class ToscaReporter {
  readonly outputDir: string;
  private readonly captureScreenshotsOnFailure: boolean;
  private readonly startedAt = new Date();
  private readonly steps: StepResult[] = [];
  private iteration?: string;
  private folder?: string;
  private status: RunStatus = 'RUNNING';

  constructor(private readonly options: ToscaReporterOptions) {
    this.outputDir = path.resolve(options.outputDir ?? path.join(process.cwd(), 'reports'));
    this.captureScreenshotsOnFailure = options.captureScreenshotsOnFailure ?? true;
    this.folder = options.folder;
    fs.mkdirSync(this.outputDir, { recursive: true });
    fs.mkdirSync(path.join(this.outputDir, 'screenshots'), { recursive: true });
  }

  startFolder(folder: string): void {
    this.folder = folder;
    console.log(`\n== Folder: ${folder} ==`);
  }

  startIteration(label: string): void {
    this.iteration = label;
    console.log(`\n-- Iteration: ${label} --`);
  }

  async step(
    name: string,
    action: () => Promise<void>,
    extras?: { module?: string; page?: Page },
  ): Promise<void> {
    const started = new Date();
    console.log(`Executing: ${name}`);
    try {
      await action();
      this.push({
        step: name,
        module: extras?.module,
        status: 'PASS',
        startedAt: started.toISOString(),
        finishedAt: new Date().toISOString(),
        durationMs: Date.now() - started.getTime(),
      });
      console.log(`PASS: ${name}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      let screenshot: string | undefined;
      if (this.captureScreenshotsOnFailure && extras?.page) {
        screenshot = await this.captureScreenshot(name, extras.page);
      }
      this.push({
        step: name,
        module: extras?.module,
        status: 'FAIL',
        startedAt: started.toISOString(),
        finishedAt: new Date().toISOString(),
        durationMs: Date.now() - started.getTime(),
        error: message,
        screenshot,
      });
      console.log(`FAIL: ${name} — ${message}`);
      throw error;
    }
  }

  async ifVisible(
    name: string,
    page: Page,
    locator: { waitFor: (options: { state: 'visible'; timeout: number }) => Promise<void>; click: () => Promise<void> },
    extras?: { module?: string; timeoutMs?: number },
  ): Promise<void> {
    const visible = await locator
      .waitFor({ state: 'visible', timeout: extras?.timeoutMs ?? 5_000 })
      .then(() => true)
      .catch(() => false);
    if (!visible) {
      this.skip(name, extras?.module, 'Condition not met — control not visible');
      return;
    }
    await this.step(name, async () => {
      await locator.click();
    }, { module: extras?.module, page });
  }

  skip(name: string, module?: string, reason?: string): void {
    const now = new Date().toISOString();
    this.push({
      step: name,
      module,
      status: 'SKIPPED',
      startedAt: now,
      finishedAt: now,
      durationMs: 0,
      error: reason,
    });
    console.log(`SKIPPED: ${name}${reason ? ` — ${reason}` : ''}`);
  }

  finish(status: Exclude<RunStatus, 'RUNNING'>): ToscaReport {
    this.status = status;
    const finishedAt = new Date();
    const report: ToscaReport = {
      testCase: this.options.testCase,
      folder: this.options.folder,
      sourceTsu: this.options.sourceTsu,
      startedAt: this.startedAt.toISOString(),
      finishedAt: finishedAt.toISOString(),
      durationMs: finishedAt.getTime() - this.startedAt.getTime(),
      status,
      passed: this.steps.filter((s) => s.status === 'PASS').length,
      failed: this.steps.filter((s) => s.status === 'FAIL').length,
      skipped: this.steps.filter((s) => s.status === 'SKIPPED').length,
      steps: this.steps,
    };

    const jsonPath = path.join(this.outputDir, 'playwright-report.json');
    const htmlPath = path.join(this.outputDir, 'playwright-report.html');
    fs.writeFileSync(jsonPath, JSON.stringify(report, null, 2), 'utf-8');
    fs.writeFileSync(htmlPath, renderToscaHtml(this.withRelativeScreenshots(report)), 'utf-8');
    console.log(`\nTosca-style JSON report: ${jsonPath}`);
    console.log(`Tosca-style HTML report: ${htmlPath}`);
    return report;
  }

  private push(step: Omit<StepResult, 'folder' | 'iteration'>): void {
    this.steps.push({
      ...step,
      folder: this.folder,
      iteration: this.iteration,
    });
  }

  private async captureScreenshot(stepName: string, page: Page): Promise<string | undefined> {
    const safe = stepName.replace(/[^a-z0-9]+/gi, '_').slice(0, 60);
    const fileName = `${Date.now()}_${safe}.png`;
    const fullPath = path.join(this.outputDir, 'screenshots', fileName);
    try {
      await page.screenshot({ path: fullPath, fullPage: true });
      return path.join('screenshots', fileName);
    } catch (error) {
      console.log(`Could not capture screenshot for ${stepName}: ${String(error)}`);
      return undefined;
    }
  }

  private withRelativeScreenshots(report: ToscaReport): ToscaReport {
    return report;
  }
}
