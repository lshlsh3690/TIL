"""
TIL 레포 폴더 구조를 스캔해서 README.md에 카테고리별 목차를 자동 생성하는 스크립트.
GitHub Actions에서 push마다 실행됨.
"""
import os
import subprocess
from urllib.parse import quote

# 스캔에서 제외할 폴더/파일
EXCLUDE_DIRS = {".git", ".github", "__pycache__", "node_modules"}
EXCLUDE_FILES = {"README.md"}

REPO_ROOT = "."
README_PATH = os.path.join(REPO_ROOT, "README.md")

# README 상단에 고정으로 넣을 소개 문구 (원하는 대로 수정하세요)
HEADER = """# 📚 TIL (Today I Learned)

책과 인강을 보며 배운 내용을 카테고리별로 정리하는 저장소입니다.
아래 목차는 커밋할 때마다 자동으로 갱신됩니다.

"""


def get_title(filepath):
    """마크다운 파일의 첫 번째 # 제목을 추출, 없으면 파일명 사용"""
    try:
        with open(filepath, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("# "):
                    return line[2:].strip()
    except Exception:
        pass
    return os.path.splitext(os.path.basename(filepath))[0]


def get_last_modified_date(filepath):
    """git 커밋 히스토리에서 파일의 최종 수정일을 조회, 히스토리가 없으면 None"""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%cd", "--date=format:%Y-%m-%d", "--", filepath],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def scan_categories(root):
    """1depth 폴더를 카테고리로 취급하고, 각 폴더 안의 .md 파일을 수집"""
    categories = {}

    for entry in sorted(os.listdir(root)):
        full_path = os.path.join(root, entry)

        if entry in EXCLUDE_DIRS or entry.startswith("."):
            continue

        if os.path.isdir(full_path):
            md_files = []
            for dirpath, _, filenames in os.walk(full_path):
                for fname in sorted(filenames):
                    if fname.endswith(".md") and fname not in EXCLUDE_FILES:
                        rel_path = os.path.relpath(
                            os.path.join(dirpath, fname), root
                        )
                        full_file_path = os.path.join(dirpath, fname)
                        title = get_title(full_file_path)
                        date = get_last_modified_date(full_file_path)
                        md_files.append((title, rel_path, date))
            if md_files:
                categories[entry] = md_files

    return categories


def build_readme(categories):
    lines = [HEADER]

    total_count = sum(len(files) for files in categories.values())
    lines.append(f"> 총 **{total_count}개** 문서 · **{len(categories)}개** 카테고리\n")

    for category in sorted(categories.keys()):
        files = categories[category]
        lines.append(f"\n## {category} ({len(files)})\n")
        for title, rel_path, date in files:
            # 윈도우 경로 구분자 대응 + 공백 등 특수문자 URL 인코딩 (마크다운 링크 깨짐 방지)
            url_path = quote(rel_path.replace(os.sep, "/"), safe="/")
            date_str = f" — 최종 수정: {date}" if date else ""
            lines.append(f"- [{title}]({url_path}){date_str}")

    lines.append("\n---\n*이 README는 GitHub Actions로 자동 생성됩니다.*")
    return "\n".join(lines)


def main():
    categories = scan_categories(REPO_ROOT)
    content = build_readme(categories)

    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"README.md 생성 완료: {sum(len(v) for v in categories.values())}개 문서, {len(categories)}개 카테고리")


if __name__ == "__main__":
    main()