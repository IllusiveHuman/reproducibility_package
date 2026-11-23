import argparse
import csv
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

RE_MOCKUS = re.compile(
    r"\b(fix|fixes|fixed|fixing|bug|bugs|defect|defects|error|errors|patch|patched)\b",
    re.I,
)
RE_ISSUE = re.compile(r"#\d+")
RE_EXCLUDE = re.compile(
    r"\b(refactor|refactoring|cleanup|style|format|bump|upgrade|dependency|dependencies|chore|docs|test|tests)\b",
    re.I,
)


def run_cmd(cmd, cwd):
    res = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    return res.stdout.strip()


def get_target_files(repo_dir, src_dir, lang):
    exts = (".java",) if lang == "java" else (".py",)
    base_path = os.path.join(repo_dir, src_dir)
    files = []
    for root, _, filenames in os.walk(base_path):
        norm_root = root.replace("\\", "/")
        if "/test/" in norm_root or "/tests/" in norm_root or "/__pycache__" in norm_root:
            continue
        for fname in filenames:
            if not fname.endswith(exts):
                continue
            if lang == "java" and (fname.startswith("Test") or fname.endswith("Test.java")):
                continue
            if lang == "python" and (
                fname.startswith("test_") or fname.endswith("_test.py") or fname == "__init__.py"
            ):
                continue
            rel_path = os.path.relpath(os.path.join(root, fname), repo_dir).replace("\\", "/")
            files.append(rel_path)
    return sorted(files)


def count_physical_lines(full_filepath):
    """Загальна кількість фізичних рядків файлу на зрізі T0 (знаменник для CD)."""
    with open(full_filepath, "r", encoding="utf-8", errors="ignore") as f:
        return len(f.readlines())


def count_loc(full_filepath, lang):
    """Кількість непорожніх некоментованих рядків коду (контрольна змінна C1: LOC)."""
    loc = 0
    in_block = False
    block_marker = None
    with open(full_filepath, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            if lang == "java":
                if s.startswith("/*"):
                    in_block = True
                if in_block:
                    if "*/" in s:
                        in_block = False
                    continue
                if s.startswith("//"):
                    continue
            elif lang == "python":
                if not in_block and (s.startswith('"""') or s.startswith("'''")):
                    marker = s[:3]
                    if s.count(marker) >= 2 and len(s) > 3:
                        continue
                    in_block = True
                    block_marker = marker
                    continue
                if in_block:
                    if block_marker in s:
                        in_block = False
                    continue
                if s.startswith("#"):
                    continue
            loc += 1
    return loc


def parse_cpd_xml_string(xml_text, repo_dir):
    """Об'єднання інтервалів клонів [line, endline] у множину унікальних фізичних рядків."""
    cloned_lines = {}
    xml_start = xml_text.find("<?xml")
    if xml_start == -1:
        return cloned_lines

    root = ET.fromstring(xml_text[xml_start:])
    abs_repo = os.path.realpath(repo_dir)

    for dup in root.iter():
        if not dup.tag.endswith("duplication"):
            continue
        lines_count = int(dup.attrib.get("lines", 0))
        for file_el in dup:
            if not file_el.tag.endswith("file"):
                continue
            raw_path = file_el.attrib.get("path", "")
            if not raw_path:
                continue

            if os.path.isabs(raw_path):
                full_file = os.path.realpath(raw_path)
            else:
                full_file = os.path.realpath(os.path.join(abs_repo, raw_path))

            rel_path = os.path.relpath(full_file, abs_repo).replace("\\", "/")

            start_line = int(file_el.attrib.get("line", 1))
            if "endline" in file_el.attrib:
                end_line = int(file_el.attrib["endline"])
            else:
                end_line = start_line + max(0, lines_count - 1)

            cloned_lines.setdefault(rel_path, set()).update(range(start_line, end_line + 1))

    return cloned_lines


def run_pmd_cpd(repo_dir, src_dir, lang):
    configs = {
        "t1": [
            "pmd",
            "cpd",
            "--minimum-tokens",
            "50",
            "--language",
            lang,
            "--dir",
            src_dir,
            "--format",
            "xml",
        ],
        "t2": [
            "pmd",
            "cpd",
            "--minimum-tokens",
            "50",
            "--language",
            lang,
            "--ignore-identifiers",
            "--ignore-literals",
            "--dir",
            src_dir,
            "--format",
            "xml",
        ],
        "t3": [
            "pmd",
            "cpd",
            "--minimum-tokens",
            "25",
            "--language",
            lang,
            "--ignore-identifiers",
            "--ignore-literals",
            "--dir",
            src_dir,
            "--format",
            "xml",
        ],
    }
    results = {}
    for key, cmd in configs.items():
        res = subprocess.run(
            cmd,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        if res.returncode not in (0, 4):
            print(f"[Попередження PMD CPD {key}]: код {res.returncode} — {res.stderr.strip()}")
        results[key] = parse_cpd_xml_string(res.stdout, repo_dir)
    return results["t1"], results["t2"], results["t3"]


def merge_type3_blocks(lines_t3_raw, max_dissimilarity=0.30):
    if not lines_t3_raw:
        return set()
    sorted_lines = sorted(lines_t3_raw)
    merged = set(sorted_lines)
    for i in range(len(sorted_lines) - 1):
        gap = sorted_lines[i + 1] - sorted_lines[i] - 1
        if 0 < gap <= 5:
            dissimilarity = gap / (20 + gap)
            if dissimilarity <= max_dissimilarity:
                merged.update(range(sorted_lines[i], sorted_lines[i + 1] + 1))
    return merged


def get_git_metrics(repo_dir, rel_filepath, tag_t0, tag_t1, t0_ts):
    numstat = run_cmd(
        ["git", "log", tag_t0, "--no-merges", "--numstat", "--pretty=format:", "--", rel_filepath],
        cwd=repo_dir,
    )
    churn = 0
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            churn += int(parts[0]) + int(parts[1])

    authors = run_cmd(
        ["git", "log", tag_t0, "--no-merges", "--format=%ae", "--", rel_filepath],
        cwd=repo_dir,
    ).splitlines()
    dev_count = max(1, len(set(a.strip() for a in authors if a.strip())))

    created_ts_str = run_cmd(
        ["git", "log", tag_t0, "--diff-filter=A", "--follow", "--format=%ct", "--", rel_filepath],
        cwd=repo_dir,
    ).splitlines()
    if created_ts_str and created_ts_str[-1].isdigit():
        age_days = max(0, (t0_ts - int(created_ts_str[-1])) // 86400)
    else:
        age_days = 0

    commits = run_cmd(
        ["git", "log", f"{tag_t0}..{tag_t1}", "--no-merges", "--format=%H|%s", "--", rel_filepath],
        cwd=repo_dir,
    ).splitlines()
    bug_fixes = 0
    for c in commits:
        if "|" not in c:
            continue
        _, msg = c.split("|", 1)
        if RE_MOCKUS.search(msg) and RE_ISSUE.search(msg) and not RE_EXCLUDE.search(msg):
            bug_fixes += 1

    return churn, dev_count, age_days, bug_fixes


def main():
    parser = argparse.ArgumentParser(description="Збір метрик клонів та Git-історії (T0 -> T1)")
    parser.add_argument("--project", required=True, help="Назва проєкту, напр. google/gson")
    parser.add_argument("--repo", required=True, help="Шлях до локального репозиторію")
    parser.add_argument("--tag0", required=True, help="Базовий релізний тег T0")
    parser.add_argument("--tag1", required=True, help="Кінцевий релізний тег T1")
    parser.add_argument("--src", required=True, help="Відносний шлях до директорії коду")
    parser.add_argument("--lang", required=True, choices=["java", "python"], help="Мова програмування")
    parser.add_argument("--out", default="full_dataset.csv", help="Вихідний CSV-файл")
    args = parser.parse_args()

    run_cmd(["git", "checkout", "-q", args.tag0], cwd=args.repo)
    t0_ts = int(run_cmd(["git", "log", "-1", "--format=%ct", args.tag0], cwd=args.repo))
    t1_ts = int(run_cmd(["git", "log", "-1", "--format=%ct", args.tag1], cwd=args.repo))
    window_days = (t1_ts - t0_ts) // 86400
    t0_date = datetime.fromtimestamp(t0_ts, tz=timezone.utc).strftime("%Y-%m-%d")
    t1_date = datetime.fromtimestamp(t1_ts, tz=timezone.utc).strftime("%Y-%m-%d")

    files = get_target_files(args.repo, args.src, args.lang)
    t1_map, t2_map, t3_map = run_pmd_cpd(args.repo, args.src, args.lang)

    rows = []
    for rel_path in files:
        full_path = os.path.join(args.repo, rel_path)
        loc = count_loc(full_path, args.lang)
        phys_lines = count_physical_lines(full_path)
        if loc == 0 or phys_lines == 0:
            continue

        raw_t1 = {ln for ln in t1_map.get(rel_path, set()) if 1 <= ln <= phys_lines}
        raw_t2 = {ln for ln in t2_map.get(rel_path, set()) if 1 <= ln <= phys_lines}
        raw_t3 = {ln for ln in t3_map.get(rel_path, set()) if 1 <= ln <= phys_lines}

        l_t1 = raw_t1
        l_t2 = raw_t2 - l_t1
        l_t3 = merge_type3_blocks(raw_t3) - (l_t1 | l_t2)
        l_total = l_t1 | l_t2 | l_t3

        cd_total = len(l_total) / phys_lines
        cd_t1 = len(l_t1) / phys_lines
        cd_t2 = len(l_t2) / phys_lines
        cd_t3 = len(l_t3) / phys_lines
        assert 0.0 <= cd_total <= 1.0, f"Некоректна щільність {cd_total} у {rel_path}"

        churn, devs, age, bugs = get_git_metrics(args.repo, rel_path, args.tag0, args.tag1, t0_ts)

        rows.append(
            {
                "project": args.project,
                "file_path": rel_path,
                "CD_total": round(cd_total, 4),
                "CD_Type1": round(cd_t1, 4),
                "CD_Type2": round(cd_t2, 4),
                "CD_Type3": round(cd_t3, 4),
                "LOC": loc,
                "PhysicalLOC": phys_lines,
                "CodeChurn": churn,
                "DevCount": devs,
                "ModuleAge": age,
                "BugFixFreq_T1": bugs,
            }
        )

    if not rows:
        print(f"Помилка: не знайдено файлів для {args.project} у директорії {args.src}")
        return

    file_exists = os.path.exists(args.out)
    with open(args.out, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)

    print(f"=== Звіт по {args.project} ({args.lang}) ===")
    print(f"Теги: {args.tag0} ({t0_date}) -> {args.tag1} ({t1_date}) | Вікно: {window_days} діб")
    print(
        f"Модулів: {len(rows)} | Загальний LOC: {sum(r['LOC'] for r in rows)} | "
        f"Фізичних рядків: {sum(r['PhysicalLOC'] for r in rows)}"
    )
    print(
        f"Модулів із клонами: {sum(r['CD_total'] > 0 for r in rows)} | "
        f"CD_total == 1.0: {sum(r['CD_total'] == 1.0 for r in rows)} | "
        f"Баг-фіксів: {sum(r['BugFixFreq_T1'] for r in rows)}\n"
    )


if __name__ == "__main__":
    main()