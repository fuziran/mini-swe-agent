# CI 修复案例记录

更新日期：2026-10-13。本页索引三个受控演示案例的原始运行、修复产物和 PR。原始日志、报告、补丁与 trajectory 保存在个人目录 `$HOME/ci-repair-evidence/`，未提交到本仓库；公开轨迹和日志前需检查敏感信息。三个案例都来自同一个小型示例仓库，不构成通用修复率评估。

## 结果与证据位置

| 案例 | 故障提交与原始 CI | 修复运行 | 本地证据目录 | PR 与检查 | 归档报告 |
| --- | --- | --- | --- | --- | --- |
| 加法 | `05520d4d4f2476b41fc2f7edecb895227a3afe84`；[Pytest](https://github.com/fuziran/ci-repair-demo/actions/runs/38339529484) | [运行 38339549288](https://github.com/fuziran/ci-repair-demo/actions/runs/38339549288)，第 2 次尝试成功 | `add/original-ci/`、`add/repair/`、`add/pr.json`、`add/pr.diff` | [PR #1](https://github.com/fuziran/ci-repair-demo/pull/1) 已合并；归档的 `pr.json` 显示 `test` 检查成功 | `verified`；候选全量 `2 passed`；5 次模型调用；估算模型成本 $0.000828588；入口耗时 11.52 秒 |
| 乘法 | `9052dc1d4b290b508e089b16fca02ef46c4d6a22`；[Pytest](https://github.com/fuziran/ci-repair-demo/actions/runs/38348094027) | [运行 38348122879](https://github.com/fuziran/ci-repair-demo/actions/runs/38348122879)，第 2 次尝试成功 | `multiply/original-ci/`、`multiply/repair/`、`multiply/pr.json`、`multiply/pr.diff` | [PR #2](https://github.com/fuziran/ci-repair-demo/pull/2) 已合并；归档的 `pr.json` 显示 `test` 检查成功 | `verified`；补丁后目标 `1 passed`、全量 `3 passed`；5 次模型调用；估算模型成本 $0.00046395；入口耗时 12.01 秒 |
| 字符串空白 | `6cf07e8d4adcfd34f9dcadd61ea97dd52e8f12a8`；[Pytest](https://github.com/fuziran/ci-repair-demo/actions/runs/38375617864) | [运行 38375646666](https://github.com/fuziran/ci-repair-demo/actions/runs/38375646666)，第 1 次尝试成功 | `slug-6cf07e8/original-ci/`、`slug-6cf07e8/`、`slug-6cf07e8/pr.json` | [PR #3](https://github.com/fuziran/ci-repair-demo/pull/3) 已合并；[PR 检查](https://github.com/fuziran/ci-repair-demo/actions/runs/38375927602)与[合并后 main 检查](https://github.com/fuziran/ci-repair-demo/actions/runs/38378797950)成功 | `verified`；两次基线失败节点相同；补丁后目标 `1 passed`、全量 `5 passed`；6 次模型调用；估算模型成本 $0.001198908；入口耗时 15.43 秒 |

本地证据目录均相对于 `$HOME/ci-repair-evidence/`。第三例现已保存原始 `pytest.log`、`pytest.xml`、修复 `report.json`、`patch.diff`、`trajectory.json`、两次基线日志、目标与全量验证日志以及 `pr.json`。加法案例的早期原始 CI artifact 只有 `pytest.log`，修复 artifact 缺少 `target.log`；不能将后续案例的文件补写成早期证据。乘法案例同一运行的不同尝试有不同调用数与费用，上表只采用已归档的成功尝试，不能把重跑计为独立案例。

## 运行环境与可复现性

| 字段 | 三例可核对的记录 | 来源与限制 |
| --- | --- | --- |
| Agent 软件版本 | `2.4.6` | 三份归档 `trajectory.json` 的 `info.mini_version`。软件版本不能确定 Git 提交。 |
| Agent Git 提交 | 历史运行未记录 | 当前 `ci-repair-reliability-8f20dbb` 标签解析为 `8f20dbb3ed33cf7fd30cfaad349881d9fb7a4ab9`，但历史运行实际检出的提交未随 artifact 保存，不能据此回填。 |
| 模型 | `deepseek/deepseek-flash` | 三份 trajectory 的 `info.config.model.model_name` 与修复工作流的 `--model` 一致；服务端具体模型版本未记录。 |
| Python | 工作流选择 `3.12` | 原始 Pytest 与修复作业均使用 `actions/setup-python` 的 `python-version: '3.12'`；实际补丁版本未归档。 |
| pytest | 工作流及修复镜像构建声明 `9.0.2` | 原始 CI 安装 `pytest==9.0.2`；`Dockerfile.repository` 也固定该版本。artifact 未单独记录 `pytest --version`。 |
| 基础镜像 | `python:3.12-slim` | `Dockerfile.repository` 使用可变标签；历史构建的镜像摘要未归档。 |
| 测试与 Agent 镜像 | `mini-swe-agent-repository:latest` | 三份 trajectory 配置使用本地镜像名；历史镜像 ID 和摘要未归档。 |
| Agent 预算 | `step_limit: 40`、`cost_limit: 3`、`wall_time_limit_seconds: 600` | 三份 trajectory 的 `info.config.agent`。入口还将每次测试运行的 `timeout` 设为 600 秒。 |

模型成本是单次成功尝试的接口估算值，不包含 GitHub Runner、Docker 构建、等待时间或同一案例的失败尝试。`duration_seconds` 只覆盖修复入口，不是完整 CI 墙钟时间。后续运行应在 artifact 中保存 Agent 检出 SHA、Python 与 pytest 实际版本、基础及构建镜像摘要、模型服务端版本和完整配置；历史缺口保持“未记录”。
