"""Rewrite a mapped Tosca test into a Jira-style manual test case."""
from __future__ import annotations

import re


def _clean_name(name: str) -> str:
    text = name or ""
    text = re.sub(r"\s*\[repeat[^\]]*\]", "", text, flags=re.I)
    text = re.sub(r"TBox\s+", "", text, flags=re.I)
    text = re.sub(r"_[A-Za-z0-9]+Portal\b", "", text)
    text = text.replace("_Settings", "")
    return re.sub(r"\s+", " ", text).strip(" -_")


def _details(node: dict) -> str:
    return " ".join(str(x) for x in (node.get("details") or []))


def _is_skip(node: dict) -> bool:
    name = (node.get("name") or "").lower()
    details = _details(node).lower()
    if name in ("tbox wait",) or name.startswith("tbox wait"):
        return True
    if "set buffer" in name or "delete buffer" in name:
        return True
    if "define excel" in name:
        return True
    if "excel range" in name and "username" not in details and "newpassword" not in details.lower():
        return True
    return False


def humanize_node(node: dict) -> str | None:
    if _is_skip(node):
        return None
    kind = node.get("kind")
    name = _clean_name(node.get("name") or "")
    details = _details(node)
    low = name.lower()
    det = details.lower()

    if kind == "if" or low.startswith("if:"):
        cond = name.replace("IF:", "").strip() or "the condition"
        return f"If {cond} is shown, close it or continue"

    if "taskkill" in det or "taskkill" in low:
        return "Close any running browser windows"
    if "open excel" in low:
        return "Open the Excel test-data file"
    if "excel" in low and "username" in det:
        return "Read each data row from the Excel sheet"
    if "excel" in low and "newpassword" in det.lower():
        return "Write updated values back to Excel and save the file"
    if "close excel" in low:
        return "Save and close the Excel file"
    if "incognito" in det or "-incognito" in det:
        return "Launch the browser in a private/incognito window"
    if "start program" in low and "http" in det:
        return "Open the application URL"
    if "maximize" in low or "window operation" in low:
        return "Maximize the browser window"
    if "login" in low and "credential" in low:
        return "Enter the username and password from Excel, then sign in"
    if "useraccount" in low or (low.startswith("click") and "username" in low):
        return "Click the user account control"
    if low in ("user option",):
        return "Open the user menu"
    if "user settings" in low or low == "user dropdown":
        return "Open user settings"
    if "manage password" in low:
        return "Open password management"
    if "enter required details" in low:
        return "Enter the required field values"
    if "logout" in low:
        return "Wait until the application logs out"
    if "page sync" in low:
        return "Wait for the page to finish loading"
    if re.fullmatch(r"[A-Za-z0-9]+", name) and name[0].isupper() and not name.startswith("Click"):
        return None
    if kind in ("folder", "branch"):
        return None
    if re.search(r"module|tbox|xparam|surrogate", low):
        return None
    if name.lower().startswith("click on"):
        return "Click " + name[8:].strip()
    return name


def _walk(nodes: list[dict], acc: list[str]):
    for node in nodes or []:
        text = humanize_node(node)
        if text:
            if not acc or acc[-1] != text:
                acc.append(text)
        if node.get("kind") == "if":
            continue
        _walk(node.get("children") or [], acc)


def _section(nodes: list[dict], folder_name: str) -> list[dict]:
    key = folder_name.lower()
    for node in nodes or []:
        name = (node.get("name") or "").lower()
        if key in name and node.get("kind") == "folder":
            return node.get("children") or []
    return []


def _dedupe_steps(steps: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for step in steps:
        key = step.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(step)
    return out


def to_jira_case(mapped: dict, tree: list[dict] | None = None) -> dict:
    tc = mapped.get("testCase") or {}
    tcp = tc.get("testConfigurationParameters") or {}
    tree = tree if tree is not None else []
    url = str(tcp.get("Url") or "").strip()
    excel = tcp.get("WorkbookName") or "Users"
    name = tc.get("name") or "Untitled test case"

    pre, steps, post = [], [], []
    _walk(_section(tree, "pre-condition") or tree[:1], pre)
    process = _section(tree, "process")
    if not process:
        process = [
            n
            for n in tree
            if "pre-condition" not in (n.get("name") or "").lower()
            and "post-condition" not in (n.get("name") or "").lower()
        ]
    _walk(process, steps)
    _walk(_section(tree, "post-condition"), post)

    if steps and pre and steps[0] == pre[0] and "browser" in steps[0].lower():
        steps = steps[1:]

    if url:
        placed = False
        for i, s in enumerate(list(steps)):
            if "incognito" in s.lower() or ("launch" in s.lower() and "browser" in s.lower()):
                if i + 1 >= len(steps) or url not in steps[i + 1]:
                    steps.insert(i + 1, f"Navigate to {url}")
                placed = True
                break
        if not placed:
            steps.insert(0, f"Navigate to {url}")

    env = [
        "The required browser is installed",
        f'Excel file "{excel}.xlsx" is available with the required test-data columns',
        "The application URL is reachable",
    ]
    setup = [p for p in pre if p not in env]
    pre = env
    steps = setup + steps
    if not post:
        post = ["Close the browser", "Clear any stored test data"]
    elif not any("browser" in s.lower() or "chrome" in s.lower() for s in post):
        post.insert(0, "Close the browser")

    steps = _dedupe_steps(steps)

    expected = [
        f'"{name}" completes successfully',
        "The application behaves as described in the steps",
    ]
    if any("excel" in s.lower() or "password" in s.lower() for s in steps):
        expected.append(f'Test data in "{excel}.xlsx" is left in a consistent state')

    return {
        "title": name,
        "folder": tc.get("parentFolder") or "",
        "objective": f'Execute the "{name}" scenario using the configured application and test data.',
        "preConditions": pre
        or [
            "The required browser is installed",
            f'Excel file "{excel}.xlsx" is available with the required test-data columns',
            "The application URL is reachable",
        ],
        "steps": steps,
        "expectedResults": expected,
        "postConditions": post,
        "url": url,
        "excel": f"{excel}.xlsx",
    }


def jira_markdown(case: dict) -> str:
    lines = [
        f"Test Case: {case['title']}",
        "",
        "Objective:",
        f"- {case['objective']}",
        "",
        "Pre-Condition:",
    ]
    for item in case["preConditions"]:
        lines.append(f"- {item}")
    lines += [
        "",
        "Steps:",
        "Repeat data-driven steps for each Excel row when this case uses a data file.",
        "",
    ]
    for i, step in enumerate(case["steps"], 1):
        lines.append(f"{i}. {step}")
    lines += ["", "Expected Result:"]
    for item in case["expectedResults"]:
        lines.append(f"- {item}")
    lines += ["", "Post-Condition:"]
    for item in case["postConditions"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)
