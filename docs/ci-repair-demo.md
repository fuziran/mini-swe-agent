# CI 修复演示与简历表述

本演示使用私有示例仓库 `fuziran/ci-repair-demo` 的一次 **E01 衍生线上故障**：故障提交 `e4b1fa2b75a31bbe75c57f65136a7dfecb4c614d`。它用于展示真实 GitHub Actions → Draft PR 链路，**不属于固定评估的 20 例**。以下链接需要该仓库的访问权限；本机原始证据位于被 Git 忽略的 `eval/evidence/`，不直接公开轨迹或日志。

截至 2026-10-16 的只读复查：原 Pytest 运行仍为失败，后续修复运行成功且 `repair-result` artifact 未过期，PR #6 仍开放并标为 Draft，PR 的 `test` 检查通过。现场演示前应重新查看链接状态；下面的本地归档可作为快照备用。

## 演示前打开的材料

| 画面 | 地址或本机文件 | 核对点 |
| --- | --- | --- |
| 失败 CI | [Pytest 运行 38862204427](https://github.com/fuziran/ci-repair-demo/actions/runs/38862204427)；`eval/evidence/online-14-3/pytest-run-38862204427/pytest.xml` | 故障 SHA；5 个节点中 2 failed、3 passed |
| 修复证据 | [修复运行 38863447981](https://github.com/fuziran/ci-repair-demo/actions/runs/38863447981) 的 `repair-result`；`eval/evidence/online-14-5/repair-run-38863447981/report.json` | 两次基线均 2 failed；定向 2 passed；全量 5 passed；`status=verified` |
| 补丁与 Draft PR | [PR #6 的 Files changed](https://github.com/fuziran/ci-repair-demo/pull/6/files)；`eval/evidence/online-14-5/repair-run-38863447981/patch.diff` | 仅恢复 `src/slug.py` 中的 `lower()`；补丁与 PR diff 字节一致 |
| PR 检查 | [PR Pytest 运行 38862433446](https://github.com/fuziran/ci-repair-demo/actions/runs/38862433446) | 维护者批准工作流后，5 passed；PR 仍保持 Draft |
| 拒绝案例 | 仓库 `eval/results.md` 的 E17 行；本机 `eval/evidence/model-v1-retry3/E17/report.json` | `not_reproduced`，基线无失败节点，模型调用 0 次 |
| 固定评估表 | 仓库 `eval/results.md` 和 `eval/results.json` | 固定分母 20；17/20 可复现，16/20 有效修复 |

原始创建 PR 的[修复运行 38862229869 第一次尝试](https://github.com/fuziran/ci-repair-demo/actions/runs/38862229869/attempts/1)有成功发布的日志，但其最初 artifact ID 现已无法读取。上述 `38863447981` 是**同一故障 SHA 的后续运行**，保存完整报告、四份 XML 和补丁；该补丁与 PR #6 的实际 diff 字节一致。演示时不能把后续运行的模型费用或耗时说成首次创建 PR 的数据。证据核对细节见本机 `eval/evidence/online-14-5/summary.md`。

## 2 分 35 秒演示顺序

| 时间 | 操作 | 讲述要点 |
| --- | --- | --- |
| 0:00–0:20 | 打开失败 CI 的 `test` 作业和原始 pytest 摘要 | `main` 上的故障 SHA 触发失败；`slugify` 两个测试失败，其他三个通过。 |
| 0:20–0:55 | 打开完整修复运行的 `repair-result/report.json`，指向两次基线、定向及全量结果 | Agent 只在独立检出中改动；验证器要求同一失败被重复复现，原失败节点实际通过，完整节点集合不消失或新增跳过。该次报告为 `verified`。 |
| 0:55–1:20 | 打开 PR #6 的 Files changed | 补丁只修改 `src/slug.py` 一行，把小写转换恢复；发布作业创建 Draft PR，未自动合并。 |
| 1:20–1:45 | 打开 PR 的 `test` 检查及运行记录 | 工作流一度需要维护者批准；批准后 pytest 为 5 passed。批准运行工作流不等于完成 PR review，PR 仍是 Draft。 |
| 1:45–2:10 | 展示 E17 的公开结果行；本机可同时展示 `report.json` 的状态 | 这例原始日志有失败，但固定 SHA 的首次基线全通过，因此返回 `not_reproduced`，在调用模型前停止。E17 是**本地固定评估案例**，不是线上 PR #6 的另一次运行。 |
| 2:10–2:35 | 展示固定评估表 | 预先冻结的 20 个独立受控合成故障中，17/20 可复现、16/20 经节点级验证及人工审阅为有效修复。16 个有可评估候选的案例未观察到回归；另 4 例无候选，不能计为“无回归”。没有隐藏测试或跨项目样本。 |

若现场无法访问私有 GitHub 仓库，使用上述本机归档的 XML、报告、补丁、PR 快照和检查记录演示，并明确它们是 2026-10-16 的快照。E17 的公开逐例结果可以展示；其本机原始报告不可用时，只陈述 `eval/results.json` 中可核对的字段。不要为了演示重新触发工作流或调用模型。

## 可用于简历的一句表述

> 实现基于 GitHub Actions 的 pytest 故障修复链路，包含隔离的 Agent 工作区、两次故障复现、节点级候选验证和待人工审阅的 Draft PR；在预先冻结的 20 个独立**受控合成故障**中，17/20 可复现、16/20 经验证与人工审阅为有效修复（公开测试，无隐藏测试或跨项目泛化结论）。

这句中的两个比率都来自[固定评估结果](https://github.com/fuziran/mini-swe-agent/blob/1fde1e64732783ed8f01c31273c5dd179e56f9ae/eval/results.md)，不是五个历史线上演示或本次 PR #6 的成功次数。模型接口估算费用若另行引用，应写明最终批次合计 `$0.0544569`，不等于实际账单金额；不要将单次入口耗时写成完整 CI 耗时。

## 展示边界

- PR #6 的工作流已由维护者批准并通过，但尚无正式 PR review，且未合并；演示不能说成“自动合并”或“人工已审核合并”。
- E17 证明不可复现输入被本地评估入口拒绝。线上发布作业只在修复作业成功时运行；本演示没有拿 E17 在线上触发拒绝工作流，因此不能声称做过该项线上实测。
- 私有运行链接及本机原始证据可能受访问权限、保留期影响。公开材料只使用已核对且无敏感内容的摘要；分享原始日志或轨迹前需要另行检查。
