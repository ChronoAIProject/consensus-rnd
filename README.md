# consensus-rnd

通用**共识式研发** skills 库 —— 可被任意 host 仓库注入的多角度共识构建引擎。

## 定位

这里的"研发"取最广义:**任何持续向仓库提交状态的活动都是研发** —— 写代码是研发,写文档是研发,做 marketing、整理资料、维护配置同样是研发。只要产物落进 git、需要被审查、需要质量保证,就适用同一套引擎。

本库不绑定任何具体项目。host 通过 `host.env` 注入自己的事实(仓库根、集成分支、规则文档、构建/测试命令、GitHub slug 等),引擎本身不写死任一项目。

## 核心:共识构建引擎

不是"多跑几遍取多数",而是**偏置独立的多角度逼近**:

- **多 solver,先验对立**:每个 solver 带不同立场(如 minimal 最小改动 / structural 架构洁净 / delete 质疑必要性),且**互相看不到对方输出**,各自独立得出结论。
- **meta-judge 仲裁**:把分歧收敛成 `consensus` / `converge` / `stalled` 三个出口,不达成一致就继续收敛,直到真停滞才升 reflector 调和。
- **任何 concrete plan 都必须过这道闸**:哪怕方向已明确,也不允许单个 agent 直接落地 —— 用多角度验证把"明显方向"证成"明显方案"。
- **验证侧同构**:产物再经多 reviewer(架构 / 质量 / 测试)共识 gate 才允许合并。
- **controller 纯编排**:所有思考、分析、诊断、验证都 delegate 给 agent;controller 只读 marker、走路由、管 git 与状态面。

## skills

| skill | 说明 | 状态 |
|---|---|---|
| `codex-refactor-loop` | 无人值守三阶段循环(audit → implement → verify),codex CLI 驱动,GitHub 为状态面 | 自 host 项目移植,verbatim;待泛化(见下) |

## 泛化路线(待迭代)

第一块是直接移植,仍带"重构"外壳与少量 host 主张。后续迭代方向:

1. **抽出引擎脊柱**(`solve → consensus → implement → verify`),让 audit 这一步(产生工作单元的种子)可替换 —— 换成任意 work-unit 来源(设计提案 / 文档任务 / 营销产出 / spec 变更),其余整套共识机制原样复用。
2. **参数化漏入的 host 主张**:如"工作语言规则"应成为可注入的 host policy,而非写死。
3. 重命名以脱离 "refactor" 语义。

> 泛化引擎本身宜走引擎自己那套共识 gate —— 用这个引擎来通用化这个引擎。
