# 固定评估集 v1

本评估集在调用修复模型前冻结。它包含 20 个独立提交：每例都是单独 Git 仓库中的根提交，仅含该例故障代码，互不叠加。另有一个不交给代理的健康基线仓库。这样代理无法从父提交或其他案例分支抄回正确代码。案例是**受控合成故障**，不等同于 20 个真实项目 issue。既有五个成功的 GitHub 演示案例不计入这里的分母。

## 案例与输入

`cases.jsonl` 是冻结清单，包含完整 `bug_sha`、健康 `base_sha`、类别、预期失败节点、原始日志来源及预期复现结果。`build_cases.py` 从 `fixture/` 重建 `healthy/` 与 `E01/`–`E20/` 各自独立的 Git 仓库；重新生成的 JSONL 必须与冻结清单逐字节一致。给修复入口的 `--repo` 只能是某一例的目录，不能是包含全部案例的上级目录。`validation.json` 记录真实 Docker/pytest 对每例原始输入、两次基线的核对结果。原始日志是实际 pytest 输出；E17 将 E01 的真实失败日志与健康提交配对，E18 在原始运行设置 `EVAL_CI_FAULT=1`，以检验环境差异。E20 是真实的收集错误，无失败节点。

| 类别 | 案例 | 设计目的 |
| --- | --- | --- |
| 简单断言 | E01–E03 | 大小写、零数量、Unicode 大小写折叠 |
| 异常契约 | E04–E06 | 非法端口、缺失用户、零分母 |
| 边界条件 | E07–E09 | 最后一页、世纪闰年、相接区间 |
| 多文件定位 | E10–E12 | 报告/账户、发票/金额、权限调用链 |
| 回归风险 | E13–E16 | 上下界、稳定去重、金额舍入、路径前缀 |
| 无法复现 | E17–E18 | 过期失败日志、仅在原 CI 环境触发的失败 |
| 范围外 | E19–E20 | 测试本身的错误断言、测试依赖缺失造成收集错误 |

E01–E16 预期可复现且可以修改 `src/*.py` 修复；E17–E18 应拒绝继续修复；E19 虽可复现，但错误完全位于测试文件中，不能通过合法源码补丁修复；E20 应在输入/收集阶段拒绝。它们全部保留在 20 例分母中。每个案例只允许一次模型尝试，不能根据结果删例、换例或只重跑失败例。若发现案例设计缺陷，应记录原因，发布新的评估集版本并重新运行全部案例。

## 固定运行条件

- 环境编号：`eval-v1`。使用 `Dockerfile.repository` 的固定基础镜像引用、本地镜像内容 ID（见 `validation.json`），Python 3.12.15、pytest 9.0.2。批量运行时以该**镜像 ID**调用，不使用可变的 `latest` 标签。依赖由镜像内容固定；记录 Dockerfile、镜像 ID、Agent Git SHA、模型配置文件哈希及开始时间。
- 模型：`deepseek/deepseek-flash`；配置文件为 `eval/agent_config.yaml`，`step_limit=40`、每例模型估算费用上限 `cost_limit=0.25` 美元。模型服务端版本若无法由服务商提供，标为“未公开”，不能声称固定了服务端权重。
- 现有 `ci_repair` 入口参数：`--workflow Pytest`、案例完整 SHA、对应原始 `pytest.log` 与 `pytest.xml`、`--config eval/agent_config.yaml`、`--timeout 300`。入口的 300 秒分别约束单次测试和 Agent；外层为每例设置 900 秒墙钟上限，并保存外层超时状态。串行运行，不为每例发布 PR。原始输入与验证都使用同一镜像 ID。
- 每例最多一次模型尝试；连接失败、模型异常、超时、费用触顶与无候选均按失败保留。若基础设施中断使整批无法执行，应记录中断并重开**整批**，不能只重跑挑选出的案例。
- `model_cost_usd` 采用入口/模型接口估算值，`model_calls` 采用报告调用数；缺失记 `null`，已确认未调用记 `0`。账单金额单独记录，不能把估算值表述为实付费用。固定配置的名义模型预算上限为 20 × $0.25 = $5，单次最终调用可能使报告值略超上限。

## 判定与统计

`reproduced=true` 要求原始失败节点与故障 SHA 的两次基线失败节点和逐节点结果一致，且没有 pytest 收集错误。`candidate_submitted=true` 要求 Agent 明确提交候选。`valid_fix=true` 要求 `reproduced=true`、候选仅修改已跟踪的 `src/*.py`、目标测试与全量测试中原失败节点都实际通过、全量节点集合不变且无新增跳过/预期失败、全量测试通过。人工还要审阅补丁是否遵守测试意图，不得通过硬编码测试输入、屏蔽异常或改写测试执行机制伪造通过；不合格要留下具体原因。v1 没有额外隐藏测试，因此通过率只能描述这些受控公开测试下的表现。

`regression=true` 指候选修复原失败节点后，使任一基线已通过节点失败、跳过或消失；没有可评估候选时记 `null`，不能伪记为无回归。E17–E20 的 `valid_fix` 均应为 `false`；E19 的真实失败仍计入复现率。一次修复即使报告 `verified`，若人工合同审阅不合格，也要记录 `valid_fix=false` 与原因。

每例结果至少保存 `case_id`、`bug_sha`、`category`、`reproduced`、`candidate_submitted`、`valid_fix`、`regression`、`failure_reason`、`duration_seconds`、`model_cost_usd`、`model_calls`、`pr_url`。本地案例的 `pr_url=null`。主指标固定分母 20：复现率、有效修复率、回归案例占比；另列已复现案例中的修复率及其分母、各状态数量、模型估算成本总和、每个有效修复的平均模型估算成本、耗时中位数和最长耗时。未知费用单列，不能当 0 汇总。

## 证据目录与复建

`eval/evidence/` 已加入 Git 忽略，用于本地原始证据，避免把可能包含模型轨迹与敏感信息的文件直接提交。基线核对采用 `eval/evidence/<批次>/<case_id>/{original,baseline,baseline_repeat}.{log,xml}`。下一阶段的模型评估采用 `eval/evidence/<评估批次>/<case_id>/`，逐例保存原始输入、`runtime.json`、`report.json`、`trajectory.json`、`patch.diff`、目标/全量验证日志和 XML、`result.json`。即使失败、超时或未提交候选，也保存当时能够取得的文件。公开前审查日志与轨迹中的秘密。

在 WSL Bash 中复建并核对冻结 SHA（目标目录须不存在）：

```bash
cd "$HOME/projects/mini-swe-agent-portfolio"
.venv/bin/python eval/build_cases.py /tmp/ci-repair-eval-v1 /tmp/ci-repair-cases-rebuilt.jsonl
cmp eval/cases.jsonl /tmp/ci-repair-cases-rebuilt.jsonl
.venv/bin/python eval/validate_cases.py /tmp/ci-repair-eval-v1 eval/cases.jsonl eval/evidence/recheck eval/validation-recheck.json
```

上述命令只生成并验证案例，不调用修复模型。`eval/validation.json` 是本次冻结时的验证摘要，逐例原始日志/XML 保留在本地忽略目录中。后续批量评估须保留原始证据和带分母的汇总表，才能声称 P2 完成。
