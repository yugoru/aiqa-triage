"""ИИ-триаж падений автотестов — конвейер из шести стадий.

    report.xml → parse → enrich (код теста + контракт из спеки)
               → triage (DeepSeek V4, structured output + Pydantic)
               → group (детерминированный фингерпринт)
               → export (verdicts.json + triage.md + issues.json [+ POST /issues])

Принцип разделения труда: детерминированный код делает всё, что требует
гарантий (парсинг, поиск контекста, группировка, валидация, заведение багов);
модель делает только то, что требует смысла (категория, причина, улики, действие).

Запуск:
    python analyzer/analyzer.py                    # JUnit: reports/report.xml → output/
    python analyzer/analyzer.py reports/report.xml # JUnit: явный отчёт
    python analyzer/analyzer.py reports/allure-results  # Allure: каталог определяется авто
    python analyzer/analyzer.py --from-allure      # Allure: reports/allure-results
    python analyzer/analyzer.py --dump-context     # показать пакеты контекста и выйти
    python analyzer/analyzer.py --no-enrich        # без обогащения кодом/контрактом
    python analyzer/analyzer.py --escalate         # спорные переспросить умной моделью
    python analyzer/analyzer.py --file-issues      # + завести черновики в POST /issues
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from deepseek import DeepSeekClient, LLMError, TriageResult, Usage  # noqa: E402
from schema import Verdict  # noqa: E402

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
ANALYZER_DIR = Path(__file__).parent


def _read_asset(name: str) -> str:
    """Прочитать файл рядом с анализатором, с понятной ошибкой вместо трейсбека."""
    try:
        return (ANALYZER_DIR / name).read_text(encoding="utf-8")
    except OSError as err:
        sys.exit(f"[!] Не удалось прочитать {name}: {err}")


PROMPT = _read_asset("prompt.md")
try:
    SPEC = json.loads(_read_asset("openapi_snapshot.json"))
except json.JSONDecodeError as err:
    sys.exit(f"[!] openapi_snapshot.json повреждён: {err}")

BOOKSHELF_URL = os.getenv("BOOKSHELF_URL", "https://qahacking.up.railway.app")
ISSUES_URL = os.getenv("ISSUES_URL", f"{BOOKSHELF_URL}/issues")

CATEGORY_ORDER = [
    "продукт",
    "код_автотеста",
    "инфраструктура",
    "инструментарий",
    "flaky",
]

# --------------------------------------------------------------------------- #
# Цветной вывод в терминал. Гасим цвет вне TTY и при переменной NO_COLOR.      #
# --------------------------------------------------------------------------- #
_ANSI = {
    "продукт": "31",
    "код_автотеста": "33",
    "инфраструктура": "34",
    "инструментарий": "35",
    "flaky": "36",
    "dim": "2",
    "bold": "1",
}
_USE_COLOR = sys.stdout.isatty() and not os.getenv("NO_COLOR")


def paint(text: str, key: str) -> str:
    if not _USE_COLOR or key not in _ANSI:
        return text
    return f"\033[{_ANSI[key]}m{text}\033[0m"


# --------------------------------------------------------------------------- #
# 1. Парсинг отчёта: junit.xml (по умолчанию) или allure-results (--from-allure)
# --------------------------------------------------------------------------- #
@dataclass
class Failure:
    test: str  # tests.test_books::test_book_fields
    kind: str  # "failure" | "error"
    message: str
    trace: str
    system_out: str = ""
    steps: str = ""  # шаги и вложения из Allure (запросы/ответы) — только allure-вход
    source: str = ""  # код теста (заполняется на стадии enrich)
    fixture_src: str = ""  # код задетой фикстуры (для ошибок на setup)
    contracts: list[str] = field(default_factory=list)


def parse_junit(path: Path) -> list[Failure]:
    """junit.xml — минимальный общий знаменатель: message + trace + stdout."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as err:
        sys.exit(f"[!] Не удалось разобрать JUnit-XML {path}: {err}")
    failures = []
    for case in root.iter("testcase"):
        node = case.find("failure")
        kind = "failure"
        if node is None:
            node = case.find("error")
            kind = "error"
        if node is None:
            continue
        out = case.find("system-out")
        failures.append(
            Failure(
                test=f"{case.get('classname', '')}::{case.get('name', '')}",
                kind=kind,
                message=(node.get("message") or "")[:600],
                trace=(node.text or "")[:4000],
                system_out=(out.text or "")[:1000] if out is not None else "",
            )
        )
    return failures


def _read_attachment(results_dir: Path, source: str) -> str:
    """Прочитать текстовое вложение Allure (лежит рядом с *-result.json)."""
    if not source:
        return ""
    path = results_dir / source
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _flatten_steps(steps: list[dict], results_dir: Path, depth: int = 0) -> list[str]:
    """Развернуть дерево шагов Allure в текст со статусами и телами вложений.

    Именно здесь «жирность» allure-входа: во вложениях лежат реальные
    параметры, тела запросов и ответы — то, чего в junit.xml нет вовсе.
    """
    marks = {"passed": "✓", "failed": "✗", "broken": "⚠", "skipped": "∅"}
    lines: list[str] = []
    for step in steps:
        indent = "  " * depth
        mark = marks.get(step.get("status", ""), "•")
        lines.append(f"{indent}{mark} {step.get('name', '')}")
        for att in step.get("attachments", []):
            content = _read_attachment(results_dir, att.get("source", ""))
            if content:
                lines.append(
                    f"{indent}    [{att.get('name', 'вложение')}]: {content[:800]}"
                )
        lines += _flatten_steps(step.get("steps", []), results_dir, depth + 1)
    return lines


def parse_allure(results_dir: Path) -> list[Failure]:
    """allure-results — «жирный» вход: шаги + реальные запросы/ответы из вложений.

    В отличие от junit.xml, здесь доступны пошаговые данные и содержимое
    HTTP-запросов/ответов (если тесты обвязаны allure.step + allure.attach).
    """
    failures = []
    for jf in sorted(results_dir.glob("*-result.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as err:
            print(f"[i] пропускаю битый {jf.name}: {err}")
            continue
        status = data.get("status")
        if status not in ("failed", "broken"):
            continue  # passed/skipped/unknown — не триажим
        details = data.get("statusDetails", {})
        name = data.get("name", "")
        full = data.get("fullName", name)

        step_lines = _flatten_steps(data.get("steps", []), results_dir)
        for att in data.get("attachments", []):  # вложения уровня теста
            content = _read_attachment(results_dir, att.get("source", ""))
            if content:
                step_lines.append(f"[{att.get('name', 'вложение')}]: {content[:800]}")
        params = ", ".join(
            f"{p.get('name')}={p.get('value')}" for p in data.get("parameters", [])
        )
        if params:
            step_lines.insert(0, f"параметры теста: {params}")

        failures.append(
            Failure(
                test=f"{full}::{name}",
                # allure: failed = упавший assert, broken = исключение до/вне assert
                kind="failure" if status == "failed" else "error",
                message=(details.get("message") or "")[:600],
                trace=(details.get("trace") or "")[:4000],
                steps="\n".join(step_lines)[:3500],
            )
        )
    return failures


# --------------------------------------------------------------------------- #
# 2. Обогащение: код теста и контракт из спеки — то, чего нет в отчёте         #
# --------------------------------------------------------------------------- #
def find_test_source(test_name: str) -> str:
    """Ищем функцию теста в tests/ и conftest.py — модель должна видеть код."""
    func = test_name.split("::")[-1]
    for py in [ROOT / "conftest.py", *sorted((ROOT / "tests").glob("*.py"))]:
        text = py.read_text(encoding="utf-8")
        match = re.search(
            rf"^def {re.escape(func)}\(.*?(?=^def |\Z)", text, re.M | re.S
        )
        if match:
            header = "\n".join(
                line
                for line in text.splitlines()[:25]
                if line.startswith(("import", "from", "BASE_URL", "GENRES_URL"))
            )
            return f"# {py.name}\n{header}\n\n{match.group(0).rstrip()}"
    return ""


def find_fixture_source(message: str, trace: str) -> str:
    """Для ошибок на setup: вытащить код задетой фикстуры из conftest.py.

    Падение случилось не в тесте, а в обвязке — модель должна видеть код фикстуры,
    иначе не отличит «инструментарий» от «код_автотеста».
    """
    blob = f"{message}\n{trace}"
    names = set(re.findall(r"argname='(\w+)'", blob)) | set(
        re.findall(r"SubRequest '(\w+)'", blob)
    )
    conftest = ROOT / "conftest.py"
    if not names or not conftest.exists():
        return ""
    text = conftest.read_text(encoding="utf-8")
    chunks = []
    for name in sorted(names):
        match = re.search(
            rf"^(?:@pytest\.fixture[^\n]*\n)?def {re.escape(name)}\(.*?(?=^def |^@pytest|\Z)",
            text,
            re.M | re.S,
        )
        if match:
            chunks.append(match.group(0).rstrip())
    return "# conftest.py (обвязка/фикстуры)\n" + "\n\n".join(chunks) if chunks else ""


def find_contracts(source: str, trace: str) -> list[str]:
    """Контракт — источник истины: описания задетых эндпоинтов из снапшота спеки."""
    mentioned = set(
        re.findall(
            r"/(?:practice|books|authors|genres|readers|auth|orders|reviews|teapot|issues)[\w/{}-]*",
            source + trace,
        )
    )
    contracts = []
    for path, methods in SPEC.get("paths", {}).items():
        template = re.sub(r"\{\w+\}", "", path)
        if any(
            m.rstrip("/").startswith(template.rstrip("/"))
            or template.rstrip("/").startswith(m.rstrip("/"))
            for m in mentioned
            if len(m) > 3
        ):
            for method, op in methods.items():
                if method in ("get", "post", "put", "patch", "delete"):
                    desc = (op.get("description") or op.get("summary") or "").strip()
                    if desc:
                        contracts.append(f"{method.upper()} {path}\n{desc[:500]}")
    return contracts[:4]


def enrich(f: Failure, *, do_enrich: bool = True) -> None:
    """Дополнить падение кодом теста и контрактом (если обогащение включено)."""
    if not do_enrich:
        return
    f.source = find_test_source(f.test)
    f.fixture_src = find_fixture_source(f.message, f.trace)
    f.contracts = find_contracts(f.source + f.fixture_src, f.trace)


def build_context(f: Failure) -> str:
    """Пакет контекста падения — качество вердикта решается здесь."""
    parts = [
        f"## Тест: {f.test}",
        f"## Тип: {f.kind} ("
        + (
            "assert не сошёлся — тело теста выполнялось"
            if f.kind == "failure"
            else "ОШИБКА НА SETUP: тело теста не выполнялось, упала обвязка (фикстура/conftest) или окружение"
        )
        + ")",
        f"## Сообщение:\n{f.message}",
        f"## Трейсбек:\n{f.trace}",
    ]
    if f.system_out:
        parts.append(f"## stdout:\n{f.system_out}")
    if f.steps:
        parts.append(f"## Шаги и данные из Allure (запросы/ответы):\n{f.steps}")
    if f.source:
        parts.append(f"## Код теста:\n{f.source}")
    if f.fixture_src:
        parts.append(f"## Код обвязки (фикстура на setup):\n{f.fixture_src}")
    for contract in f.contracts:
        parts.append(f"## Контракт из OpenAPI:\n{contract}")
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# 4. Группировка: одна причина — один инцидент (делает код, не модель)         #
# --------------------------------------------------------------------------- #
def fingerprint(f: Failure, v: Verdict) -> str:
    """Детерминированный отпечаток: категория + нормализованная первая строка."""
    line = (
        f.message.splitlines()[0]
        if f.message
        else (f.trace.splitlines()[0] if f.trace else "")
    )
    normalized = re.sub(r"[0-9a-f]{8,}|\d+", "N", line.lower())[:120]
    return f"{v.category.value}|{normalized}"


def group(failures: list[Failure], verdicts: list[Verdict]) -> dict[str, dict]:
    groups: dict[str, dict] = {}
    for f, v in zip(failures, verdicts):
        key = fingerprint(f, v)
        groups.setdefault(key, {"verdict": v, "tests": []})["tests"].append(f.test)
    return groups


# --------------------------------------------------------------------------- #
# 5. Выходные артефакты                                                        #
# --------------------------------------------------------------------------- #
def write_triage_md(groups: dict, total_failed: int, out: Path) -> None:
    """Сводка к стендапу: сгруппирована по категориям, ⚠ у needs_human."""
    lines = [
        "# Триаж прогона",
        "",
        f"Красных тестов: **{total_failed}**, причин после группировки: **{len(groups)}**",
        "",
    ]
    for category in CATEGORY_ORDER:
        chunk = [g for g in groups.values() if g["verdict"].category.value == category]
        if not chunk:
            continue
        lines.append(f"## {category} — причин: {len(chunk)}")
        lines.append("")
        for g in chunk:
            v = g["verdict"]
            flag = " ⚠ **needs_human**" if v.needs_human else ""
            lines += [
                f"### {v.root_cause}{flag}",
                f"- тесты ({len(g['tests'])}): {', '.join(g['tests'])}",
                f"- уверенность: {v.confidence:.2f}",
                f"- улики: {v.evidence}",
                f"- действие: {v.suggested_action}",
                "",
            ]
    out.write_text("\n".join(lines), encoding="utf-8")


_HTML_COLORS = {
    "продукт": "#e5484d",
    "код_автотеста": "#f76b15",
    "инфраструктура": "#3e63dd",
    "инструментарий": "#8e4ec6",
    "flaky": "#e0a800",
}

_HTML_STYLE = """
:root{--bg:#f4f5fb;--card:#fff;--fg:#1a1a2e;--muted:#6b7280;--line:#e6e8f0;--accent:#4f46e5}
@media(prefers-color-scheme:dark){:root{--bg:#0f1117;--card:#181b24;--fg:#e6e7ee;--muted:#9aa0ac;--line:#272b36;--accent:#8b8aff}}
*{box-sizing:border-box}
body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;background:var(--bg);color:var(--fg);line-height:1.5}
header{position:sticky;top:0;z-index:5;background:var(--accent);color:#fff;padding:18px 32px;box-shadow:0 2px 12px rgba(0,0,0,.18)}
header h1{margin:0;font-size:21px}
header .sub{opacity:.92;font-size:13.5px;margin-top:4px}
main{max-width:960px;margin:0 auto;padding:22px 20px 60px}
.tiles{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:20px}
.tile{flex:1;min-width:120px;background:var(--card);border:1px solid var(--line);border-top:3px solid var(--tc);border-radius:11px;padding:12px 14px;cursor:pointer;transition:.15s;user-select:none}
.tile:hover{transform:translateY(-2px);box-shadow:0 4px 14px rgba(0,0,0,.08)}
.tile.off{opacity:.35}
.tile .n{font-size:26px;font-weight:800;line-height:1}
.tile .l{font-size:12px;color:var(--muted);margin-top:3px}
.card{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--cc);border-radius:12px;padding:15px 18px;margin:12px 0;box-shadow:0 1px 3px rgba(0,0,0,.05)}
.card h3{margin:0 0 4px;font-size:15px;font-weight:650;display:flex;gap:8px;align-items:flex-start;flex-wrap:wrap}
.pill{font-size:11px;font-weight:700;padding:2px 10px;border-radius:20px;white-space:nowrap}
.pill.cat{color:#fff}
.pill.nh{background:#fde68a;color:#8a5a00}
.conf{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--muted);margin:9px 0}
.bar{flex:0 0 150px;height:6px;border-radius:3px;background:var(--line);overflow:hidden}
.bar>i{display:block;height:100%}
.tests{display:flex;flex-wrap:wrap;gap:6px;margin:9px 0}
.tests span{font-family:ui-monospace,Consolas,monospace;font-size:12px;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:2px 8px}
.ev{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px 11px;font-size:13px;margin-top:8px}
.act{font-size:14px;margin-top:9px}
.ev b,.act b{color:var(--accent)}
"""

_HTML_SCRIPT = (
    "<script>document.querySelectorAll('.tile').forEach(t=>t.onclick=()=>{"
    "t.classList.toggle('off');var c=t.dataset.cat;"
    "document.querySelectorAll('.card[data-cat=\"'+c+'\"]').forEach("
    "el=>el.style.display=t.classList.contains('off')?'none':'')})</script>"
)


def write_triage_html(groups: dict, total_failed: int, out: Path) -> None:
    """Триаж как самодостаточная HTML-страница: стат-плитки, фильтр, тёмная тема."""
    esc = html.escape
    per_cat = {c: 0 for c in CATEGORY_ORDER}
    for g in groups.values():
        per_cat[g["verdict"].category.value] += 1

    tiles = "".join(
        f'<div class="tile" data-cat="{c}" style="--tc:{_HTML_COLORS[c]}">'
        f'<div class="n" style="color:{_HTML_COLORS[c]}">{per_cat[c]}</div>'
        f'<div class="l">{esc(c)}</div></div>'
        for c in CATEGORY_ORDER
        if per_cat[c]
    )

    cards: list[str] = []
    for category in CATEGORY_ORDER:
        for g in [
            g for g in groups.values() if g["verdict"].category.value == category
        ]:
            v = g["verdict"]
            color = _HTML_COLORS[category]
            cbar = (
                "#16a34a"
                if v.confidence >= 0.8
                else "#e0a800"
                if v.confidence >= 0.6
                else "#dc2626"
            )
            nh = '<span class="pill nh">⚠ нужен человек</span>' if v.needs_human else ""
            tests = "".join(
                f"<span>{esc(t.split('::')[-1])}</span>" for t in g["tests"]
            )
            cards.append(
                f'<div class="card" data-cat="{category}" style="--cc:{color}">'
                f'<h3><span class="pill cat" style="background:{color}">{esc(category)}</span>'
                f"<span>{esc(v.root_cause)}</span>{nh}</h3>"
                f'<div class="conf">уверенность {v.confidence:.2f}'
                f'<span class="bar"><i style="width:{int(v.confidence * 100)}%;'
                f'background:{cbar}"></i></span>· тестов: {len(g["tests"])}</div>'
                f'<div class="tests">{tests}</div>'
                f'<div class="ev"><b>Улики:</b> {esc(v.evidence)}</div>'
                f'<div class="act"><b>Действие:</b> {esc(v.suggested_action)}</div>'
                f"</div>"
            )

    page = (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Триаж прогона</title><style>{_HTML_STYLE}</style></head><body>"
        f'<header><h1>🩺 Триаж прогона</h1><div class="sub">Красных тестов: '
        f"{total_failed} · причин после группировки: {len(groups)} · "
        f"клик по плитке — фильтр по категории</div></header>"
        f'<main><div class="tiles">{tiles}</div>{"".join(cards)}</main>'
        f"{_HTML_SCRIPT}</body></html>"
    )
    out.write_text(page, encoding="utf-8")


def build_issues(groups: dict) -> list[dict]:
    """Черновики багов: только уверенные (≥0.6) и не needs_human."""
    issues = []
    for g in groups.values():
        v = g["verdict"]
        if v.needs_human or v.confidence < 0.6:
            continue  # спорное — человеку, не в трекер
        issues.append(
            {
                "title": v.draft_bug_title
                or f"[{v.category.value}] {v.root_cause[:100]}",
                "category": v.category.value,
                "body": (
                    f"Затронутые тесты: {', '.join(g['tests'])}\n\n"
                    f"Первопричина: {v.root_cause}\n\nУлики: {v.evidence}\n\n"
                    f"Рекомендация: {v.suggested_action}"
                ),
                "labels": ["ai-triage", v.category.value],
                "status": "draft",
            }
        )
    return issues


def file_issues(issues: list[dict]) -> None:
    """Отправить черновики в POST /issues (human-in-the-loop, по флагу).

    Каждый баг независим: сетевая ошибка на одном не блокирует остальные.
    """
    for issue in issues:
        try:
            resp = requests.post(ISSUES_URL, json=issue, timeout=15)
        except requests.RequestException as err:
            print(f"  !! {issue['title'][:60]}: сеть недоступна ({err})")
            continue
        mark = "→" if resp.status_code < 400 else "!!"
        print(f"  {mark} {ISSUES_URL}: {resp.status_code} {issue['title'][:60]}")


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def load_failures(args: argparse.Namespace) -> list[Failure]:
    """Выбрать вход: Allure или JUnit.

    Режим Allure включается флагом ``--from-allure`` ИЛИ автоматически, если
    переданный путь — каталог (allure-results). Иначе — JUnit-xml (дефолт).
    """
    report = args.report
    use_allure = args.from_allure or (report and Path(report).is_dir())

    if use_allure:
        results_dir = Path(report) if report else ROOT / "reports" / "allure-results"
        if not results_dir.exists():
            sys.exit(
                f"[!] allure-results не найдены: {results_dir} "
                f"(сначала: pytest --alluredir={results_dir})"
            )
        print(f"[i] Вход: {paint('Allure', 'bold')} — {results_dir}")
        return parse_allure(results_dir)

    report_path = Path(report or "reports/report.xml")
    if not report_path.exists():
        report_path = ROOT / "reports" / "sample_report.xml"
        print(
            f"[i] {report or 'reports/report.xml'} не найден, беру эталонный {report_path.name}"
        )
    print(f"[i] Вход: {paint('JUnit', 'bold')} — {report_path}")
    return parse_junit(report_path)


def dump_context(failures: list[Failure]) -> None:
    """Напечатать пакеты контекста, отправляемые модели, без вызова модели."""
    for f in failures:
        print(paint(f"\n{'=' * 70}", "dim"))
        print(paint(f"КОНТЕКСТ → {f.test}", "bold"))
        print(paint("=" * 70, "dim"))
        print(build_context(f))


def print_economics(results: list[TriageResult]) -> None:
    """Свести токены и причины остановки по всем вызовам модели."""
    usages: list[Usage] = [u for r in results for u in r.usages]
    prompt_t = sum(u.prompt_tokens for u in usages)
    completion_t = sum(u.completion_tokens for u in usages)
    truncated = sum(1 for u in usages if u.finish_reason == "length")
    escalated = sum(1 for r in results if r.escalated)
    print(
        paint(
            f"[i] Экономика: {len(usages)} вызовов, токенов {prompt_t + completion_t} "
            f"(prompt {prompt_t} / completion {completion_t}), "
            f"обрезок по длине: {truncated}, эскалаций: {escalated}",
            "dim",
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="ИИ-триаж падений автотестов")
    parser.add_argument(
        "report",
        nargs="?",
        default=None,
        help="путь к junit.xml (дефолт reports/report.xml) ИЛИ к каталогу allure-results "
        "(каталог определяется автоматически)",
    )
    parser.add_argument(
        "--from-allure",
        action="store_true",
        help="принудительно читать allure-results (по умолчанию reports/allure-results)",
    )
    parser.add_argument(
        "--dump-context", action="store_true", help="показать пакеты контекста и выйти"
    )
    parser.add_argument(
        "--no-enrich",
        action="store_true",
        help="без обогащения кодом/контрактом",
    )
    parser.add_argument(
        "--escalate",
        action="store_true",
        help="спорные переспросить умной моделью (LLM_MODEL_HARD)",
    )
    parser.add_argument(
        "--file-issues",
        action="store_true",
        help="завести черновики багов через POST /issues",
    )
    args = parser.parse_args()

    failures = load_failures(args)
    print(f"[1/4] Падений в отчёте: {paint(str(len(failures)), 'bold')}")

    if not failures:
        print(paint("[i] Прогон зелёный — падений нет, триажить нечего.", "dim"))
        return

    for f in failures:
        enrich(f, do_enrich=not args.no_enrich)

    if args.dump_context:
        dump_context(failures)
        print(paint("\n[i] --dump-context: модель не вызывалась.", "dim"))
        return

    try:
        client = DeepSeekClient.from_env(PROMPT)
    except LLMError as err:
        sys.exit(f"[!] {err}")

    mode = " (+enrich)" if not args.no_enrich else " (без enrich)"
    print(
        f"[2/4] Триаж через {client.model}{' → ' + client.model_hard if args.escalate else ''}{mode}"
    )
    print(
        paint(
            f"    (каждое падение — отдельный вызов модели, ~20–40 сек; всего {len(failures)})",
            "dim",
        )
    )
    verdicts: list[Verdict] = []
    results: list[TriageResult] = []
    for f in failures:
        try:
            result = client.triage(build_context(f), escalate=args.escalate)
        except LLMError as err:
            sys.exit(f"[!] Модель недоступна на «{f.test}»: {err}")
        v = result.verdict
        verdicts.append(v)
        results.append(result)
        mark = paint("⚠", "продукт") if v.needs_human else " "
        esc = paint(" ↑pro", "dim") if result.escalated else ""
        print(
            f"  {mark} {paint(f'{v.category.value:14s}', v.category.value)} "
            f"{v.confidence:.2f}  {f.test}{esc}"
        )

    groups = group(failures, verdicts)
    print(
        f"[3/4] Причин после группировки: {paint(str(len(groups)), 'bold')} "
        f"(из {len(failures)} падений)"
    )

    out_dir = ROOT / "output"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "verdicts.json").write_text(
        json.dumps([v.model_dump() for v in verdicts], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_triage_md(groups, len(failures), out_dir / "triage.md")
    write_triage_html(groups, len(failures), out_dir / "triage.html")
    issues = build_issues(groups)
    (out_dir / "issues.json").write_text(
        json.dumps(issues, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"[4/4] output/: triage.md, triage.html, verdicts.json, issues.json "
        f"({len(issues)} черновиков)"
    )
    print_economics(results)

    if args.file_issues:
        print("[i] Завожу черновики в трекер:")
        file_issues(issues)
    else:
        print(
            paint(
                "[i] Заведение багов выключено (human-in-the-loop): "
                "посмотрите output/triage.md, затем --file-issues",
                "dim",
            )
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\n[i] Прервано пользователем (Ctrl+C).")
