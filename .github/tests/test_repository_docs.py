"""仓库文档一致性；真实工作树检查与临时副本反例共用同一验证入口。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[2]
README_PAIR = ("README.md", "README.zh-CN.md")
SECTION_PAIRS = (
    ("What It Provides", "提供什么"),
    ("Core", "核心"),
    ("Protocol at a Glance", "协议速览"),
    ("Risks and Limits", "风险与限制"),
    ("Quick Start", "快速开始"),
    ("Architecture", "架构"),
    ("Contributing", "参与贡献"),
    ("License", "License"),
)
WORKFLOW = ".github/workflows/consensus-rnd-ci.yml"


@dataclass(frozen=True)
class Entry:
    path: str
    link_target: str | None = None


@dataclass(frozen=True)
class Repository:
    root: Path
    entries: tuple[Entry, ...]

    def read(self, path: str) -> str:
        return (self.root / path).read_text(encoding="utf-8")


class DocError(AssertionError):
    """带检查编号的可诊断文档错误。"""


def require(condition: bool, check: str, message: str) -> None:
    if not condition:
        raise DocError(f"{check}: {message}")


def read_repository(root: Path) -> Repository:
    indexed = subprocess.check_output(
        ["git", "ls-files", "--stage", "-z"], cwd=root, text=True
    )
    entries = []
    for record in indexed.split("\0"):
        if not record:
            continue
        metadata, path = record.split("\t", 1)
        mode, object_id, _stage = metadata.split()
        target = None
        if mode == "120000":
            target = subprocess.check_output(
                ["git", "cat-file", "blob", object_id], cwd=root, text=True
            )
        entries.append(Entry(path, target))
    return Repository(root, tuple(entries))


def outside_fences(text: str) -> str:
    lines = []
    fence = ""
    for line in text.splitlines():
        match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if match:
            marker = match.group(1)
            if not fence:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = ""
        elif not fence:
            lines.append(line)
    return "\n".join(lines)


def section(text: str, title: str) -> str:
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if match is None:
        raise DocError(f"missing section: {title}")
    return match.group(1)


def headings(text: str, level: int = 2) -> list[str]:
    return re.findall(rf"^{'#' * level} (.+)$", outside_fences(text), re.M)


def anchors(text: str) -> set[str]:
    prose = outside_fences(text)
    result = set(re.findall(r'<a\s+(?:id|name)=["\']([^"\']+)', prose))
    for title in re.findall(r"^#{1,6} (.+?)(?:\s+#+)?$", prose, re.M):
        title = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", title)
        slug = re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")
        unique = slug
        suffix = 0
        while unique in result:
            suffix += 1
            unique = f"{slug}-{suffix}"
        result.add(unique)
    return result


def link_destinations(text: str) -> list[str]:
    prose = outside_fences(text)
    links = re.findall(r"(?<!!)\[[^\n]*?\]\(<?([^\s)>]+)>?(?:\s+\"[^\"]*\")?\)", prose)
    definitions = dict(re.findall(r"^\[([^]]+)\]:\s*<?([^\s>]+)>?", prose, re.M))
    for label, reference in re.findall(r"\[([^]\n]+)\]\[([^]\n]*)\]", prose):
        key = reference or label
        require(key in definitions, "a", f"undefined link reference: {key}")
        links.append(definitions[key])
    return links


def check_links(repo: Repository) -> None:
    for entry in repo.entries:
        if "/" in entry.path or not entry.path.endswith(".md"):
            continue
        for destination in link_destinations(repo.read(entry.path)):
            url = urlsplit(destination)
            if url.scheme or url.netloc:
                continue
            target = repo.root / (unquote(url.path) or entry.path)
            require(target.exists(), "a", f"{entry.path}: missing link target {destination}")
            if url.fragment:
                require(target.is_file(), "a", f"fragment on directory: {destination}")
                require(
                    unquote(url.fragment) in anchors(target.read_text(encoding="utf-8")),
                    "a", f"{entry.path}: missing fragment {destination}",
                )


def check_readme_pair(repo: Repository) -> None:
    for index, filename in enumerate(README_PAIR):
        text = repo.read(filename)
        other = README_PAIR[1 - index]
        linked_paths = {urlsplit(link).path.removeprefix("./") for link in link_destinations(text)}
        require(other in linked_paths, "b", f"{filename}: missing reciprocal link to {other}")
        actual = headings(text)
        expected = [pair[index] for pair in SECTION_PAIRS]
        require(actual == expected, "b", f"{filename}: major section order {actual!r} != {expected!r}")
        risk, quick = SECTION_PAIRS[3][index], SECTION_PAIRS[4][index]
        require(actual.index(risk) < actual.index(quick), "b", f"{filename}: risks must precede quick start")
        require(bool(section(text, risk).strip()), "b", f"{filename}: empty risk section")


def table_identifiers(text: str, first_heading: str, check: str) -> list[str]:
    lines = outside_fences(text).splitlines()
    starts = [index for index, line in enumerate(lines) if line.startswith(f"| {first_heading} |")]
    require(len(starts) == 1, check, f"expected one {first_heading!r} table")
    values = []
    for line in lines[starts[0] + 2:]:
        if not line.startswith("|"):
            break
        cell = line.split("|", 2)[1].strip()
        match = re.fullmatch(r"`([^`]+)`", cell)
        require(match is not None, check, f"invalid identifier cell: {cell}")
        values.append(match.group(1))
    require(bool(values) and len(values) == len(set(values)), check, f"empty or duplicate {first_heading} rows")
    return values


def check_skill_discovery(repo: Repository) -> None:
    skills = {path.parent.name for path in (repo.root / "skills").glob("*/SKILL.md")}
    require(bool(skills), "c", "no published skills")
    for filename in README_PAIR:
        require(set(table_identifiers(repo.read(filename), "skill", "c")) == skills,
                "c", f"{filename}: skill table differs from skills tree")
    gemini = set(re.findall(r"^- `([^`]+)`\s+—", repo.read("GEMINI.md"), re.M))
    require(gemini == skills, "c", "GEMINI.md skill list differs from skills tree")
    manifest_paths = ("package.json", "gemini-extension.json", ".claude-plugin/plugin.json",
                      ".claude-plugin/marketplace.json", ".codex-plugin/plugin.json", ".cursor-plugin/plugin.json")
    expected = (repo.root / "skills").resolve()
    for filename in manifest_paths:
        manifest = json.loads(repo.read(filename))
        if "skills" in manifest:
            paths = manifest["skills"]
            paths = [paths] if isinstance(paths, str) else paths
            require(isinstance(paths, list) and bool(paths), "c", f"{filename}: invalid skills declaration")
            for path in paths:
                require(isinstance(path, str) and (repo.root / path).resolve() == expected,
                        "c", f"{filename}: skills path does not resolve to repository skills tree")
        elif filename in (".codex-plugin/plugin.json", ".cursor-plugin/plugin.json"):
            raise DocError(f"c: {filename}: missing skills declaration")
        if filename == ".claude-plugin/marketplace.json":
            require(bool(manifest["plugins"]), "c", "empty marketplace")
            for plugin in manifest["plugins"]:
                source = repo.root / plugin["source"]
                require((source / "skills").resolve() == expected and source.is_dir(),
                        "c", "marketplace source does not expose repository skills tree")
        if filename == "gemini-extension.json":
            require(manifest["contextFileName"] == "GEMINI.md", "c", "Gemini context entry differs")
    link = repo.root / ".codex-plugin/skills"
    tracked_links = {entry.path for entry in repo.entries if entry.link_target is not None}
    require(".codex-plugin/skills" in tracked_links and link.is_symlink() and link.resolve() == expected,
            "c", "tracked Codex skills symlink does not resolve to repository skills tree")


def tree_entries(text: str) -> tuple[Entry, ...]:
    tree = re.search(r"```text\n\.\n(.*?)\n```", section(text, "目录约定"), re.S)
    require(tree is not None, "d", "missing directory tree")
    parents: list[str] = []
    entries = []
    for line in tree.group(1).splitlines():
        match = re.fullmatch(r"((?:│   |    )*)[├└]── (.+)", line)
        require(match is not None, "d", f"invalid tree row: {line}")
        depth = len(match.group(1)) // 4
        require(depth <= len(parents), "d", f"tree depth jumps: {line}")
        name, _, target = match.group(2).split("#", 1)[0].strip().partition(" -> ")
        name = name.rstrip("/")
        parents = parents[:depth]
        path = "/".join([*parents, name])
        entries.append(Entry(path, target or None))
        parents.append(name)
    return tuple(entries)


def immediate_children(entries: tuple[Entry, ...], parent: str = "") -> set[str]:
    prefix = parent + "/" if parent else ""
    return {entry.path[len(prefix):].split("/", 1)[0] for entry in entries if entry.path.startswith(prefix)}


def check_directory_tree(repo: Repository) -> None:
    documented = tree_entries(repo.read("CLAUDE.md"))
    require(len({entry.path for entry in documented}) == len(documented), "d", "duplicate tree path")
    for parent in ("", ".github", "skills/sshx"):
        actual = immediate_children(repo.entries, parent)
        listed = immediate_children(documented, parent)
        require(actual == listed, "d", f"{parent or '.'}: tree entries {sorted(listed)} != inventory {sorted(actual)}")
    for entry in documented:
        path = repo.root / entry.path
        require(path.exists(), "d", f"documented path does not exist: {entry.path}")
        require(path.is_symlink() == (entry.link_target is not None), "d", f"symlink annotation missing or false: {entry.path}")
        if entry.link_target is not None:
            require(str(path.readlink()) == entry.link_target, "d", f"symlink target differs: {entry.path}")
    actual_links = {entry.path: entry.link_target for entry in repo.entries if entry.link_target is not None}
    listed_links = {entry.path: entry.link_target for entry in documented if entry.link_target is not None}
    require(listed_links == actual_links, "d", "documented symlinks differ from Git inventory")


def verification_commands(text: str) -> tuple[str, ...]:
    return tuple(line.strip() for block in re.findall(r"```bash\n(.*?)\n```", text, re.S)
                 for line in block.splitlines() if line.strip() and not line.lstrip().startswith("#"))


def workflow_commands(text: str) -> set[str]:
    commands = set()
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)run:\s*(.*)$", line)
        if match is None:
            continue
        value = match.group(2)
        if value not in ("|", ">", "|-", ">-"):
            commands.add(value)
            continue
        indent = len(match.group(1))
        for body in lines[index + 1:]:
            if body.strip() and len(body) - len(body.lstrip()) <= indent:
                break
            if body.strip():
                commands.add(body.strip())
    return commands


def check_verification_commands(repo: Repository) -> None:
    commands = verification_commands(repo.read("CONTRIBUTING.md"))
    require(bool(commands), "e", "no contributor verification commands")
    available = workflow_commands(repo.read(WORKFLOW))
    for command in commands:
        require(command in available, "e", f"documented command is not a CI run command: {command}")


def check_protocol_identifiers(repo: Repository) -> None:
    skill = repo.read("skills/sshx/SKILL.md")
    stages = re.findall(r"^\d+\. `([^`]+)`", section(skill, "InlineConsensusProtocol"), re.M)
    carriers = re.findall(r"^\d+\. `([^`]+)`", section(skill, "Worker Delegation"), re.M)
    seats = re.findall(r"^- `([^`]+)`(?: \([^\n]+?\))?:", section(skill, "Thinking Panel"), re.M)
    reviewers = re.findall(r"^- `([^`]+)`:", section(skill, "Review Triplet"), re.M)
    groups = ((stages, ("Stage", "阶段")), (seats, ("Thinking seat", "思考席位")),
              (reviewers, ("Review seat", "评审席位")), (carriers, ("Carrier", "Carrier")))
    for expected, labels in groups:
        require(bool(expected), "f", f"could not derive {labels[0]} from SKILL.md")
        for filename, label in zip(README_PAIR, labels):
            actual = table_identifiers(repo.read(filename), label, "f")
            require(set(actual) == set(expected), "f", f"{filename}: {label} identifiers {actual!r} != contract {expected!r}")
            if label in ("Stage", "阶段"):
                require(actual == expected, "f", f"{filename}: stage order differs from contract")


@dataclass(frozen=True)
class Mutation:
    check: str
    path: str
    before: str
    after: str
    label: str


class RepositoryDocsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo = read_repository(ROOT)

    def test_a_relative_links_and_fragments(self) -> None:
        check_links(self.repo)

    def test_b_readme_pair(self) -> None:
        check_readme_pair(self.repo)

    def test_c_skill_and_manifest_discovery(self) -> None:
        check_skill_discovery(self.repo)

    def test_d_documented_tree(self) -> None:
        check_directory_tree(self.repo)

    def test_e_verification_commands_match_ci(self) -> None:
        check_verification_commands(self.repo)

    def test_f_protocol_identifiers(self) -> None:
        check_protocol_identifiers(self.repo)

    def copy_fixture(self, root: Path) -> Repository:
        for entry in self.repo.entries:
            source = self.repo.root / entry.path
            target = root / entry.path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target, follow_symlinks=False)
        return Repository(root, self.repo.entries)

    def test_untracked_files_do_not_affect_inventory_or_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.copy_fixture(root / "source")
            git_dir = subprocess.check_output(
                ["git", "rev-parse", "--absolute-git-dir"], cwd=self.repo.root, text=True
            ).strip()
            # 临时副本只读复用原 Git index，验证真实 Git inventory 读取。
            (repo.root / ".git").write_text(f"gitdir: {git_dir}\n", encoding="utf-8")
            untracked = ("review-notes.md", "local-venv/pyvenv.cfg")
            for name in untracked:
                path = repo.root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("[local note](missing-local-target)\n", encoding="utf-8")
            self.repo = read_repository(repo.root)
            self.assertEqual(self.repo.entries, repo.entries)
            check_directory_tree(self.repo)
            copied = self.copy_fixture(root / "copied")
            for name in untracked:
                self.assertFalse((copied.root / name).exists())
            check_directory_tree(copied)

    def assert_detected(self, check: str, repo: Repository, label: str) -> None:
        # 检查集合固定；反例走真实检查入口，不复制判断逻辑。
        checks = {"a": check_links, "b": check_readme_pair, "c": check_skill_discovery,
                  "d": check_directory_tree, "e": check_verification_commands, "f": check_protocol_identifiers}
        with self.assertRaisesRegex(DocError, rf"^{check}:") as caught:
            checks[check](repo)
        print(f"SEED {check} [{label}] -> {caught.exception}")

    def test_seeded_text_violations(self) -> None:
        mutations = (
            Mutation("a", "README.md", "./LICENSE)", "./missing-license)", "missing relative target"),
            Mutation("a", "README.zh-CN.md", "#trigger)", "#missing-fragment)", "missing used fragment"),
            Mutation("b", "README.md", "./README.zh-CN.md", "./CLAUDE.md", "missing reciprocal link"),
            Mutation("b", "README.zh-CN.md", "## 风险与限制", "## 未知章节", "missing risk section"),
            Mutation("b", "README.md", "## Risks and Limits", "## Quick Start", "misordered major sections"),
            Mutation("c", "README.md", "| `sshx` |", "| `ghost` |", "README skill differs"),
            Mutation("c", "GEMINI.md", "- `sshx`", "- `ghost`", "Gemini skill differs"),
            Mutation("c", ".codex-plugin/plugin.json", '"./skills/"', '"./missing/"', "Codex skills path differs"),
            Mutation("c", ".cursor-plugin/plugin.json", '"./skills/"', '"./missing/"', "Cursor skills path differs"),
            Mutation("c", ".claude-plugin/marketplace.json", '"source": "./"', '"source": "./missing/"', "marketplace source differs"),
            Mutation("c", "gemini-extension.json", '"GEMINI.md"', '"README.md"', "Gemini context differs"),
            Mutation("d", "CLAUDE.md", "├── LICENSE\n", "", "missing tracked top-level entry"),
            Mutation("d", "CLAUDE.md", "├── LICENSE\n", "├── LICENSE\n├── missing-file\n", "extra nonexistent tree entry"),
            Mutation("d", "CLAUDE.md", "AGENTS.md -> CLAUDE.md", "AGENTS.md -> README.md", "documented symlink target differs"),
            Mutation("d", "CLAUDE.md", "        ├── formal/", "        ├── missing/", "skill child differs"),
            Mutation("d", "CLAUDE.md", "│   └── workflows/", "│   └── missing/", "GitHub child differs"),
            Mutation("e", "CONTRIBUTING.md", "--check\n", "--unknown\n", "documented command differs from CI"),
            Mutation("f", "README.md", "| `intake` |", "| `unknown_stage` |", "stage differs"),
            Mutation("f", "README.zh-CN.md", "| `worth` |", "| `unknown_seat` |", "thinking seat differs"),
            Mutation("f", "README.md", "| `tests` |", "| `unknown_review` |", "review seat differs"),
            Mutation("f", "README.zh-CN.md", "| `nyxid-oracle` |", "| `unknown_carrier` |", "carrier differs"),
            Mutation("f", "skills/sshx/SKILL.md", "1. `intake`", "1. `new_stage`", "owner contract changes independently"),
        )
        for mutation in mutations:
            with self.subTest(check=mutation.check, label=mutation.label), tempfile.TemporaryDirectory() as directory:
                repo = self.copy_fixture(Path(directory))
                text = repo.read(mutation.path)
                self.assertIn(mutation.before, text, "seed no longer applies")
                (repo.root / mutation.path).write_text(text.replace(mutation.before, mutation.after), encoding="utf-8")
                self.assert_detected(mutation.check, repo, mutation.label)

    def test_seeded_filesystem_violations(self) -> None:
        for check in ("c", "d"):
            with self.subTest(check=check), tempfile.TemporaryDirectory() as directory:
                repo = self.copy_fixture(Path(directory))
                link = repo.root / ".codex-plugin/skills"
                link.unlink()
                link.symlink_to("../.github")
                self.assert_detected(check, repo, "actual Codex symlink target differs")
        with tempfile.TemporaryDirectory() as directory:
            repo = self.copy_fixture(Path(directory))
            (repo.root / "skills/sshx/SKILL.md").unlink()
            self.assert_detected("c", repo, "published skill removed")
        with tempfile.TemporaryDirectory() as directory:
            repo = self.copy_fixture(Path(directory))
            (repo.root / "LICENSE").unlink()
            self.assert_detected("d", repo, "documented tracked file absent")


if __name__ == "__main__":
    unittest.main()
