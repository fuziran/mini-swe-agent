# CI 失败自动修复 Agent：现状、差距与后续开发路线

> 更新日期：2026-10-13。依据本仓库当前代码、示例仓库本地 Git 记录，以及此前保存的 GitHub Actions / PR 输出。远端实时状态和当前 WSL 工作区状态仍需按下文命令复核。

## 1. 项目目标与当前结论

目标是在 mini-SWE-agent 基础上二次开发一个**可复现、可审查、可量化**的 CI 失败自动修复 Agent，用作 AI 应用开发 / Agent 开发实习的项目经历。当前范围固定为：GitHub Actions 中 `main` 分支的 Python `pytest` 失败，自动提出 **Draft PR**，由人审核和合并。暂不扩展到 Jenkins、JUnit 输入、多 Agent 或编译失败。

**当前已跑通最小端到端链路**：两个受控故障提交触发了失败的 Pytest 工作流；修复工作流在隔离目录复现、调用模型、独立验证、创建 Draft PR；PR 的 Pytest 检查通过。第二个修复在示例仓库本地 `main` 的最新提交中已显示为合并提交。**当前尚不能宣称对一般 CI 失败具有稳定修复率**：两个案例都来自很小的计算器项目，尚无固定评估集、系统的负例测试或跨项目验证。

两次 PR 的 Pytest 检查最初都出现过 `action_required`，经人工批准后才运行并通过。因此已证明**自动创建 PR 与最终检查通过**，尚未证明“PR 检查全程无需人工介入”。这个运行条件应在演示说明中如实标注，并在后续复验。

## 2. 已完成的工作与可核对证据

| 能力 | 当前实现 / 证据 | 边界 |
| --- | --- | --- |
| 仓库代理 | [仓库环境](src/minisweagent/environments/repository.py)提供搜索、读文件、应用补丁、运行测试和结束等工具；[仓库模型](src/minisweagent/models/repository_model.py)负责工具调用；[配置](src/minisweagent/config/repository.yaml)设置 40 步及成本上限。 | 这是本项目相对上游 mini-SWE-agent 的二次开发，应在简历中明确写成“基于 mini-SWE-agent 扩展”。 |
| 开发环境 | 用户在 WSL 中运行仓库环境/模型测试，记录为 `6 passed`；本地示例仓库曾复现 `1 failed, 1 passed`，随后由代理改为 `2 passed`。 | 这不是整个项目测试集的结果；本次文档编写未重新运行 WSL 测试。 |
| CI 入口 | [ci_repair.py](src/minisweagent/run/ci_repair.py)接收仓库、工作流名、SHA、日志、输出目录和可选运行 ID；[pyproject.toml](pyproject.toml)注册 `mini-ci-repair`。 | 目前仅识别文本日志中的 `FAILED ...::...`，没有解析 `pytest.xml`。 |
| 独立验证 | 入口创建 `baseline`、`agent`、`candidate` 三个检出目录；原始 SHA 跑全量 pytest，补丁先跑原失败测试再跑全量 pytest；限制变更为 `src/*.py`。 | 基线仅运行一次；没有专门识别 flaky 测试。 |
| 隔离和权限 | 基线及候选 pytest 在禁网、只读挂载的 Docker 容器中运行；Agent 的 `bash` / `run_tests` 命令在另一个受限容器中执行。Agent 控制进程、模型调用、搜索/读文件和 `apply_patch` 在 GitHub Runner 宿主执行，只修改隔离的 agent 检出目录；[修复工作流模板](examples/ci_repair_demo/.github/workflows/repair.yml)把只读 `repair` 作业与有写权限的 `publish` 作业分开。 | 已实现测试命令隔离和作业权限分离，但不是“整个 Agent 都在容器里”；尚未完成恶意补丁、文件模式、依赖供应链等系统测试。 |
| 自动 PR | 失败的 `Pytest` `workflow_run` 触发修复；发布前检查报告状态、运行 ID、SHA、`main` 是否仍指向失败提交、补丁能否应用及修改路径；创建 Draft PR，同一 SHA 的已有 PR 不重复创建。 | 只对示例仓库 `main` 上的 `push` 失败启用；不是通用 GitHub App。 |
| 结果证据 | `report.json`、基线/目标/全量测试日志、`patch.diff` 和 trajectory 作为 Actions artifact 保存；PR 正文链接到原 CI 与修复运行。 | artifact 有保留期；轨迹和日志在公开前应检查敏感信息。 |

### 两个真实端到端案例

| 案例 | 原故障与 CI | 修复结果 | 独立验证 / 模型记录 |
| --- | --- | --- | --- |
| 加法错误 | 失败 SHA `05520d4d4f2476b41fc2f7edecb895227a3afe84`；[Pytest 运行](https://github.com/fuziran/ci-repair-demo/actions/runs/38339529484) | [修复运行](https://github.com/fuziran/ci-repair-demo/actions/runs/38339549288)成功；[Draft PR #1](https://github.com/fuziran/ci-repair-demo/pull/1)只将 `a - b` 改为 `a + b`；PR 检查通过。此前记录显示随后经人工合并。 | 基线 `test_add` 失败；候选全量 `2 passed`；5 次模型调用，报告估算成本约 **$0.00083**，入口耗时 **11.52 秒**。修复运行首次因模型密钥未正确设置失败，重跑成功。 |
| 乘法错误 | 失败 SHA `9052dc1d4b290b508e089b16fca02ef46c4d6a22`；[Pytest 运行](https://github.com/fuziran/ci-repair-demo/actions/runs/38348094027) | [修复运行](https://github.com/fuziran/ci-repair-demo/actions/runs/38348122879)成功；[Draft PR #2](https://github.com/fuziran/ci-repair-demo/pull/2)只将乘法中的 `a + b` 改为 `a * b`；PR 检查通过。示例仓库本地 `main` 当前最新提交为 `d7c2704 fix(run): repair reproducible pytest failure (#2)`。 | 基线 `test_multiply` 失败；补丁后目标 `1 passed`、全量 `3 passed`；6 次模型调用，报告估算成本约 **$0.00050**，入口耗时 **13.72 秒**。同一修复运行重跑后没有产生第二个 PR。 |

上述成本是模型接口报告的估算值，**不含 GitHub Runner、Docker 构建、等待时间和重跑成本**。`duration_seconds` 是入口自身耗时，也不等于完整 CI 墙钟时间。两个成功案例不能换算成“通用修复率”，更不能把同一运行的重跑算作新案例。

## 3. 距离可用于简历的完整项目还缺什么

### P0：把当前成果固定成可复核基线

1. 在 WSL 中确认本仓库和示例仓库的 `status`、提交和 PR #2 远端状态。Windows Git 通过 WSL UNC 路径可能显示内容差异为零的“修改”，应以 WSL Git 为准；不要覆盖真实未提交改动。
2. 下载两次成功修复的 Actions artifact，保存 SHA、原 CI 日志、补丁、`report.json`、PR 链接和检查结果。原始轨迹先放在本地私有目录；公开前检查是否含密钥、私有代码或个人信息。
3. 将运行时版本固定并记录：本仓库提交 SHA、示例仓库故障 SHA、模型名称及版本、Python / pytest / Docker 镜像版本、配置和预算。当前 `AGENT_REF=feature/repository-agent` 是可变分支，`latest` 镜像也不利于复现。版本固定后再比较案例。

**完成标准**：任何人能根据一张案例记录找到原始失败运行、补丁、验证报告、PR 和 PR 检查；无需依赖聊天记录。

### P1：加强正确性、失败处理和安全边界

| 任务 | 建议改动位置 | 验收方式 |
| --- | --- | --- |
| 结构化失败输入 | `src/minisweagent/run/ci_repair.py` | 优先解析现有 `pytest.xml` 或保留文本日志兼容；区分“无 pytest 失败”“pytest 启动/依赖失败”“可重现断言/异常失败”。不要把 `pytest.xml` 输入误写成支持任意 JUnit 生态。 |
| 减少误报 | 同上 | 对原 SHA 至少重复一次基线测试；两次失败节点不一致时标记 `unstable`，不调用模型、不创建 PR。候选补丁继续跑目标测试与全量测试。 |
| 完整失败报告 | 同上 | 超时、模型异常、补丁不适用、测试失败和变更越界都写出统一 `report.json`，含阶段、原因、耗时、调用数/成本（若可得）与退出状态。不能只依赖 Agent 的 `finish` 文本。 |
| 发布前的负例检查 | `examples/ci_repair_demo/.github/workflows/repair.yml` | 用真实临时 Git 仓库或受控测试分支验证 SHA 不匹配、过期 `main`、重复 PR、修改测试/工作流/依赖文件、无效补丁及软链接/文件模式变更均不发布。发布作业不能执行候选仓库代码。 |
| 固定依赖与权限 | 两个工作流、镜像构建 | 记录并固定 Agent 提交及关键工具/镜像版本；确认测试容器中没有 PR 写凭据和模型密钥；限制 Actions 权限及并发/成本预算。 |
| CI 与复现环境一致 | `pytest.yml`、`Dockerfile.repository` | 当前原 CI 使用 Python 3.12，修复镜像基于 Python 3.11；将版本和测试依赖对齐后，用同一故障 SHA 确认仍能复现并修复。 |
| 工作流本身可维护 | 工作流模板及相应验证 | 把实际演示仓库中的工作流与仓库模板保持同步；配置检查、动作版本升级时重跑一条端到端案例。 |
| PR 检查的触发条件 | 示例仓库 Actions 设置与 `pytest.yml` | 记录 `action_required` 的确切原因与批准步骤；在相同权限设置下再次触发 PR 检查，明确哪些阶段仍需人工操作。 |

此处只写**有意义的负例和集成验证**。仓库规则要求不要用 mock/patch 模拟外部行为，也不要写仅重复实现细节的平凡测试。可用临时 Git 仓库、真实 pytest 和 Docker 来验证入口边界；PR 创建部分可在受控示例仓库中验证。

### P2：完成固定约 20 例的评估

先冻结案例，再运行模型，避免只挑成功案例。建议在项目中建立 `eval/cases.jsonl` 和评估说明，**每个案例是一个独立的故障提交**，记录可运行的基线、故障 SHA、预期失败节点、依赖/镜像版本与类别。类别至少覆盖：简单断言、异常、边界条件、多文件定位、容易引入回归的修复、无法复现或不稳定失败，以及明确超出 `src/*.py` 范围的拒绝案例。不要把相同逻辑的加减乘除错误凑成主要样本。

评估程序逐例在同一模型、配置、单例时限与成本上限下运行现有入口，保存原日志、trajectory、补丁、验证日志和机器可读结果。多数案例可以先在本地隔离环境批量评估；已跑通的 GitHub Actions → Draft PR 链路保留少量真实端到端演示即可，避免为 20 例制造大量 PR 和无谓费用。

每例最少记录：`case_id`、`bug_sha`、`category`、`reproduced`、`candidate_submitted`、`valid_fix`、`regression`、`failure_reason`、`duration_seconds`、`model_cost_usd`、`model_calls`、`pr_url`（未发布则为空）。所有失败、超时和拒绝案例都保留记录。`valid_fix` 必须同时满足原故障可复现、原失败测试通过、全量测试通过及变更范围合规；若还有独立隐藏回归测试，应一并通过。模型报告成本与实际账单分开标注。

报告时固定分母为全部 **20** 例：复现率 = `reproduced / 20`，有效修复率 = `valid_fix / 20`，回归案例占比 = `regression / 20`。可补充“在已复现案例中的修复率”，但必须显示其分母。列出各状态的数量、总模型成本、每个有效修复的平均模型成本、耗时中位数和最长耗时；不要把案例筛掉后再计算主要成功率。

**完成标准**：20 例清单、原始证据和汇总表可复算；公开展示至少一个成功案例、一个失败案例及失败原因。两条现有成功演示是链路证据，不能替代这个评估集。

### P3：整理成可信的求职项目材料

在 `docs/` 新增简短的架构说明和复现说明：系统边界、事件来源、三份检出、Agent/验证器/发布作业的职责、权限边界、状态与失败分类、运行命令、模型费用和设计取舍。准备一个 2–3 分钟演示：展示红色 Pytest、修复 artifact、Draft PR diff、PR 绿色检查和一次失败时拒绝发布。附上 20 例评估表与已知限制。当前仓库规则要求**不要自行追加 README**；后续若确需在 README 增加入口，先另行明确安排。

简历表述应突出你的增量贡献，例如：仓库工具接口、隔离执行、独立验证、GitHub Actions 事件联动、Draft PR 发布与评估框架。可以写“已在两个受控故障提交上完成端到端演示”，暂时不要写“自动修复率 X%”或“适用于所有 Python 项目”。等 20 例完成后，再填入真实统计值和分母。

### P4：再扩展范围

只有 P0–P3 稳定后，才加入 Ruff 失败类型；先定义其独立解析、复现和验收规则，再验证不会干扰 pytest 链路。JUnit、Jenkins、多 Agent、编译失败及跨仓库通用化放在更后面，按新增证据决定是否值得做。

## 4. 推荐的实际开发顺序

1. **今天：核对并归档两个真实案例。** 不重跑模型。核对 PR #2 是否已在远端合并；将证据从可能过期的 Actions artifact 下载到私有本地目录；确认 WSL 工作区状态和现有六个相关测试。若工作区有未提交改动，先记录内容，不覆盖。
2. **下一项代码任务：入口的稳定失败与错误报告。** 先给 `ci_repair.py` 建立统一结果状态与失败原因；用真实临时 Git 仓库覆盖“可复现”“不稳定”“越界修改”“候选仍失败”“异常/超时”几条关键路径。再改动工作流，避免入口改动与 CI 改动混在一起难以判断。
3. **随后：固定版本并跑第三条不同类型的端到端案例。** 选一个异常或边界条件故障，不再用同类算术运算；必须经过失败 CI、自动 Draft PR、人工审核及 PR 检查，确认改动没有破坏已有链路。
4. **最后：冻结 20 例并批量评估。** 先写案例表和判定规则，再跑模型；逐例保留失败证据并汇总指标。完成后制作架构图、演示和简历条目。

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
  tests/models/test_repository_model.py

git -C "$DEMO_ROOT" status --short
git -C "$DEMO_ROOT" log -1 --oneline
gh pr view 2 -R "$DEMO_REPO" --json state,isDraft,mergedAt,url
gh pr checks 2 -R "$DEMO_REPO"
```

若要保存现有两份成功运行的证据，可在**个人本地目录**执行以下命令；保留原始 artifact，不把未经检查的 trajectory 提交到公开仓库：

```bash
EVIDENCE_ROOT="$HOME/ci-repair-evidence"
mkdir -p "$EVIDENCE_ROOT/add" "$EVIDENCE_ROOT/multiply"
gh run download 38339549288 -R fuziran/ci-repair-demo \
  -n repair-result -D "$EVIDENCE_ROOT/add"
gh run download 38348122879 -R fuziran/ci-repair-demo \
  -n repair-result -D "$EVIDENCE_ROOT/multiply"
```

若 GitHub artifact 已到期，记录其不可用状态，并保留现有 PR diff、Actions 日志和本地验证记录；不要重建历史数据冒充原运行证据。

## 5. 最终验收定义

项目可以作为成熟的实习项目展示时，应同时具备：

- 至少一条可重复的真实 GitHub Actions 失败 → 独立修复验证 → Draft PR → PR 检查通过链路；现有两条案例已证明基本链路。
- 明确的安全边界：测试执行无 PR 写凭据，发布前复核来源和补丁范围，候选失败或不稳定时不发布；通过负例验证这些规则。
- 固定 20 例、统一判定与完整失败记录；给出带分母的修复率、回归情况、时间和费用。
- 可复现的版本/配置、架构说明、运行步骤、可打开的 PR 和 CI 证据；简历陈述与证据完全一致。

当前最有价值的投入是**可靠性与评估证据**。继续叠加框架、语言和多 Agent 会扩大范围，却不能补足上述关键证据。
