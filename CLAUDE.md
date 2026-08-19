# consensus-rnd - Agent 工作指南

本文件给在本仓库内维护 skill、清单和文档的 agent 使用，不是下游 host 的运行时配置。仓库定位与共识引擎设计哲学见英文 canonical [`README.md`](./README.md)，中文 companion 见 [`README.zh-CN.md`](./README.zh-CN.md)。

## 旁路推理通道: nyxid oracle (ChatGPT Pro)

`nyxid oracle` 可作为 codex / Claude 之外的独立推理通道。命令、参数和输出字段以 `nyxid oracle --help` 为准；长 prompt 可用 `--file -`，多轮可用 `--new-conversation` / `--conversation`。异步请求只在完成后读取一次结果，不 busy-loop 轮询。oracle 能自行抓取公开可访问的 GitHub 链接，所以大段仓库内容可改为附 pin 到 commit SHA 的 permalink，而不是塞进 prompt；未公开的内容不得外链，也不得为了外链而推送或改仓库可见性。

## 仓库性质

这是一个跨平台 Agent Skills 发布仓库，不是应用代码仓库。唯一产物是 `skills/<name>/` 下的 `SKILL.md` 及其配套文件，同一份 `skills/` 由 Claude Code / Codex / Cursor / Gemini 共享。

## 目录约定

```text
.
├── .claude-plugin/        # Claude Code: plugin.json + marketplace.json
├── .codex-plugin/         # Codex: plugin.json
├── .cursor-plugin/        # Cursor: plugin.json
├── gemini-extension.json + GEMINI.md
├── package.json           # npm 风格元数据 / 版本锚点
├── AGENTS.md -> CLAUDE.md # 跨 agent 约定(符号链接)
├── README.md + README.zh-CN.md
├── LICENSE
├── skills/sshx/           # 发布的 worker-delegated inline consensus skill
└── .version-bump.json     # 版本号同步映射
```

## 设计哲学(跨 skill 不动点)

- 单一主干、插件扩展：每个 skill 只有一条权威主链路，新能力挂载在明确的子模块或脚本上。
- 内核最小化：SKILL.md 承载触发条件、稳定不变量、行为合同和事实源索引；机械部分下沉到 `scripts/`，prompt 模板下沉到 `prompts/`。
- 边界清晰、职责分层：本文件承载仓库级边界；skill 的工作流细则和术语归其自身事实源维护。
- 事实源唯一：版本号以 `.version-bump.json` 为准，skill 行为以对应 `SKILL.md` 和测试为准，host 事实由 host 配置注入。
- 抽象优先、行为契约：跨 skill 只通过稳定文件 artifact、host 配置等边界协作，不耦合内部实现。
- 强类型边界、窄扩展点：扩展点必须有清楚职责、最小权限和可验证的授权来源，不引入通用生命周期权力。
- 抽象一旦能被滥用即设计未完成；删除优先，废弃路径直接移除，不保留兼容空壳或历史副本。
- 变更必须可验证：优先用 behavior test 或端到端可观察输入 / 输出 / 副作用断言；source-regression 只锁跨 artifact 一致性、事实源唯一性和授权边界。
- 治理前置：架构性或流程性规则必须和机械验证手段同时进入仓库。
- 正确架构优先：随着 skill 增长若边界自然变松，应重设计而非增加 escape hatch。
- 命名跟随职责：被脚本解析或跨 agent 传递的名字由所属事实源声明格式、canonical write policy 和迁移策略。
- 哲学文档不写 schema/identifier 版本后缀；Release semver 只作为 `.version-bump.json` 的发布坐标。

## 共识引擎哲学

权威表述见英文 canonical [`README.md`](./README.md) 的 Core 段；中文 companion 为 [`README.zh-CN.md`](./README.zh-CN.md)。

- 偏置独立多角度：同一决策点的 solver / reviewer 互不可见，各自带先验立场。
- meta-judge 收敛：分歧收敛到达成、接近或真停滞等固定出口。
- concrete plan 必过共识闸：方向明确也不能由单个 agent 直接落地。
- 验证侧同构：产物必须经过独立 reviewer 和固定 review truth table。
- controller 纯编排：caller 只 intake、派发 worker、route 和汇总；实现、验证、修复、review、design solve 全部 delegate 给 worker。
- 不伪造共识；只有确实需要人做产品、战略、治理或权限决策时才升级给 maintainer。

## 角色与边界

- **maintainer(人)**：产品、战略和治理级决策；罕见的手工生命周期动作。
- **agent worker**：在隔离 worktree 内承担实现、验证、修复、review 和 design solving；不得 commit、push、merge、tag、release 或修改外部状态。
- **host 项目**：skill 不修改 host 的 git、CI 或 policy 配置，只读取 host 明确注入的事实。

## 新增 / 修改 skill

- 每个 skill 一个目录 `skills/<kebab-name>/SKILL.md`，frontmatter 只需 `name` 和触发条件描述的 `description`。
- 重型参考可拆到 `REFERENCE.md`，机械代码放 `scripts/`，prompt 模板放 `prompts/`。
- 修改 skill 必须先记录 no-skill baseline，再用 behavior test 或端到端证据验证行为。
- 新增 runtime surface 必须说明允许做什么、不允许做什么、事实源在哪里、如何验证。

## Agent 工作约定

- 不弹 popup：仓库内 skill、清单和本文件的维护由 agent 自决。
- Skill routing 优先；artifact 路径相对 `$REPO_ROOT`，不硬编码 host 事实。
- controller worktree 统一放在 `<repo-root>/.worktrees/<name>/`，不创建 sibling worktree。
- 没有明确授权时，不修改 host 配置、不发布 release、不关闭外部状态面、不执行不可逆生命周期动作。
- README pair 是唯一英文 canonical public-doc carve-out；README.md 与 README.zh-CN.md 双向链接，大段顺序对齐即可。

## Python 代码规范

本节只约束本仓库 Python skill scripts 和测试代码。

- 类型边界清楚：公共函数和方法有类型注解，跨层结构化事实优先使用 dataclass、TypedDict 或明确投影类型；宽 `Mapping` / `dict` 只用于外部 JSON adapter。
- 职责分层：I/O、外部副作用、环境读取和决策逻辑分开；纯函数优先，可机械验证的判断从副作用执行体中拆出。
- 复杂度不继续膨胀：过长函数或高复杂度分支要拆成职责清晰的 helper / projection，或在同次变更中记录具体后续计划。
- fail-closed 可诊断：失败路径抛出具体异常或返回明确原因；禁止裸 `except`、吞错和静默 fallback。
- 命名跟随职责：稳定 API、类型和 parser 的名字表达职责，不把临时实现泄露进稳定接口；代码风格跟随既有事实源。

## 版本同步(强制)

改版本号时，以 `.version-bump.json` 为唯一映射事实源；其中列出的 manifest 文件必须同步为同一版本。

## 工程约定

- 文档分层：README pair 写公开身份，CLAUDE.md 写仓库宪法，`skills/<name>/SKILL.md` 写 skill 合同。
- 废弃文件直接删除，历史由 git 保存；不保留 `.bak`、`.old` 或 `.deprecated` 副本。
- Git 提交聚焦单一目的；实现 worker 不执行生命周期动作。
- 跨平台清单必须与 `skills/` 目录一致；测试按风险扩展，禁止用源码字面断言替代行为验证。
- 临时日志和一次性报告不是长期事实源；错误、失败和 skip 必须抛出或写可 grep 的诊断日志。

## 通用工程基本规则(面向对象,跨语言)

落到代码层的通用面向对象设计准则,**跨语言适用** —— C# / Java / Python / TS 等具体语言机制只作举例,约束的是**设计意图**而非某语言语法。本节先列**面向对象核心原则**(为什么这么设计),再给**实现层细则**(具体怎么做);细则是原则的落地,与已有「异常必抛出 + 必记日志」「事实源唯一」「命名跟随职责」等不动点同向,按事实源唯一不在多处重复声明。

**核心原则(OO principles)**

- **单一职责(SRP)**:一个类 / 模块只有一个变化原因,只承担一组内聚职责;职责一多就拆。
- **开闭(OCP)**:对扩展开放、对修改关闭;新增行为靠新增类型 / 扩展点,不改既有稳定代码。
- **里氏替换(LSP)**:子类型必须能无差别替换父类型而不破坏其契约 —— 前置条件不加强、后置条件不减弱、不抛父类型未声明的异常。
- **接口隔离(ISP)**:接口窄而专,调用方不被迫依赖用不到的方法;宁可多个小接口,不要一个胖接口。
- **依赖倒置(DIP)/ 面向抽象**:高层与低层都依赖抽象,而非高层依赖低层;依赖、参数、返回值优先用接口 / 抽象类型,面向接口编程不面向具体实现,便于替换、mock 与测试。
- **组合优于继承**:优先用组合 / 委托复用行为;继承只用于真正稳定的 is-a 契约,不为复用代码而继承。
- **迪米特法则(最少知识)**:只与直接协作者交互,不链式穿透 `a.b().c().d()` 抓别人内部结构;需要什么就让对方直接给到手。
- **高内聚低耦合**:相关的聚在一起、无关的分开;跨边界只经稳定契约,不依赖对方内部实现。
- **封装**:状态私有,只经方法 / 契约暴露;不泄露内部表示,不让外部依赖可变内部字段。
- **DRY(一处权威)**:同一知识只有一处权威表达,重复即抽象(承「事实源唯一」不动点)。
- **KISS / YAGNI**:用能解决当前问题的最简设计;不为臆想的未来需求预埋抽象与扩展点。

**实现层细则(上述原则的落地)**

- **一方法一职**:一个方法只做一件事,只做方法名所表达的事,不包揽整条流程。
- **参数不搬运**:实现类只关心自己的参数和字段,不做参数转换搬运;需要某类型就直接传该类型(如需要字节串就传字节串),不传通用容器再在方法内转换,转换交给扩展 / 工具函数。
- **依赖过多即拆分**:一个类依赖太多 service 时,把部分能力改为参数传入或拆成更小职责,不让单个类成为万能入口。
- **取值依赖一大堆就建 manager/service**:若取某个东西要先备齐一堆前置依赖,把它收敛成有明确状态边界的 manager / service(例如「需要 chain id 才能算」的能力不要做成无上下文静态 helper)。
- **service 无状态**:service 必须无状态,命名以 `Service` 结尾。
- **改接口先走评审**:改动任何对外 interface / 契约前,必须先开 issue / PR、至少 2 人 review 并组织讨论,想改的人负责发起;本仓库即落到共识闸 —— concrete plan 必过多角度共识(见「共识引擎哲学」)。
- **接口保持简单**:加方法前先尝试扩展方法 / 组合实现,能不进接口就不进接口。
- **delegate 先想 interface**:想用 delegate / 函数指针前,先考虑能否用 interface 表达职责。
- **元编程需顶层评审**:attribute / 反射 / delegate / 语言级 event 等元编程机制需顶层开发评审讨论后才用,不默认使用。
- **禁全局可变静态**:不用静态全局属性 / 全局可变状态。
- **运行期不改静态值**:运行期绝不设置或改写静态值。
- **只读不可降级**:字段在编码时已是 readonly / final / 只读时,永远不为图方便移除只读修饰。
- **event 只在同命名空间内 raise**:只在同一命名空间 / 模块内发出 event,不跨模块乱发。
- **符号引用不用裸字符串**:引用符号名用语言的符号引用机制(如 `nameof`),不写裸字符串字面量 `"MethodName"`。
- **跟随既有风格**:加代码遵循文件既有风格;不得不破坏风格时,就地标 `TODO: review required`。
- **基础设施层引用方向**:基础设施层只能被同一命名空间中 Domain 以上的层引用,不被反向依赖。
- **子命名空间不反向引用父**:子命名空间不反向引用父命名空间(如 `X.Kernel.Node` 不应引用 `X.Kernel`)。
- **不乱注入跨命名空间基础设施**:不理解含义时,不注入 / 引入其它命名空间(模块 / 层)的基础设施。
- **复制即抽象**:需要复制代码时,抽成方法 / 扩展方法 / delegate,不就地复制。
- **通用化优先**:重复出现的逻辑、被多处依赖的能力,提炼为不绑定单一调用点的通用方法 / manager / service,不写死一次性专用实现。
- **非用户输入不防御性校验**:参数若非用户输入,不必写防御性校验,让异常自然抛出(承「异常必抛出」不动点,失败须可诊断)。
- **不吞基类异常**:不 catch 基类异常(等于一次吞掉一切),除非确知在做什么(承「严禁吞掉 / 静默」不动点)。

**常用设计模式(Design Patterns)**

成熟解法目录,服务于上面的原则、按需取用;**忌为模式而模式**(承 KISS / YAGNI),简单问题别套重模式。

创建型:

- **工厂 / 抽象工厂(Factory)**:把"创建哪个具体类型"从调用方抽走,调用方只依赖抽象;按上下文(配置 / chain id 等)决定实例时用,是 DIP 的落地。
- **建造者(Builder)**:分步构造复杂对象,消除长参数列表 / 可选参数爆炸。
- **单例(Singleton)**:慎用 —— 它常是全局可变状态的伪装;优先用 DI 容器管理生命周期,不自建运行期静态单例(承「禁全局可变静态」「运行期不改静态值」)。

结构型:

- **适配器(Adapter)**:把不兼容接口包成目标接口,隔离第三方 / 旧实现。
- **装饰器(Decorator)**:用组合叠加横切行为(日志 / 缓存 / 重试)而不改原类,体现开闭 + 组合优于继承。
- **代理(Proxy)**:在真实对象前加同接口的间接层(延迟加载 / 访问控制 / 远程调用)。
- **外观(Facade)**:给一组子系统一个简化入口,降低调用方耦合(呼应高内聚低耦合)。

行为型:

- **策略(Strategy)**:把可替换算法封到同一接口、运行期注入,是「delegate 先想 interface」的正解。
- **观察者 / 事件(Observer)**:发布-订阅解耦;本仓库落到「event 只在同命名空间内 raise」。
- **模板方法(Template Method)**:父类定骨架、子类填可变步骤,骨架稳定 + 步骤可变时用。
- **命令(Command)**:把请求封成对象,便于排队 / 撤销 / 审计。
- **责任链(Chain of Responsibility)**:请求沿处理器链传递直到被处理,适合管线 / 中间件。
- **状态(State)**:把状态相关行为分到状态对象,消灭巨型 `switch`。

## 共识研发不动点

<!-- consensus-rnd:foundational-invariants:start -->
- FI-001 AI 产物默认不可信；进入主线前必须经过独立检查、review 或自动验证。
- FI-002 Host 事实由 host 配置或规则注入；通用 skill 不硬编码具体项目、组织、路径、分支或人员事实。
- FI-003 稳定核心保持小而可审计；高频变化留在 host 规则、prompt、脚本或扩展层。
- FI-004 跨进程、跨 turn 或跨节点的事实必须有权威记录。
- FI-005 边界优先于便利；职责、层级、协议和状态所有权必须清楚。
- FI-006 变更必须可验证且基于 evidence；失败、缺口和越界承诺要显式暴露。
- FI-007 删除优先；废弃路径直接移除，除非 host 规则明确要求迁移期兼容。
<!-- consensus-rnd:foundational-invariants:end -->
