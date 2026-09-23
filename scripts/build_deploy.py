"""Build an isolated Railway Docker context; never deploy or read credentials.

Only the application, declared dependencies, one catalogue seed and static
assets are copied. Re-running replaces only the fixed deploy/railway output.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_ROOT = ROOT / "deploy"
OUTPUT = DEPLOY_ROOT / "railway"
MANIFEST = DEPLOY_ROOT / "railway-manifest.json"
APP_EXTENSIONS = {".py", ".html", ".css", ".js"}
FIXED_FILES = ("scripts/run_site.py", "requirements.txt", "Dockerfile", "data/raw_details.jsonl")
EXCLUDED_DIRS = {".git", ".venv", "venv", "node_modules", ".agents", ".codex", ".pytest_cache", "__pycache__", "deploy", "output"}
IMPORT_REQUIREMENTS = {
    "fastapi": "fastapi", "uvicorn": "uvicorn", "httpx": "httpx", "openai": "openai",
    "telegram": "python-telegram-bot", "dotenv": "python-dotenv", "openpyxl": "openpyxl",
    "pypdf": "pypdf", "docx": "python-docx", "reportlab": "reportlab",
    "pydantic": "fastapi",  # FastAPI declares this runtime dependency.
}


def require_inside(path: Path, parent: Path) -> Path:
    resolved, boundary = path.resolve(), parent.resolve()
    if resolved == boundary or not resolved.is_relative_to(boundary):
        raise ValueError("Путь сборки вышел за разрешённый каталог.")
    return resolved


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def sources() -> list[Path]:
    files = [path for path in (ROOT / "app").iterdir() if path.is_file() and path.suffix in APP_EXTENSIONS]
    files.extend(ROOT / name for name in FIXED_FILES)
    assets = ROOT / "дизайн" / "assets"
    if not assets.is_dir():
        raise ValueError("Отсутствует каталог дизайн/assets.")
    files.extend(path for path in assets.rglob("*") if path.is_file())
    if not files or not any(path.name == "main.py" for path in files):
        raise ValueError("Приложение для сборки не найдено.")
    for path in files:
        if not path.is_file():
            raise ValueError(f"Отсутствует обязательный файл: {path.relative_to(ROOT)}")
        require_inside(path, ROOT)
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != ROOT and parent.is_relative_to(ROOT)):
            raise ValueError(f"Символическая ссылка недопустима в сборке: {path.relative_to(ROOT)}")
    return sorted(set(files), key=lambda path: path.relative_to(ROOT).as_posix())


def excluded_counts() -> dict:
    """Count excluded categories without reading their contents."""
    counts = Counter()
    for current, directories, files in os.walk(ROOT):
        directories[:] = [name for name in directories if name not in EXCLUDED_DIRS]
        for name in files:
            lower = name.lower()
            if lower == ".env" or lower.startswith(".env."):
                counts["environment_files"] += 1
            elif any(part in lower for part in (".sqlite", ".db")):
                counts["database_and_state_files"] += 1
            elif lower.endswith((".log", ".pid")):
                counts["logs_and_pid_files"] += 1
            elif lower == "metrics.jsonl":
                counts["assistant_metrics_logs"] += 1
    return dict(counts)


def inspect_seed(path: Path) -> int:
    ids = set()
    required = {"id", "article", "name", "price", "quantity"}
    with path.open(encoding="utf-8-sig") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or not required.issubset(row):
                    raise ValueError
                product_id = int(row["id"])
                if product_id <= 0 or product_id in ids:
                    raise ValueError
                ids.add(product_id)
            except (ValueError, TypeError):
                raise ValueError(f"Неверный каталог seed, строка {number}.") from None
    if not ids:
        raise ValueError("Каталог seed пуст.")
    return len(ids)


def check_portability(bundle: Path, relative_files: set[str]) -> dict:
    requirements = (bundle / "requirements.txt").read_text(encoding="utf-8-sig")
    declared = {match.group(1).lower() for line in requirements.splitlines()
                if (match := re.match(r"\s*([A-Za-z0-9_.-]+)", line))}
    imported, py_count, absolute_paths = set(), 0, []
    secret_pattern = re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{24,}|\b\d{6,12}:[A-Za-z0-9_-]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
    for relative in sorted(relative_files):
        path = bundle / relative
        if path.suffix not in APP_EXTENSIONS:
            continue
        text = path.read_text(encoding="utf-8-sig")
        if secret_pattern.search(text):
            # Report only the filename; never echo matched content.
            raise ValueError(f"В файле сборки обнаружено значение, похожее на ключ: {relative}")
        if path.suffix == ".py":
            tree = ast.parse(text, filename=relative)
            compile(tree, relative, "exec")
            py_count += 1
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                    imported.add(node.module.split(".")[0])
                elif isinstance(node, ast.Constant) and isinstance(node.value, str) and re.match(r"^[A-Za-z]:[/\\]", node.value):
                    absolute_paths.append(relative)
        for static in re.findall(r"(?:src|href)=[\"']/(?:static|assets)/([^\"'?#]+)", text):
            prefix = "дизайн/assets/" if f"/assets/{static}" in text else "app/"
            if prefix + static not in relative_files:
                raise ValueError(f"Ресурс отсутствует в сборке: {prefix + static}")
    unknown = imported - sys.stdlib_module_names - {"app"} - set(IMPORT_REQUIREMENTS)
    missing = sorted({IMPORT_REQUIREMENTS[name] for name in imported if name in IMPORT_REQUIREMENTS} - declared)
    if unknown or missing:
        raise ValueError("Зависимости не покрыты requirements: " + ", ".join(sorted(unknown) + missing))
    dockerfile = (bundle / "Dockerfile").read_text(encoding="utf-8-sig")
    instructions = [line.strip() for line in dockerfile.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not instructions or not re.match(r"FROM\s+python:3\.13(?:[-\w.]*)", instructions[0], re.I):
        raise ValueError("Dockerfile должен использовать проверяемый Python 3.13 Linux image.")
    if not any(line.startswith("WORKDIR /app") for line in instructions):
        raise ValueError("Dockerfile не задаёт WORKDIR /app.")
    if "fonts-dejavu-core" not in dockerfile or "reportlab" not in declared:
        raise ValueError("Для русского PDF нужны reportlab и Linux-шрифт DejaVu.")
    for line in instructions:
        command, _, value = line.partition(" ")
        if command.upper() in {"COPY", "CMD", "ENTRYPOINT"} and value.startswith("["):
            if not isinstance(json.loads(value), list):
                raise ValueError("Неверная JSON-инструкция Dockerfile.")
        if command.upper() == "COPY":
            operands = json.loads(value) if value.startswith("[") else value.split()
            for source in operands[:-1]:
                if not (bundle / source).exists() or source == ".":
                    raise ValueError(f"Docker COPY должен ссылаться на конкретный файл/каталог сборки: {source}")
    runner = (bundle / "scripts/run_site.py").read_text(encoding="utf-8-sig")
    if "PORT" not in runner or "seed" not in runner or "catalog.sqlite" not in runner:
        raise ValueError("run_site не содержит PORT или инициализацию seed для пустого volume.")
    unexpected_absolute = set(absolute_paths) - {"app/cms_exports.py"}
    if unexpected_absolute:
        raise ValueError("Обязательный Windows-путь в коде: " + ", ".join(sorted(unexpected_absolute)))
    if "app/cms_exports.py" in absolute_paths:
        pdf_code = (bundle / "app/cms_exports.py").read_text(encoding="utf-8-sig")
        if "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf" not in pdf_code:
            raise ValueError("У PDF-шрифта отсутствует Linux fallback.")
    warnings = []
    health = next((line for line in instructions if line.upper().startswith("HEALTHCHECK ")), "")
    if not health:
        warnings.append("Dockerfile не содержит HEALTHCHECK.")
    elif "PORT" not in health:
        warnings.append("HEALTHCHECK использует фиксированный порт; Railway PORT должен совпадать.")
    return {"python_files_compiled": py_count, "imports_covered": True, "static_files_present": True,
            "linux_pdf_font_present": True, "windows_font_is_optional_fallback": bool(absolute_paths),
            "dockerfile_static_checks": "passed", "docker_build_performed": False,
            "warnings": warnings}


def audit_bundle(bundle: Path, expected_files: set[str]) -> dict:
    actual = {path.relative_to(bundle).as_posix() for path in bundle.rglob("*") if path.is_file()}
    if actual != expected_files:
        raise ValueError("Состав сборки отличается от allowlist.")
    forbidden = [name for name in actual if any(part.startswith(".env") for part in Path(name).parts)
                 or re.search(r"\.(?:sqlite(?:-wal|-shm)?|db|log|pid|pyc)$", name, re.I)
                 or name == "data/metrics.jsonl"]
    if forbidden:
        raise ValueError("В сборку попали запрещённые файлы: " + ", ".join(forbidden))
    seed_count = inspect_seed(bundle / "data/raw_details.jsonl")
    portability = check_portability(bundle, actual)
    entries = [{"path": relative, "bytes": (bundle / relative).stat().st_size, "sha256": digest(bundle / relative)}
               for relative in sorted(actual)]
    return {"created_at": datetime.now(timezone.utc).isoformat(), "files_count": len(entries),
            "total_bytes": sum(entry["bytes"] for entry in entries), "seed_products": seed_count,
            "forbidden_files": 0, "excluded_categories": excluded_counts(), "portability": portability, "files": entries}


def build(*, check_only=False):
    require_inside(DEPLOY_ROOT, ROOT)
    destination = require_inside(OUTPUT, DEPLOY_ROOT)
    if destination != ROOT.resolve() / "deploy" / "railway" or OUTPUT.is_symlink():
        raise ValueError("Разрешён только собственный каталог deploy/railway без ссылок.")
    files = sources()
    expected = {path.relative_to(ROOT).as_posix() for path in files}
    if check_only:
        if not OUTPUT.is_dir():
            raise ValueError("Сначала соберите deploy/railway без флага --check.")
        result = audit_bundle(OUTPUT, expected)
        if any(digest(ROOT / item["path"]) != item["sha256"] for item in result["files"]):
            raise ValueError("Исходники изменились после сборки. Повторите build_deploy.py.")
    else:
        DEPLOY_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="railway-stage-", dir=DEPLOY_ROOT) as staging:
            stage = require_inside(Path(staging), DEPLOY_ROOT)
            for source in files:
                target = stage / source.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            result = audit_bundle(stage, expected)
            if OUTPUT.exists():
                # This exact resolved path was checked above before recursive deletion.
                shutil.rmtree(destination)
            OUTPUT.mkdir()
            for source in stage.iterdir():
                # TemporaryDirectory has owner-only Windows ACLs. Copying into
                # the workspace output inherits its ACL; moving would preserve
                # private staging ACLs and block the elevated Railway uploader.
                target = OUTPUT / source.name
                if source.is_dir():
                    shutil.copytree(source, target)
                else:
                    shutil.copy2(source, target)
    MANIFEST.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    concise = {key: value for key, value in result.items() if key != "files"}
    concise.update(bundle="deploy/railway", manifest="deploy/railway-manifest.json")
    return concise


def main():
    parser = argparse.ArgumentParser(description="Собрать или проверить изолированный контекст Railway без публикации.")
    parser.add_argument("--check", action="store_true", help="Только проверить готовую сборку и совпадение с исходниками")
    args = parser.parse_args()
    try:
        result = build(check_only=args.check)
    except (OSError, ValueError, SyntaxError) as exc:
        print("Сборка не готова: " + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
