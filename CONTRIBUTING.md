# 贡献指南

从 feature branch 向 `dev` 提交 PR。仓库约定以 [CLAUDE.md](./CLAUDE.md) 为准；公开介绍见 [README.md](./README.md) 与[中文说明](./README.zh-CN.md)。

## 开发与验证

在仓库根目录运行下列命令，与 [CI](./.github/workflows/consensus-rnd-ci.yml) 保持一致。准备 Git、Python 3（CI 使用 Python 3.12）、Bash、`jq` 和 ShellCheck；在虚拟环境中安装锁定的 Ruff。测试使用临时目录和模拟 carrier，不要求实际调用模型服务。

```bash
python3 -m pip install ruff==0.14.0
python3 -m unittest discover -s skills/sshx/tests -p 'test_*.py'
python3 -m unittest discover -s .github/tests -p 'test_*.py'
python3 .github/scripts/bump_version.py --check
shellcheck --severity=warning --exclude=SC1007 skills/sshx/scripts/*.sh
python3 -m ruff check --select E9,F63,F7,F82 .github/scripts .github/tests skills/sshx/scripts skills/sshx/tests
```

ShellCheck 仅排除脚本中有意使用的 `a= b=` 赋值写法对应的 SC1007。`lint-advisory` 的安装失败或检查发现问题会使 job 失败；是否设为合并必需检查由 maintainer 管理。

Skill suite 中的 [Lean 构建测试](./skills/sshx/tests/test_sshx_formal_model.py)仅在 `lake` 已安装时执行，否则显式输出 skip。需要验证模型时，先准备 Lean/Lake；依赖、缓存及构建说明见 [formal/README.md](./skills/sshx/formal/README.md#build)。首次构建可能需要下载依赖与缓存。

[SKILL.md](./skills/sshx/SKILL.md) 由 [digest 检查](./skills/sshx/tests/test_sshx_contract.py)与 [Lean 逐条追踪](./skills/sshx/formal/README.md)锁定。合同变更需要同步测试与模型，并经过独立 review；不能仅更新 digest 就视为通过。

本地 Claude Code 开发可选手动创建符号链接 `.claude/skills -> ../skills`；[.gitignore](./.gitignore) 已忽略该路径，它不是发布入口。

## 版本与发布

版本和 release 由 maintainer 负责。维护版本时使用 [bump_version.py](./.github/scripts/bump_version.py)，以 [.version-bump.json](./.version-bump.json) 为唯一同步映射，不单独修改某个平台清单。

预发布由 maintainer 手工发布，tag 使用 `v<version>`，发布说明写在 GitHub Releases。清单版本可能先于最新 tag；判断已发布内容应查看对应 release，不把清单版本当作已经发布的证明。贡献 PR 不代替 maintainer 的合并、版本与发布决策。
