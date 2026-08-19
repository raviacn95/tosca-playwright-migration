"""
Publish converted Tosca test cases as Manual cases in one report.

Writes:
  allure-results/*-result.json   — Allure (suite / steps / tags)
  reports/manual-catalog.json    — all manuals in one index
  reports/manual-catalog.html    — browseable manual scripts (no Java needed)
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from html import escape
from pathlib import Path

from jira_testcase import jira_markdown, to_jira_case

ROOT = Path(__file__).resolve().parents[1]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _value_line(v: dict) -> str:
    name = v.get("attributeName") or "Value"
    val = v.get("value")
    am = v.get("actionMode") or ""
    steer = v.get("steering") or {}
    loc_bits = [f"{k}={steer[k]}" for k in ("Id", "Title", "InnerText", "Tag", "ClassName") if steer.get(k)]
    loc = f"  [{', '.join(loc_bits)}]" if loc_bits else ""
    extra = ""
    if v.get("subValues"):
        nested = "; ".join(_value_line(s) for s in v["subValues"])
        extra = f" → {nested}"
    return f"{name} = {val} ({am}){loc}{extra}"


def flow_to_manual_steps(nodes: list, prefix="") -> list[dict]:
    """Flat manual script + nested tree for Allure."""
    out = []
    for node in nodes or []:
        t = node.get("type")
        name = node.get("name") or t or "Step"
        if t in ("TestStepFolder", "TestStepFolderReference", "ReuseableTestStepBlock"):
            rep = f"  [repeat {node.get('repetition')}]" if node.get("repetition") else ""
            children = flow_to_manual_steps(node.get("items") or [], prefix)
            out.append({
                "kind": "folder",
                "name": f"{name}{rep}",
                "instruction": f"Folder: {name}{rep}",
                "children": children,
            })
        elif t == "TestCaseControlFlowItem":
            branches = []
            for b in node.get("branches") or []:
                branches.append({
                    "kind": "branch",
                    "name": b.get("name") or "Branch",
                    "instruction": f"IF {b.get('name')}",
                    "children": flow_to_manual_steps(b.get("items") or [], prefix),
                })
            out.append({
                "kind": "if",
                "name": f"IF: {name}",
                "instruction": f"If the condition is true, execute Then; otherwise skip.",
                "children": branches,
            })
        elif t == "XTestStep":
            values = node.get("values") or []
            details = [_value_line(v) for v in values]
            engine = node.get("engine") or ""
            module = node.get("module") or ""
            action = "Perform"
            joined = " ".join(str(v.get("value") or "") for v in values)
            if "{Click}" in joined or joined.strip() in ("X", "x"):
                action = "Click"
            elif any(v.get("actionMode") == "Input" for v in values):
                action = "Enter / set"
            elif any(v.get("actionMode") == "WaitOn" for v in values):
                action = "Wait until visible"
            out.append({
                "kind": "step",
                "name": name,
                "module": module,
                "engine": engine,
                "action": action,
                "instruction": f"{action}: {name}" + (f" (module: {module})" if module else ""),
                "expected": "Control is found and the action succeeds.",
                "details": details,
                "children": [],
            })
        else:
            kids = flow_to_manual_steps(node.get("items") or [], prefix)
            if kids:
                out.extend(kids)
    return out


def _allure_steps(nodes: list[dict], status="skipped") -> list[dict]:
    ts = _now_ms()
    steps = []
    for node in nodes:
        child_steps = _allure_steps(node.get("children") or [], status)
        params = [{"name": "detail", "value": d} for d in (node.get("details") or [])[:12]]
        if node.get("module"):
            params.insert(0, {"name": "module", "value": node["module"]})
        step = {
            "name": node.get("instruction") or node.get("name") or "Step",
            "status": status,
            "stage": "finished",
            "start": ts,
            "stop": ts,
            "steps": child_steps,
            "parameters": params,
            "attachments": [],
        }
        steps.append(step)
    return steps


def mapped_to_allure(mapped: dict) -> dict:
    tc = mapped.get("testCase") or {}
    tcp = tc.get("testConfigurationParameters") or {}
    name = tc.get("name") or "Unnamed test"
    folder = tc.get("parentFolder") or "Tosca"
    project = mapped.get("project") or "Tosca"
    source = mapped.get("source") or ""
    uid = tc.get("id") or hashlib.md5(name.encode()).hexdigest()
    tree = flow_to_manual_steps(mapped.get("flow") or [])
    jira = to_jira_case(mapped, tree)
    description = jira_markdown(jira)
    full_name = f"{project}.{folder}.{name}"
    ts = _now_ms()
    allure_steps = []
    for i, step in enumerate(jira.get("steps") or [], 1):
        allure_steps.append({
            "name": f"{i}. {step}",
            "status": "skipped",
            "stage": "finished",
            "start": ts,
            "stop": ts,
            "steps": [],
            "parameters": [],
            "attachments": [],
        })
    return {
        "uuid": str(uid),
        "historyId": hashlib.sha1(full_name.encode("utf-8")).hexdigest(),
        "name": name,
        "fullName": full_name,
        "status": "skipped",
        "statusDetails": {
            "message": "Manual test case — ready for Jira / tester execution (not automated in this result).",
        },
        "stage": "finished",
        "description": description,
        "start": ts,
        "stop": ts,
        "steps": allure_steps,
        "labels": [
            {"name": "parentSuite", "value": project},
            {"name": "suite", "value": folder},
            {"name": "subSuite", "value": "Manual"},
            {"name": "feature", "value": folder},
            {"name": "story", "value": name},
            {"name": "package", "value": project},
            {"name": "tag", "value": "manual"},
            {"name": "tag", "value": "jira"},
            {"name": "severity", "value": "normal"},
        ],
        "links": [{"name": "Source export", "url": source, "type": "tms"}] if source else [],
        "parameters": [{"name": str(k), "value": str(v)} for k, v in tcp.items() if k in ("Browser", "WorkbookName")],
        "attachments": [],
    }


SAFE_ENV_KEYS = ("Browser", "WorkbookName")


def write_allure_files(mapped: dict, allure_dir: Path) -> Path:
    allure_dir.mkdir(parents=True, exist_ok=True)
    result = mapped_to_allure(mapped)
    path = allure_dir / f"{result['uuid']}-result.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    categories = [
        {
            "name": "Manual test cases (Tosca converted)",
            "matchedStatuses": ["skipped"],
            "messageRegex": ".*Manual test case.*",
        },
        {"name": "Product defects", "matchedStatuses": ["failed"]},
        {"name": "Passed automated", "matchedStatuses": ["passed"]},
    ]
    (allure_dir / "categories.json").write_text(json.dumps(categories, indent=2), encoding="utf-8")

    env = mapped.get("testCase", {}).get("testConfigurationParameters") or {}
    env_lines = [
        f"Project={mapped.get('project') or ''}",
        f"Source={Path(str(mapped.get('source') or '')).name}",
        "Framework=Tosca-to-Playwright",
        "ReportType=Manual catalog + Allure",
    ]
    for key in SAFE_ENV_KEYS:
        if env.get(key):
            env_lines.append(f"{key}={env[key]}")
    (allure_dir / "environment.properties").write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    return path


def upsert_catalog(mapped: dict, catalog_json: Path) -> dict:
    catalog_json.parent.mkdir(parents=True, exist_ok=True)
    catalog = {"generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"), "testCases": []}
    if catalog_json.exists():
        try:
            catalog = json.loads(catalog_json.read_text(encoding="utf-8"))
        except Exception:
            pass
    cases = catalog.get("testCases") or []
    tc = mapped.get("testCase") or {}
    entry = {
        "id": tc.get("id"),
        "name": tc.get("name"),
        "folder": tc.get("parentFolder"),
        "project": mapped.get("project"),
        "source": mapped.get("source"),
        "config": tc.get("testConfigurationParameters") or {},
        "steps": flow_to_manual_steps(mapped.get("flow") or []),
        "kind": "manual",
        "jira": to_jira_case(mapped, flow_to_manual_steps(mapped.get("flow") or [])),
    }
    cases = [c for c in cases if c.get("id") != entry["id"]]
    cases.append(entry)
    catalog["testCases"] = sorted(cases, key=lambda c: ((c.get("folder") or ""), (c.get("name") or "")))
    catalog["generatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    catalog["count"] = len(catalog["testCases"])
    catalog_json.write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")
    return catalog


def _render_step_html(node: dict, n=1) -> str:
    kind = node.get("kind")
    kids = "".join(_render_step_html(c) for c in node.get("children") or [])
    details = "".join(f"<li>{escape(d)}</li>" for d in node.get("details") or [])
    details_html = f"<ul class='details'>{details}</ul>" if details else ""
    cls = kind or "step"
    expected = node.get("expected")
    expected_html = f"<div class='expected'>Expected: {escape(expected)}</div>" if expected else ""
    return (
        f"<div class='node {escape(cls)}'>"
        f"<div class='title'>{escape(node.get('name') or '')}</div>"
        f"<div class='instruction'>{escape(node.get('instruction') or '')}</div>"
        f"{expected_html}{details_html}{kids}</div>"
    )


def render_catalog_html(catalog: dict, out_html: Path) -> Path:
    cases = catalog.get("testCases") or []
    nav = []
    panels = []
    for i, case in enumerate(cases):
        active = " active" if i == 0 else ""
        nav.append(
            f"<button class='nav-item{active}' data-id='{escape(str(case.get('id')))}'>"
            f"<span class='badge'>Manual</span>"
            f"<strong>{escape(case.get('name') or '')}</strong>"
            f"<small>{escape(case.get('folder') or '')}</small></button>"
        )
        jira = case.get("jira") or {}
        pre = "".join(f"<li>{escape(x)}</li>" for x in jira.get("preConditions") or [])
        steps_ol = "".join(f"<li>{escape(x)}</li>" for x in jira.get("steps") or [])
        exp = "".join(f"<li>{escape(x)}</li>" for x in jira.get("expectedResults") or [])
        post = "".join(f"<li>{escape(x)}</li>" for x in jira.get("postConditions") or [])
        hidden = "" if i == 0 else " hidden"
        panels.append(
            f"<article class='panel{hidden}' id='case-{escape(str(case.get('id')))}'>"
            f"<h2>Test Case: {escape(jira.get('title') or case.get('name') or '')}</h2>"
            f"<p class='meta'>{escape(case.get('folder') or '')}</p>"
            f"<p class='objective'><strong>Objective:</strong> {escape(jira.get('objective') or '')}</p>"
            f"<h3>Pre-Condition</h3><ul>{pre}</ul>"
            f"<h3>Steps</h3><p class='note'>Repeat data-driven steps for each Excel row when this case uses a data file.</p><ol>{steps_ol}</ol>"
            f"<h3>Expected Result</h3><ul>{exp}</ul>"
            f"<h3>Post-Condition</h3><ul>{post}</ul>"
            f"</article>"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Manual test catalog — Tosca conversions</title>
  <style>
    :root {{ --bg:#0f172a; --panel:#111827; --card:#1e293b; --line:#334155; --text:#e2e8f0; --muted:#94a3b8; --accent:#38bdf8; --manual:#a78bfa; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; font-family: Segoe UI, Arial, sans-serif; background:var(--bg); color:var(--text); display:flex; min-height:100vh; }}
    aside {{ width:320px; background:var(--panel); border-right:1px solid var(--line); padding:16px; overflow:auto; }}
    main {{ flex:1; padding:28px 36px; overflow:auto; }}
    h1 {{ font-size:18px; margin:0 0 12px; }}
    .count {{ color:var(--muted); margin-bottom:16px; }}
    .nav-item {{ display:block; width:100%; text-align:left; background:transparent; border:1px solid var(--line); color:var(--text); padding:10px 12px; margin:0 0 8px; border-radius:8px; cursor:pointer; }}
    .nav-item.active, .nav-item:hover {{ border-color:var(--accent); }}
    .nav-item small {{ display:block; color:var(--muted); }}
    h3 {{ margin-top: 24px; }}
    ul, ol {{ line-height: 1.6; }}
    .objective, .note, .meta {{ color:var(--muted); }}
    .badge {{ display:inline-block; background:var(--manual); color:#1e1b4b; font-size:11px; font-weight:700; padding:2px 6px; border-radius:999px; margin-bottom:4px; }}
    .panel[hidden] {{ display:none; }}
  </style>
</head>
<body>
  <aside>
    <h1>Manual test cases</h1>
    <div class="count">{catalog.get("count") or len(cases)} converted from Tosca · {escape(catalog.get("generatedAt") or "")}</div>
    {''.join(nav)}
  </aside>
  <main>
    {''.join(panels) if panels else "<p>No converted test cases yet. Upload a .tsu file on the hub or run <code>npm run convert</code>.</p>"}
  </main>
  <script>
    document.querySelectorAll('.nav-item').forEach(btn => {{
      btn.addEventListener('click', () => {{
        document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        document.querySelectorAll('.panel').forEach(p => p.hidden = true);
        const el = document.getElementById('case-' + btn.dataset.id);
        if (el) el.hidden = false;
      }});
    }});
  </script>
</body>
</html>"""
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(html, encoding="utf-8")
    return out_html


def write_empty_catalog(reports_dir: Path | None = None) -> dict:
    reports_dir = reports_dir or (ROOT / "reports")
    catalog = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "testCases": [],
        "count": 0,
    }
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "manual-catalog.json").write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    render_catalog_html(catalog, reports_dir / "manual-catalog.html")
    return catalog


def reset_conversion_outputs(allure_dir: Path | None = None, reports_dir: Path | None = None) -> None:
    """Rebuild catalogs from the current conversion batch only."""
    allure_dir = allure_dir or (ROOT / "allure-results")
    reports_dir = reports_dir or (ROOT / "reports")
    allure_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    for path in allure_dir.glob("*-result.json"):
        path.unlink(missing_ok=True)
    for path in reports_dir.glob("jira-*.md"):
        path.unlink(missing_ok=True)
    write_empty_catalog(reports_dir)


def publish_manual_case(mapped: dict, allure_dir: Path | None = None, reports_dir: Path | None = None) -> dict:
    allure_dir = allure_dir or (ROOT / "allure-results")
    reports_dir = reports_dir or (ROOT / "reports")
    allure_path = write_allure_files(mapped, allure_dir)
    catalog = upsert_catalog(mapped, reports_dir / "manual-catalog.json")
    html_path = render_catalog_html(catalog, reports_dir / "manual-catalog.html")
    case = catalog["testCases"][-1] if catalog.get("testCases") else None
    md_path = reports_dir / "jira-test-case.md"
    if case and case.get("jira"):
        md_name = re.sub(r"[^A-Za-z0-9]+", "-", case["jira"]["title"]).strip("-")
        md_path = reports_dir / f"jira-{md_name}.md"
        md_path.write_text(jira_markdown(case["jira"]), encoding="utf-8")
    return {
        "allure": str(allure_path),
        "catalogJson": str(reports_dir / "manual-catalog.json"),
        "catalogHtml": str(html_path),
        "jiraMarkdown": str(md_path),
        "count": catalog.get("count"),
    }
