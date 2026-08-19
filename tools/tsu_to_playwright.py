"""
Tosca .tsu → Playwright .spec.ts converter.

Handles gzip JSON entities, nested TCP blobs, generic Html locators,
Excel loops, IF conditions, ToscaReporter logging, JSON+HTML reports,
and screenshots on failure.

Usage:
  python tools/tsu_to_playwright.py
  python tools/tsu_to_playwright.py example.tsu
  python tools/tsu_to_playwright.py imports/tsu
  python tools/tsu_to_playwright.py path/to/file.tsu --out tests/generated

Drop Tosca exports in imports/tsu (see tosca.config.json).
"""
from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(Path(__file__).parent))
from manual_report import publish_manual_case, reset_conversion_outputs  # noqa: E402
from project_paths import collect_tsu_files, generated_tests_dir, resolve_tsu_target  # noqa: E402

LOCAL_PATH_RE = re.compile(r"(?i)(^[a-z]:\\|\\\\|/Users/|onedrive)")
BROWSER_PATH_KEYS = {"Path", "EdgePath"}


def sanitize_tcp(tcp) -> dict:
    """Keep TCP values that tests need; drop machine-local paths from exports."""
    if not isinstance(tcp, dict):
        return {}
    workbook = str(tcp.get("WorkbookName") or "Users").strip() or "Users"
    out: dict = {}
    for key, value in tcp.items():
        if key in BROWSER_PATH_KEYS:
            continue
        text = str(value) if value is not None else ""
        if key == "ExcelPath" or text.lower().endswith(".xlsx"):
            out[key] = f"data/{workbook}.xlsx"
            continue
        if LOCAL_PATH_RE.search(text):
            continue
        out[key] = value
    out.setdefault("ExcelPath", f"data/{workbook}.xlsx")
    return out

ACTION_MODE = {
    1: "Select",
    37: "Input",
    69: "WaitOn",
    101: "Constraint",
    165: "Buffer",
    517: "Select",
    519: "Insert",
}


class ToscaModel:
    def __init__(self, entities: list[dict]):
        self.entities = entities
        self.by = {e["Surrogate"]: e for e in entities}

    def A(self, e):
        return (e or {}).get("Attributes") or {}

    def S(self, e, name):
        return ((e or {}).get("Assocs") or {}).get(name) or []

    def one(self, e, name):
        ids = self.S(e, name)
        return ids[0] if ids else None

    def get(self, sid):
        return self.by.get(sid)


def looks_b64(s: str) -> bool:
    if not isinstance(s, str) or len(s) < 24:
        return False
    return all(c.isalnum() or c in "+/=\n\r" for c in s[:24])


def decode_bytes(inner: bytes):
    if inner[:2] == b"\xff\xfe":
        return inner.decode("utf-16")
    if inner[:2] == b"\xfe\xff":
        return inner.decode("utf-16-be")
    try:
        return inner.decode("utf-8")
    except Exception:
        try:
            return inner.decode("utf-16")
        except Exception:
            return None


def parse_tcp_xml(xml_text: str) -> dict:
    props = {}
    for m in re.finditer(r"<TCProperty\s+([^>]+)/>", xml_text):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        if attrs.get("Name"):
            val = (attrs.get("Value") or "").replace("&quot;", '"').replace("&amp;", "&")
            props[attrs["Name"]] = val.strip('"')
    return props


def decode_nested(s):
    if not looks_b64(s):
        return None
    try:
        raw = base64.b64decode(s)
    except Exception:
        return None
    payload = raw
    if raw[:2] == b"\x1f\x8b":
        try:
            payload = gzip.decompress(raw)
        except Exception:
            return None
    txt = decode_bytes(payload)
    if txt is None:
        return None
    stripped = txt.lstrip("\ufeff").lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return json.loads(txt)
        except Exception:
            pass
    if "TCProperty" in txt:
        return parse_tcp_xml(txt)
    return txt


def load_tsu(path: Path) -> dict:
    with gzip.open(path, "rb") as f:
        return json.loads(f.read().decode("utf-8"))


def action_name(code):
    try:
        return ACTION_MODE.get(int(code), f"Unknown({code})")
    except Exception:
        return None


def healing_class(self_healing) -> str | None:
    raw = self_healing
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return None
    if not isinstance(raw, dict):
        return None
    params = (((raw.get("HealingParameters") or {}).get("$values")) or [])
    for item in params:
        if item.get("Name") == "ClassName" and item.get("Value"):
            return item["Value"]
    return None


def map_tree(model: ToscaModel, source: str) -> dict:
    def xparams_of(attr_entity):
        out = {}
        for pid in model.S(attr_entity, "Properties"):
            p = model.get(pid)
            if not p:
                continue
            pa = model.A(p)
            name, val = pa.get("Name"), pa.get("Value")
            out[name] = decode_nested(val) if name == "SelfHealingData" and looks_b64(val) else val
        return out

    def steering_summary(params):
        keys = ["Id", "InnerText", "Title", "Tag", "ClassName", "Name", "Url"]
        return {k: params[k] for k in keys if params.get(k) not in (None, "")}

    def resolve_value_tree(val_id):
        e = model.get(val_id)
        if not e:
            return {"missing": val_id}
        attr = model.get(model.one(e, "ModuleAttribute"))
        params = xparams_of(attr) if attr else {}
        node = {
            "attributeName": model.A(attr).get("Name") if attr else None,
            "businessType": model.A(attr).get("BusinessType") if attr else None,
            "value": model.A(e).get("Value"),
            "actionMode": action_name(model.A(e).get("ActionMode")),
            "steering": steering_summary(params),
            "selfHealing": params.get("SelfHealingData"),
        }
        cls = healing_class(node["selfHealing"])
        if cls and "ClassName" not in node["steering"]:
            node["steering"]["ClassName"] = cls
        subs = model.S(e, "SubValues")
        if subs:
            node["subValues"] = [resolve_value_tree(sid) for sid in subs]
        return node

    def map_step(step_id):
        e = model.get(step_id)
        a = model.A(e)
        mod = model.get(model.one(e, "Module"))
        engine = special = None
        if mod:
            for pid in model.S(mod, "Properties"):
                p = model.get(pid)
                pa = model.A(p)
                if pa.get("Name") == "Engine":
                    engine = pa.get("Value")
                if pa.get("Name") == "SpecialExecutionTask":
                    special = pa.get("Value")
        return {
            "type": "XTestStep",
            "id": step_id,
            "name": a.get("Name"),
            "disabled": a.get("Disabled"),
            "module": model.A(mod).get("Name") if mod else None,
            "engine": engine,
            "specialExecutionTask": special,
            "values": [resolve_value_tree(vid) for vid in model.S(e, "TestStepValues")],
        }

    def map_node(nid, seen=None):
        seen = set() if seen is None else seen
        if nid in seen:
            return {"type": "cycle", "id": nid}
        seen = seen | {nid}
        e = model.get(nid)
        if not e:
            return {"type": "missing", "id": nid}
        oc = e["ObjectClass"]
        a = model.A(e)
        base = {"type": oc, "id": nid, "name": a.get("Name") or None}
        if oc == "XTestStep":
            return map_step(nid)
        if oc == "TestStepFolder":
            base["repetition"] = a.get("Repetition") or None
            base["items"] = [map_node(i, seen) for i in model.S(e, "Items")]
            return base
        if oc == "TestStepFolderReference":
            reused = model.one(e, "ReusedItem")
            base["name"] = a.get("Name") or (model.A(model.get(reused)).get("Name") if reused else None)
            overlays = []
            for prid in model.S(e, "ParameterLayerReference"):
                pr = model.get(prid)
                for pref in model.S(pr, "AllParameterReferences") or model.S(pr, "ParameterReferences"):
                    pe = model.get(pref)
                    param = model.get(model.one(pe, "Parameter")) if pe else None
                    overlays.append({
                        "name": model.A(param).get("Name") if param else None,
                        "value": model.A(pe).get("Value") if pe else None,
                    })
            if overlays:
                base["parameterOverlays"] = overlays
            if reused:
                base["items"] = [map_node(i, seen) for i in model.S(model.get(reused), "Items")]
            return base
        if oc == "TestCaseControlFlowItem":
            branches = []
            for fid in model.S(e, "ControlFlowFolders"):
                fe = model.get(fid)
                branches.append({
                    "name": model.A(fe).get("Name"),
                    "statementType": model.A(fe).get("StatementType"),
                    "items": [map_node(i, seen) for i in model.S(fe, "Items")],
                })
            base["branches"] = branches
            return base
        base["repetition"] = a.get("Repetition") or None
        base["items"] = [map_node(i, seen) for i in model.S(e, "Items")]
        return base

    tc = next(e for e in model.entities if e["ObjectClass"] == "TestCase")
    tcp = decode_nested(model.A(tc).get("TestConfigurationParameters")) or {}
    if isinstance(tcp, str):
        tcp = {}
    tcp = sanitize_tcp(tcp)
    parent = model.get(model.one(tc, "ParentFolder"))
    project = next((e for e in model.entities if e["ObjectClass"] == "TCProject"), None)
    return {
        "source": Path(source).name,
        "project": model.A(project).get("Name") if project else None,
        "testCase": {
            "id": tc["Surrogate"],
            "name": model.A(tc).get("Name"),
            "parentFolder": model.A(parent).get("Name") if parent else None,
            "testConfigurationParameters": tcp,
        },
        "flow": [map_node(i) for i in model.S(tc, "Items")],
    }


def flatten_values(values):
    out = []
    for v in values or []:
        out.append(v)
        out.extend(flatten_values(v.get("subValues") or []))
    return out


def js_str(value) -> str:
    return json.dumps("" if value is None else str(value))


def playwright_locator(steering: dict | None, attr_name: str | None = None) -> str | None:
    if not steering:
        return None
    tag = (steering.get("Tag") or "").lower()
    el_id = steering.get("Id")
    title = steering.get("Title")
    inner = re.sub(r"\s+", " ", (steering.get("InnerText") or "")).strip()
    cls = steering.get("ClassName")
    if el_id:
        return f"page.locator({js_str('#' + el_id)})"
    if title:
        sel = f"{tag or '*'}[title={json.dumps(title)}]"
        return f"page.locator({js_str(sel)})"
    if inner:
        if "close" in inner.lower() and len(inner) < 40:
            return 'page.getByRole("button", { name: /close/i })'
        if tag == "a":
            return f"page.getByRole(\"link\", {{ name: {js_str(inner)} }})"
        return f"page.getByText({js_str(inner)})"
    if tag == "a" and attr_name:
        return f"page.getByRole(\"link\", {{ name: {js_str(attr_name)} }})"
    if cls:
        classes = ".".join(c for c in cls.split() if c)
        sel = f"{tag}.{classes}" if tag else f".{classes}"
        return f"page.locator({js_str(sel)})"
    if tag:
        return f"page.locator({js_str(tag)})"
    return None


def resolve_to_js(value, attr_name=None, in_loop=False) -> str | None:
    if value is None:
        return None
    raw = str(value)
    if attr_name and "password" in attr_name.lower() and "{" not in raw and len(raw) > 40:
        return 'String(row.Password ?? "")'
    inner = raw
    while True:
        m = re.fullmatch(r"\{TextInput\[(.*)\]\}", inner, re.DOTALL)
        if not m:
            break
        inner = m.group(1)
    if re.search(r"\{RANDOMREGEX\[", inner):
        return "newPassword" if in_loop else "randomToscaPassword()"
    if inner in ("{Click}", "X", "x"):
        return None
    if inner == "{NULL}":
        return None
    m = re.fullmatch(r"\{CP\[([^\]]+)\]\}", inner)
    if m:
        key = m.group(1)
        if key.lower().startswith("excel"):
            return "excelPath"
        return f"config[{js_str(key)}]"
    m = re.fullmatch(r"\{PL\[([^\]]+)\]\}", inner)
    if m:
        key = m.group(1)
        mapping = {
            "Username": "String(row.Username ?? username)",
            "Password": "String(row.Password ?? password)",
            "Url": "config.Url",
            "ChromePath": "config.Path",
            "EdgePath": "config.EdgePath || ''",
            "Caption": "config.Caption",
            "Argument": "config.Argument",
        }
        return mapping.get(key, f"config[{js_str(key)}]")
    m = re.fullmatch(r"\{B\[([^\]]+)\]\}", inner)
    if m:
        buf = m.group(1)
        if "Username" in buf:
            return "String(row.Username ?? username)"
        if "Newpassword" in buf or "NewPassword" in buf:
            return "newPassword"
        if "Password" in buf:
            return "String(row.Password ?? password)"
        if buf in ("Repeat", "Rows"):
            return "data.length"
        return f"buffers[{js_str(buf)}]"
    if inner in ("True", "False"):
        return inner.lower()
    return js_str(inner)


def has_html(node: dict) -> bool:
    t = node.get("type")
    if t == "XTestStep":
        return (node.get("engine") or "").lower() == "html"
    for child in node.get("items") or []:
        if has_html(child):
            return True
    for branch in node.get("branches") or []:
        for child in branch.get("items") or []:
            if has_html(child):
                return True
    return False


def is_repeat(node: dict) -> bool:
    rep = str(node.get("repetition") or "")
    return "Repeat" in rep or "Repetition" in rep


def first_locator_from_steps(items) -> str | None:
    for item in items or []:
        if item.get("type") == "XTestStep":
            for v in flatten_values(item.get("values")):
                loc = playwright_locator(v.get("steering") or {}, v.get("attributeName"))
                if loc:
                    return loc
        loc = first_locator_from_steps(item.get("items") or [])
        if loc:
            return loc
    return None


class SpecWriter:
    def __init__(self, mapped: dict, tsu_path: str):
        self.mapped = mapped
        self.tsu_path = tsu_path
        self.tc = mapped["testCase"]
        self.tcp = self.tc.get("testConfigurationParameters") or {}
        self.lines: list[str] = []
        self.excel_loaded = False
        self.goto_emitted = False
        self.in_loop = False

    def add(self, indent: int, text: str = ""):
        for line in text.split("\n"):
            self.lines.append((" " * indent + line) if line else "")

    def wrap_step(self, indent: int, name: str, module: str | None, body_lines: list[str], use_page=True):
        extras = "{ page }" if use_page else "{}"
        if module:
            extras = f"{{ module: {js_str(module)}, page }}" if use_page else f"{{ module: {js_str(module)} }}"
        self.add(indent, f"await reporter.step({js_str(name)}, async () => {{")
        if not body_lines:
            self.add(indent + 2, "// no Playwright equivalent")
        else:
            for line in body_lines:
                self.add(indent + 2, line)
        self.add(indent, f"}}, {extras});")

    def emit_framework(self, step: dict, indent: int):
        name = step.get("name") or "Framework step"
        task = step.get("specialExecutionTask") or ""
        values = flatten_values(step.get("values"))
        by_attr = {v.get("attributeName"): v.get("value") for v in values if v.get("attributeName")}

        if task == "StartProgram" or "Start Program" in name:
            path_val = str(by_attr.get("Path") or "")
            if "taskkill" in path_val.lower() or "TaskKill" in (step.get("name") or ""):
                self.wrap_step(indent, name, step.get("module"), ["// Tosca TBox Start Program taskkill — Playwright owns the browser."], False)
                return
            if "Edge" in name and str(self.tcp.get("Browser", "Chrome")).lower() == "chrome":
                self.add(indent, f"// Skipped Edge launch; TCP Browser={self.tcp.get('Browser')}")
                return
            if not self.goto_emitted:
                self.wrap_step(indent, name, step.get("module"), ["await page.goto(config.Url, { waitUntil: 'domcontentloaded' });"])
                self.goto_emitted = True
            else:
                self.add(indent, f"// {name}: URL already opened")
            return

        if task == "Wait" or name.startswith("TBox Wait") or "Wait for" in name:
            duration = 1000
            for v in values:
                if v.get("attributeName") == "Duration":
                    try:
                        duration = int(v.get("value") or 1000)
                    except Exception:
                        duration = 1000
            self.wrap_step(indent, name, step.get("module"), [f"await page.waitForTimeout({min(duration, 8000)});"])
            return

        if task == "WindowOperation" or "Window Operation" in name:
            self.wrap_step(indent, name, step.get("module"), ["await page.setViewportSize({ width: 1920, height: 1080 });"])
            return

        if task in ("OpenExcelFile", "DefineExcelRange", "CloseExcelFile") or "Excel" in (step.get("module") or ""):
            self.emit_excel(step, indent)
            return

        if task == "SetBuffer":
            self.wrap_step(indent, name, step.get("module"), ["// Repeat count comes from data.length after Excel load."], False)
            return

        if task == "DeleteBuffer":
            self.wrap_step(indent, name, step.get("module"), ["// No Tosca buffers in Playwright; Excel rows are the data store."], False)
            return

        self.wrap_step(indent, name, step.get("module"), [f"// Unmapped framework task {task or name}"], False)

    def emit_excel(self, step: dict, indent: int):
        name = step.get("name") or "Excel"
        values = flatten_values(step.get("values"))
        writes_new = any(
            v.get("actionMode") in ("Insert", "Input")
            and v.get("value")
            and "Newpassword" in str(v.get("value"))
            for v in values
        )
        buffers_users = any("Username_" in str(v.get("value") or "") for v in values)
        if not self.excel_loaded:
            self.wrap_step(
                indent,
                name,
                step.get("module"),
                [
                    "data = loadUsers(excelPath);",
                    "expect(data.length, 'Excel should contain user rows').toBeGreaterThan(0);",
                ],
                False,
            )
            self.excel_loaded = True
            return
        if writes_new:
            self.wrap_step(
                indent,
                name,
                step.get("module"),
                [
                    "row.Password = newPassword;",
                    "row.NewPassword = newPassword;",
                    "saveUsers(excelPath, data);",
                ],
                False,
            )
            return
        if buffers_users:
            self.add(indent, f"// {name}: usernames/passwords already loaded via xlsx")
            return
        self.add(indent, f"// {name}: Excel range already mapped")

    def emit_html(self, step: dict, indent: int):
        name = step.get("name") or "Html step"
        actions = []
        for v in flatten_values(step.get("values")):
            am = v.get("actionMode")
            val = v.get("value")
            loc = playwright_locator(v.get("steering") or {}, v.get("attributeName"))
            if not loc:
                continue
            if am in ("WaitOn", "Constraint") and str(val) == "True":
                actions.append(f"await expect({loc}.first()).toBeVisible();")
            elif am == "Input" and str(val) in ("{Click}", "X", "x"):
                actions.append(f"await {loc}.first().click();")
            elif am in ("Input", "Insert") and resolve_to_js(val, v.get("attributeName"), self.in_loop) is not None:
                js_val = resolve_to_js(val, v.get("attributeName"), self.in_loop)
                actions.append(f"await {loc}.first().fill({js_val});")
            elif am == "Buffer" and resolve_to_js(val, v.get("attributeName"), self.in_loop) == "newPassword":
                actions.append("// new password already captured in newPassword")
        # de-dupe consecutive identical lines
        deduped = []
        for line in actions:
            if not deduped or deduped[-1] != line:
                deduped.append(line)
        self.wrap_step(indent, name, step.get("module"), deduped or ["// no actionable Html values"])

    def emit_step(self, step: dict, indent: int):
        engine = (step.get("engine") or "").lower()
        module = step.get("module") or ""
        if str(step.get("disabled")) in ("1", "true", "True"):
            self.add(indent, f"// Disabled: {step.get('name')}")
            return
        if engine == "html":
            self.emit_html(step, indent)
        elif engine in ("excel",) or "Excel" in module:
            self.emit_excel(step, indent)
        else:
            self.emit_framework(step, indent)

    def emit_if(self, item: dict, indent: int):
        cond = next((b for b in item.get("branches") or [] if (b.get("name") or "").lower() == "condition"), None)
        then = next((b for b in item.get("branches") or [] if (b.get("name") or "").lower() == "then"), None)
        loc = first_locator_from_steps((cond or {}).get("items") or [])
        module = None
        cond_items = (cond or {}).get("items") or []
        if cond_items and cond_items[0].get("module"):
            module = cond_items[0].get("module")
        if loc:
            extra = f", {{ module: {js_str(module)}, timeoutMs: 8000 }}" if module else ", { timeoutMs: 8000 }"
            self.add(indent, f"await reporter.ifVisible({js_str(item.get('name') or 'IF')}, page, {loc}.first(){extra});")
            return
        self.add(indent, f"// IF {item.get('name')}: no locator on condition")
        if then:
            self.walk(then.get("items") or [], indent)

    def walk(self, items: list, indent: int):
        for item in items or []:
            t = item.get("type")
            if t in ("TestStepFolder", "TestStepFolderReference", "ReuseableTestStepBlock"):
                name = item.get("name") or t
                if is_repeat(item) and has_html(item) and not self.in_loop:
                    self.add(indent, f"reporter.startFolder({js_str(name)});")
                    self.add(indent, "for (const [rowIndex, row] of data.entries()) {")
                    self.add(indent + 2, "reporter.startIteration(`Row ${rowIndex + 1} (${row.Username || 'blank'})`);")
                    self.add(indent + 2, "const username = String(row.Username ?? '');")
                    self.add(indent + 2, "const password = String(row.Password ?? '');")
                    self.add(indent + 2, "const newPassword = randomToscaPassword();")
                    self.in_loop = True
                    self.goto_emitted = False
                    self.walk(item.get("items") or [], indent + 2)
                    self.in_loop = False
                    self.add(indent, "}")
                elif is_repeat(item) and not has_html(item):
                    self.add(indent, f"// Excel buffer loop '{name}' replaced by xlsx.sheet_to_json")
                else:
                    self.add(indent, f"reporter.startFolder({js_str(name)});")
                    self.walk(item.get("items") or [], indent)
            elif t == "TestCaseControlFlowItem":
                self.emit_if(item, indent)
            elif t == "XTestStep":
                self.emit_step(item, indent)
            else:
                self.walk(item.get("items") or [], indent)

    def render(self, import_prefix: str) -> str:
        name = self.tc.get("name") or "Tosca Test"
        tcp_js = json.dumps(self.tcp, indent=2)
        workbook = str(self.tcp.get("WorkbookName") or "Users")
        excel_default = f"data/{workbook}.xlsx"
        header = f"""import {{ test, expect }} from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import * as xlsx from 'xlsx';
import {{ ToscaReporter }} from '{import_prefix}/reporting/toscaReporter';

// Generated from {Path(self.tsu_path).name}
const config = {tcp_js} as Record<string, string>;
const excelPath = process.env.TOSCA_EXCEL || {js_str(excel_default)};

type UserRow = {{ Username?: string; Password?: string; NewPassword?: string; [k: string]: unknown }};
const buffers: Record<string, string> = {{}};

function randomToscaPassword(): string {{
  const lower = 'abcdefghijklmnopqrstuvwxyz';
  const upper = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
  const special = '!@#$%^&*';
  const pick = (set: string) => set[Math.floor(Math.random() * set.length)];
  const digit = () => String(Math.floor(Math.random() * 10));
  return `${{pick(lower)}}${{pick(special)}}${{pick(upper)}}${{pick(upper)}}${{digit()}}${{digit()}}${{pick(lower)}}${{pick(special)}}${{pick(upper)}}`;
}}

function loadUsers(filePath: string): UserRow[] {{
  const full = path.resolve(filePath);
  if (!fs.existsSync(full)) throw new Error('Excel file not found: ' + full);
  const workbook = xlsx.readFile(full);
  const sheetName = 'Sheet1';
  const sheet = workbook.Sheets[sheetName] || workbook.Sheets[workbook.SheetNames[0]];
  return xlsx.utils.sheet_to_json<UserRow>(sheet);
}}

function saveUsers(filePath: string, rows: UserRow[]): void {{
  const worksheet = xlsx.utils.json_to_sheet(rows);
  const workbook = xlsx.utils.book_new();
  xlsx.utils.book_append_sheet(workbook, worksheet, 'Sheet1');
  xlsx.writeFile(workbook, path.resolve(filePath));
}}

test({js_str(name)}, async ({{ page }}) => {{
  const reporter = new ToscaReporter({{
    testCase: {js_str(name)},
    folder: {js_str(self.tc.get("parentFolder") or "")},
    sourceTsu: {js_str(self.tsu_path)},
    captureScreenshotsOnFailure: true,
  }});
  let data: UserRow[] = [];
  let runStatus: 'PASSED' | 'FAILED' = 'PASSED';
  try {{
"""
        self.lines = []
        self.walk(self.mapped.get("flow") or [], 4)
        body = "\n".join(self.lines)
        footer = """
  } catch (error) {
    runStatus = 'FAILED';
    throw error;
  } finally {
    reporter.finish(runStatus);
  }
});
"""
        return header + body + footer


def relative_import(out_dir: Path) -> str:
    rel = Path(os.path.relpath(ROOT / "src", out_dir)).as_posix()
    if not rel.startswith("."):
        rel = "./" + rel
    return rel


def generate_playwright(mapped: dict, tsu_path: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9]+", "_", mapped["testCase"]["name"]).strip("_")
    filename = out_dir / f"{safe}.spec.ts"
    import_prefix = relative_import(out_dir)
    writer = SpecWriter(mapped, tsu_path.name)
    filename.write_text(writer.render(import_prefix), encoding="utf-8")
    return filename


def collect_tsu_paths(target: Path) -> list[Path]:
    return collect_tsu_files(target)


def convert_one(tsu_path: Path, out_dir: Path, mapped_dir: Path | None = None) -> dict:
    print(f"Loading {tsu_path} ...")
    raw = load_tsu(tsu_path)
    model = ToscaModel(raw["Entities"])
    mapped = map_tree(model, str(tsu_path))
    spec = generate_playwright(mapped, tsu_path, out_dir)
    published = publish_manual_case(mapped)
    print(f"  Playwright:  {spec}")
    print(f"  Allure:      {published['allure']}")
    if mapped_dir:
        mapped_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9]+", "_", mapped["testCase"]["name"] or tsu_path.stem).strip("_")
        mapped_path = mapped_dir / f"{safe}.mapped.json"
        mapped_path.write_text(json.dumps(mapped, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  Mapped JSON: {mapped_path}")
    return {"mapped": mapped, "spec": spec, "report": published}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Convert Tosca .tsu export to Playwright spec + manual Allure catalog")
    parser.add_argument(
        "tsu",
        nargs="?",
        default=None,
        help="File or folder. Default: imports/tsu (tosca.config.json → tsuImportDir)",
    )
    parser.add_argument("--out", default=None, help="Playwright spec output directory")
    parser.add_argument("--keep-mapped", action="store_true", help="Write mapped JSON next to generated specs")
    args = parser.parse_args(argv)

    target = resolve_tsu_target(args.tsu)
    if not target.exists():
        print(f"TSU not found: {target}", file=sys.stderr)
        print("Drop .tsu files into imports/tsu and run again.", file=sys.stderr)
        return 1

    paths = collect_tsu_paths(target)
    if not paths:
        print(f"No .tsu files in {target}", file=sys.stderr)
        print("Copy Tosca exports into imports/tsu (common import location).", file=sys.stderr)
        return 1

    out_dir = Path(args.out) if args.out else generated_tests_dir()
    mapped_dir = out_dir / "mapped" if args.keep_mapped else None
    reset_conversion_outputs()

    for tsu_path in paths:
        convert_one(tsu_path, out_dir, mapped_dir)

    catalog = ROOT / "reports" / "manual-catalog.html"
    print(f"\nManual catalog (all test cases): {catalog}")
    print("Allure results: allure-results/")
    print("Open catalog:  reports/manual-catalog.html")
    print("Open Allure:   npx allure generate allure-results --clean -o allure-report && npx allure open allure-report")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
