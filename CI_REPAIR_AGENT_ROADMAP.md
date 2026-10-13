# CI 失败自动修复 Agent：现状、差距与后续开发路线

> 更新日期：2026-10-13。依据本仓库提交 `8f20dbb`、示例仓库合并提交 `ca84b5d`、本地保存的修复证据，以及已核对的 GitHub Actions / PR 输出。后续状态仍以 GitHub 和 WSL Git 的实时结果为准。

## 1. 项目目标与当前结论

目标是在 mini-SWE-agent 基础上二次开发一个**可复现、可审查、可量化**的 CI 失败自动修复 Agent，用作 AI 应用开发 / Agent 开发实习的项目经历。当前范围固定为：GitHub Actions 中 `main` 分支的 Python `pytest` 失败，自动提出 **Draft PR**，由人审核和合并。暂不扩展到 Jenkins、JUnit 输入、多 Agent 或编译失败。

**当前已完成三条受控端到端链路**：加法、乘法和字符串空白边界故障分别触发失败的 Pytest 工作流；修复工作流在隔离目录复现、调用模型、独立验证、创建 Draft PR；PR 检查通过后由人合并。第三例合并后的示例仓库 `main` 再次通过 Pytest。**当前尚不能宣称对一般 CI 失败具有稳定修复率**：案例都来自同一个小型示例仓库，尚无固定评估集、系统的负例测试或跨项目验证。

前两例 PR 的 Pytest 检查最初都出现过 `action_required`，经人工批准后才运行并通过；第三例发布后首次查询尚无检查结果，随后 PR 检查运行并通过。因此已证明**自动创建 PR 与最终检查通过**，不能写成“PR 检查全程无需人工介入”。

## 2. 已完成的工作与可核对证据

| 能力 | 当前实现 / 证据 | 边界 |
| --- | --- | --- |
| 仓库代理 | [仓库环境](src/minisweagent/environments/repository.py)提供搜索、读文件、应用补丁、运行测试和结束等工具；[仓库模型](src/minisweagent/models/repository_model.py)负责工具调用；[配置](src/minisweagent/config/repository.yaml)设置 40 步及成本上限。 | 这是本项目相对上游 mini-SWE-agent 的二次开发，应在简历中明确写成“基于 mini-SWE-agent 扩展”。 |
| 开发环境 | 新版相关测试在 WSL 中记录为 `15 passed, 1 warning`；第三例故障提交的容器测试为 `1 failed, 4 passed`，代理补丁后全量为 `5 passed`。 | 相关测试不是整个项目测试集；第三例属于受控演示。 |
| CI 入口 | [ci_repair.py](src/minisweagent/run/ci_repair.py)接收仓库、工作流名、SHA、日志、输出目录和可选运行 ID；[pyproject.toml](pyproject.toml)注册 `mini-ci-repair`。 | 目前仅识别文本日志中的 `FAILED ...::...`，没有解析 `pytest.xml`。 |
| 独立验证 | 入口创建 `baseline`、`agent`、`candidate` 三个检出目录；原始 SHA 连续跑两次全量 pytest，要求两次失败节点与原 CI 完全一致；补丁先跑原失败测试再跑全量 pytest；限制变更为 `src/*.py`。 | 两次相同失败只能降低不稳定故障的误报风险，不能证明测试绝对稳定。 |
| 隔离和权限 | 基线及候选 pytest 在禁网、只读挂载的 Docker 容器中运行；Agent 的 `bash` / `run_tests` 命令在另一个受限容器中执行。Agent 控制进程、模型调用、搜索/读文件和 `apply_patch` 在 GitHub Runner 宿主执行，只修改隔离的 agent 检出目录；[修复工作流模板](examples/ci_repair_demo/.github/workflows/repair.yml)把只读 `repair` 作业与有写权限的 `publish` 作业分开。 | 已实现测试命令隔离和作业权限分离，但不是“整个 Agent 都在容器里”；尚未完成恶意补丁、文件模式、依赖供应链等系统测试。 |
| 自动 PR | 失败的 `Pytest` `workflow_run` 触发修复；发布前检查报告状态、运行 ID、SHA、`main` 是否仍指向失败提交、补丁能否应用及修改路径；创建 Draft PR，同一 SHA 的已有 PR 不重复创建。 | 只对示例仓库 `main` 上的 `push` 失败启用；不是通用 GitHub App。 |
| 结果证据 | `report.json`、两次基线/目标/全量测试日志、`patch.diff` 和 trajectory 作为 Actions artifact 保存；PR 正文链接到原 CI 与修复运行。 | artifact 有保留期；轨迹和日志在公开前应检查敏感信息。 |

### 三个真实端到端案例

| 案例 | 原故障与 CI | 修复结果 | 独立验证 / 模型记录 |
| --- | --- | --- | --- |
| 加法错误 | 失败 SHA `05520d4d4f2476b41fc2f7edecb895227a3afe84`；[Pytest 运行](https://github.com/fuziran/ci-repair-demo/actions/runs/38339529484) | [修复运行](https://github.com/fuziran/ci-repair-demo/actions/runs/38339549288)第 2 次尝试成功；[PR #1](https://github.com/fuziran/ci-repair-demo/pull/1)最初为 Draft，只将 `a - b` 改为 `a + b`；PR 检查通过，已人工合并。 | 基线 `test_add` 失败；候选全量 `2 passed`；已归档报告记录 5 次模型调用、估算成本 **$0.000828588**、入口耗时 **11.52 秒**。首次尝试因模型密钥未正确设置失败。 |
| 乘法错误 | 失败 SHA `9052dc1d4b290b508e089b16fca02ef46c4d6a22`；[Pytest 运行](https://github.com/fuziran/ci-repair-demo/actions/runs/38348094027) | [修复运行](https://github.com/fuziran/ci-repair-demo/actions/runs/38348122879)已重跑一次；[PR #2](https://github.com/fuziran/ci-repair-demo/pull/2)最初为 Draft，只将 `a + b` 改为 `a * b`；PR 检查通过，已人工合并。 | 基线 `test_multiply` 失败；补丁后目标 `1 passed`、全量 `3 passed`。目前归档的报告记录 5 次调用、**$0.00046395**、**12.01 秒**；此前聊天输出中，同一运行 ID 的另一份报告为 6 次调用、约 **$0.000500502**、**13.72 秒**。两份报告应按不同尝试区分，不计作两个独立案例。 |
| 字符串空白边界 | 失败 SHA `6cf07e8d4adcfd34f9dcadd61ea97dd52e8f12a8`；[Pytest 运行](https://github.com/fuziran/ci-repair-demo/actions/runs/38375617864) | [修复运行](https://github.com/fuziran/ci-repair-demo/actions/runs/38375646666)的 `repair` 与 `publish` 均成功；[PR #3](https://github.com/fuziran/ci-repair-demo/pull/3)最初为 Draft，仅将 `src/slug.py` 的 `split(" ")` 改为 `split()`；[PR 检查](https://github.com/fuziran/ci-repair-demo/actions/runs/38375927602)通过，人工 squash 合并为 `ca84b5dbc94f906a76ff48d64a81159fab2cc666`；[合并后 main 检查](https://github.com/fuziran/ci-repair-demo/actions/runs/38378797950)通过。 | 两次基线都只在 `tests/test_slug.py::test_slugify_whitespace` 失败；补丁后目标 `1 passed`、全量 `5 passed`。本地保存的 `repair-result/report.json` 记录 `verified`、6 次模型调用、估算成本 **$0.001198908**、入口耗时 **15.43 秒**。 |

上述成本是单次尝试的模型接口估算值，**不含 GitHub Runner、Docker 构建和等待时间，也不代表该案例所有重跑尝试的累计成本**。`duration_seconds` 是入口自身耗时，不等于完整 CI 墙钟时间。三个成功案例不能换算成“通用修复率”，同一运行的重跑也不是新案例。加法案例早期 artifact 只有 `pytest.log`，修复结果缺少 `target.log`；乘法案例的已归档 artifact 包含这两项。按各自原始版本保存证据，不补造历史文件。

## 3. 距离可用于简历的完整项目还缺什么

### P0：把当前成果固定成可复核基线

当前进度：PR #1/#2/#3 均已人工合并，第三例合并提交为 `ca84b5d`，其 `main` 检查已通过。前两例的原始 CI 与修复 artifact、PR 元数据和 diff 已保存到 `$HOME/ci-repair-evidence/`；第三例的修复 artifact 已保存到 `$HOME/ci-repair-evidence/slug-6cf07e8/`，原始 CI artifact 与 PR 元数据仍需单独归档。前两例修复运行的当前 `run_attempt` 均为 2；第三例未记录为重跑。演示仓库的 `AGENT_REF=ci-repair-reliability-8f20dbb`，该标签当前指向提交 `8f20dbb3ed33cf7fd30cfaad349881d9fb7a4ab9`；Git 标签可被重写，仍需在案例记录中保存提交 SHA。镜像基础标签和部分依赖也未固定到不可变版本。

1. 在 WSL 中确认本仓库和示例仓库的 `status`、提交及 PR #3 状态。提交 `cab4da7` 推送后，本仓库在 WSL 中显示工作区干净；Windows Git 通过 WSL UNC 路径曾显示四个“修改”，与 WSL 状态不一致，应以 WSL Git 为准。
2. 补齐第三例原始 CI artifact、PR 元数据和检查结果；三例都保存故障 SHA、原 CI 日志、补丁、`report.json`、PR 链接与检查结果。原始轨迹先放在本地私有目录；公开前检查敏感信息。
3. 完整记录三例运行时版本：Agent 提交/标签、故障 SHA、模型名称及版本、Python、pytest、Docker 基础镜像摘要、配置和预算。CI 与修复镜像现均选 Python 3.12，pytest 固定为 9.0.2；`python:3.12-slim` 与本地镜像名 `latest` 仍不是不可变镜像引用。

**完成标准**：任何人能根据一张案例记录找到原始失败运行、补丁、验证报告、PR 和 PR 检查；无需依赖聊天记录。

### P1：加强正确性、失败处理和安全边界

| 任务 | 建议改动位置 | 验收方式 |
| --- | --- | --- |
| 结构化失败输入 | `src/minisweagent/run/ci_repair.py` | 优先解析现有 `pytest.xml` 或保留文本日志兼容；区分“无 pytest 失败”“pytest 启动/依赖失败”“可重现断言/异常失败”。不要把 `pytest.xml` 输入误写成支持任意 JUnit 生态。 |
| 减少误报（已实现，待补负例） | 同上 | 已对原 SHA 跑两次基线并要求失败节点与原 CI 完全一致；节点变化标记 `unstable`，不调用模型、不创建 PR。下一步用真实临时仓库验证拒绝路径。 |
| 完整失败报告（基础实现） | 同上 | 已记录阶段、原因、耗时与可得的调用数/成本，并处理超时与执行异常；继续用真实失败场景验证模型异常、补丁不适用、测试失败和变更越界的报告。 |
| 发布前的负例检查 | `examples/ci_repair_demo/.github/workflows/repair.yml` | 用真实临时 Git 仓库或受控测试分支验证 SHA 不匹配、过期 `main`、重复 PR、修改测试/工作流/依赖文件、无效补丁及软链接/文件模式变更均不发布。发布作业不能执行候选仓库代码。 |
| 固定依赖与权限 | 两个工作流、镜像构建 | Agent 标签已指向固定提交，pytest 已固定；继续记录基础镜像摘要、关键依赖及配置，确认测试容器中没有 PR 写凭据和模型密钥，限制 Actions 权限及并发/成本预算。 |
| CI 与复现环境一致（已完成主要对齐） | `pytest.yml`、`Dockerfile.repository` | 原 CI 与修复镜像都使用 Python 3.12 和 pytest 9.0.2；乘法故障在新镜像中本地重放并修复，第三例完成 GitHub 端到端验证。后续记录具体 Python 补丁版本和镜像摘要。 |
| 工作流本身可维护 | 工作流模板及相应验证 | 把实际演示仓库中的工作流与仓库模板保持同步；配置检查、动作版本升级时重跑一条端到端案例。 |
| PR 检查的触发条件 | 示例仓库 Actions 设置与 `pytest.yml` | 前两例出现 `action_required`，第三例 PR 检查最终通过；明确记录人工批准步骤和权限设置，不将链路描述为完全无人值守。 |

此处只写**有意义的负例和集成验证**。仓库规则要求不要用 mock/patch 模拟外部行为，也不要写仅重复实现细节的平凡测试。可用临时 Git 仓库、真实 pytest 和 Docker 来验证入口边界；PR 创建部分可在受控示例仓库中验证。

### P2：完成固定约 20 例的评估

先冻结案例，再运行模型，避免只挑成功案例。建议在项目中建立 `eval/cases.jsonl` 和评估说明，**每个案例是一个独立的故障提交**，记录可运行的基线、故障 SHA、预期失败节点、依赖/镜像版本与类别。类别至少覆盖：简单断言、异常、边界条件、多文件定位、容易引入回归的修复、无法复现或不稳定失败，以及明确超出 `src/*.py` 范围的拒绝案例。不要把相同逻辑的加减乘除错误凑成主要样本。

评估程序逐例在同一模型、配置、单例时限与成本上限下运行现有入口，保存原日志、trajectory、补丁、验证日志和机器可读结果。多数案例可以先在本地隔离环境批量评估；已跑通的 GitHub Actions → Draft PR 链路保留少量真实端到端演示即可，避免为 20 例制造大量 PR 和无谓费用。

每例最少记录：`case_id`、`bug_sha`、`category`、`reproduced`、`candidate_submitted`、`valid_fix`、`regression`、`failure_reason`、`duration_seconds`、`model_cost_usd`、`model_calls`、`pr_url`（未发布则为空）。所有失败、超时和拒绝案例都保留记录。`valid_fix` 必须同时满足原故障可复现、原失败测试通过、全量测试通过及变更范围合规；若还有独立隐藏回归测试，应一并通过。模型报告成本与实际账单分开标注。

报告时固定分母为全部 **20** 例：复现率 = `reproduced / 20`，有效修复率 = `valid_fix / 20`，回归案例占比 = `regression / 20`。可补充“在已复现案例中的修复率”，但必须显示其分母。列出各状态的数量、总模型成本、每个有效修复的平均模型成本、耗时中位数和最长耗时；不要把案例筛掉后再计算主要成功率。

**完成标准**：20 例清单、原始证据和汇总表可复算；公开展示至少一个成功案例、一个失败案例及失败原因。三条现有成功演示是链路证据，不能替代这个评估集。

### P3：整理成可信的求职项目材料

在 `docs/` 新增简短的架构说明和复现说明：系统边界、事件来源、三份检出、Agent/验证器/发布作业的职责、权限边界、状态与失败分类、运行命令、模型费用和设计取舍。准备一个 2–3 分钟演示：展示红色 Pytest、修复 artifact、Draft PR diff、PR 绿色检查和一次失败时拒绝发布。附上 20 例评估表与已知限制。当前仓库规则要求**不要自行追加 README**；后续若确需在 README 增加入口，先另行明确安排。

简历表述应突出你的增量贡献，例如：仓库工具接口、隔离执行、独立验证、GitHub Actions 事件联动、Draft PR 发布与评估框架。可以写“已在三个受控故障提交上完成端到端演示”，暂时不要写“自动修复率 X%”或“适用于所有 Python 项目”。等 20 例完成后，再填入真实统计值和分母。

### P4：再扩展范围

只有 P0–P3 稳定后，才加入 Ruff 失败类型；先定义其独立解析、复现和验收规则，再验证不会干扰 pytest 链路。JUnit、Jenkins、多 Agent、编译失败及跨仓库通用化放在更后面，按新增证据决定是否值得做。

## 4. 推荐的实际开发顺序

1. **补齐第三例归档与环境记录。** 不重跑模型。保存原始 Pytest artifact、PR 元数据和检查状态，核对三例的版本、配置与成本口径；以 WSL Git 状态为准。
2. **下一项代码任务：发布前拒绝路径。** 用真实临时 Git 仓库或受控分支验证过期 `main`、重复 PR、越界文件、无效补丁及软链接/文件模式变化都不会发布；必要时收紧 `repair.yml` 的检查。补齐入口 `unstable`、候选失败、超时等关键负例，不使用 mock/patch。
3. **冻结 20 例并批量评估。** 先写案例表和判定规则，再跑模型；逐例保留失败证据并汇总指标。
4. **最后整理展示材料。** 制作架构说明、复现步骤、成功与拒绝案例演示及带分母的评估表。

### 立即可运行的核对命令（在 WSL Bash 中）

```bash
AGENT_ROOT="$HOME/projects/mini-swe-agent-portfolio"
DEMO_ROOT="$HOME/projects/ci-repair-demo"
DEMO_REPO="fuziran/ci-repair-demo"

cd "$AGENT_ROOT"
git status --short
git log -1 --oneline
.venv/bin/python -m pytest -q \
  tests/environments/test_repository.py \
  tests/models/test_repository_model.py \
  tests/run/test_ci_repair.py

git -C "$DEMO_ROOT" status --short
git -C "$DEMO_ROOT" log -1 --oneline
gh pr view 3 -R "$DEMO_REPO" --json state,isDraft,mergedAt,url
gh pr checks 3 -R "$DEMO_REPO"
gh run view 38378797950 -R "$DEMO_REPO"
```

第三例的修复 artifact 已保存；如需补齐原始 Pytest artifact 和 PR 元数据，可在**个人本地目录**执行以下命令，不把未经检查的 trajectory 提交到公开仓库：

```bash
EVIDENCE_ROOT="$HOME/ci-repair-evidence"
mkdir -p "$EVIDENCE_ROOT/slug-6cf07e8/original-ci"
gh run download 38375617864 -R fuziran/ci-repair-demo \
  -n pytest-log -D "$EVIDENCE_ROOT/slug-6cf07e8/original-ci"
gh pr view 3 -R fuziran/ci-repair-demo \
  --json number,url,state,isDraft,mergedAt,mergeCommit,statusCheckRollup \
  > "$EVIDENCE_ROOT/slug-6cf07e8/pr.json"
```

若 GitHub artifact 已到期，记录其不可用状态，并保留现有 PR diff、Actions 日志和本地验证记录；不要重建历史数据冒充原运行证据。

## 5. 最终验收定义

项目可以作为成熟的实习项目展示时，应同时具备：

- 至少一条可重复的真实 GitHub Actions 失败 → 独立修复验证 → Draft PR → PR 检查通过链路；现有三条案例已证明基本链路。
- 明确的安全边界：测试执行无 PR 写凭据，发布前复核来源和补丁范围，候选失败或不稳定时不发布；通过负例验证这些规则。
- 固定 20 例、统一判定与完整失败记录；给出带分母的修复率、回归情况、时间和费用。
- 可复现的版本/配置、架构说明、运行步骤、可打开的 PR 和 CI 证据；简历陈述与证据完全一致。

当前最有价值的投入是**可靠性与评估证据**。继续叠加框架、语言和多 Agent 会扩大范围，却不能补足上述关键证据。
