# CI 修复评估复现说明

本页复建[固定评估集 v1](https://github.com/fuziran/mini-swe-agent/blob/1fde1e64732783ed8f01c31273c5dd179e56f9ae/eval/protocol.md)的 20 个独立故障提交，核对其原始 pytest 行为，并从已发布数据复算结果。以下复建、验证和复算**不调用模型，也不创建 PR**。案例是受控合成故障；17/20 可复现、16/20 有效修复只适用于该固定集合及其公开测试。

## 环境与版本

在 Ubuntu/WSL 中准备 Git、可运行 Linux 容器的 Docker、Python 3.10+；建议 Python 3.12。以下命令默认使用 `$HOME/projects/mini-swe-agent-portfolio`；若在新检出中复核，先在同一终端执行 `export PROJECT=/新检出的绝对路径`。首次安装依赖可运行：

```bash
PROJECT="${PROJECT:-$HOME/projects/mini-swe-agent-portfolio}"
cd "$PROJECT"
git rev-parse HEAD
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[full]'
.venv/bin/python -m pip check
docker info >/dev/null
```

本次已发布评估的 Agent 为 `1eb83dbfbc90188a63e211e6be83a8c0e5aa4b8a`，模型配置为 `deepseek/deepseek-flash`、每例最多 40 步、名义费用上限 $0.25、入口时限 300 秒、外层时限 900 秒。评估镜像内容 ID 是 `sha256:89cd1a451dfa7bac8d24aedd4f40351ec62bfdef7a6069124a428167dd104e91`，容器内 Python 3.12.15、pytest 9.0.2。当前项目代码版本可能较新，不能把当前测试结果或重跑结果冒充该历史批次。模型服务端权重版本未公开。具体版本与清单哈希见 `eval/results.json`；验证时使用的镜像见 `eval/validation.json`。

以下检查本机是否已有**相同内容 ID**的镜像，以及重建脚本所用的 `latest` 标签是否指向它：

```bash
PROJECT="${PROJECT:-$HOME/projects/mini-swe-agent-portfolio}"
cd "$PROJECT"
FROZEN_IMAGE=sha256:89cd1a451dfa7bac8d24aedd4f40351ec62bfdef7a6069124a428167dd104e91
docker image inspect "$FROZEN_IMAGE" --format '{{.Id}}'
test "$(docker image inspect mini-swe-agent-repository:latest --format '{{.Id}}')" = "$FROZEN_IMAGE"
docker run --rm --network none "$FROZEN_IMAGE" python --version
docker run --rm --network none "$FROZEN_IMAGE" python -m pytest --version
```

若镜像缺失，可在项目根目录运行 `docker build -t mini-swe-agent-repository:latest -f Dockerfile.repository .` 重建功能环境，然后再检查版本和内容 ID。新构建的 ID 若不同，只能称为**新环境中的功能复现**，不能声称与历史批次镜像逐字节相同。不要通过给不同镜像重贴标签来绕过 ID 检查。

## 从新目录复建并核对 20 例

下面在 `/tmp` 创建新目录，不覆盖冻结清单、原始证据或首次评估记录。`build_cases.py` 生成一个健康仓库和 E01–E20 各自的单提交 Git 仓库；`cmp` 要求新清单与 `eval/cases.jsonl` 逐字节一致。`validate_cases.py` 在固定镜像中对每例运行原始输入和两次基线，并核对故障 SHA、失败节点及预期分类。预计启动 60 个 pytest 容器；E17、E18 不可复现和 E20 收集失败是设计内结果，最终每例仍应显示 `ok`。

```bash
PROJECT="${PROJECT:-$HOME/projects/mini-swe-agent-portfolio}"
REPRO_DIR="$(mktemp -d /tmp/ci-repair-repro.XXXXXX)"
printf '复现目录：%s\n' "$REPRO_DIR"
cd "$REPRO_DIR"
"$PROJECT/.venv/bin/python" "$PROJECT/eval/build_cases.py" "$REPRO_DIR/repos" "$REPRO_DIR/cases.jsonl"
cmp "$PROJECT/eval/cases.jsonl" "$REPRO_DIR/cases.jsonl"
"$PROJECT/.venv/bin/python" "$PROJECT/eval/validate_cases.py" \
  "$REPRO_DIR/repos" "$REPRO_DIR/cases.jsonl" \
  "$REPRO_DIR/evidence" "$REPRO_DIR/validation.json"
```

在同一终端继续核对 `validation.json` 的镜像 ID、20 条 `matches_design=true` 以及每例的复现结论；若换了终端，先把 `REPRO_DIR` 设为上一步打印的实际路径。

```bash
"$PROJECT/.venv/bin/python" - "$REPRO_DIR/validation.json" "$PROJECT/eval/validation.json" <<'PY'
import json
import sys
from pathlib import Path

rebuilt, frozen = (json.loads(Path(path).read_text()) for path in sys.argv[1:])
assert rebuilt["image_id"] == frozen["image_id"]
assert len(rebuilt["cases"]) == len(frozen["cases"]) == 20
assert all(case["matches_design"] for case in rebuilt["cases"])
assert [(case["case_id"], case["bug_sha"], case["reproduced"]) for case in rebuilt["cases"]] == [
    (case["case_id"], case["bug_sha"], case["reproduced"]) for case in frozen["cases"]
]
print("20 cases and frozen image verified")
PY
```

新目录的 `evidence/E01/` 至 `E20/` 含原始、首次基线、重复基线的日志和 XML。保留上面输出的 `REPRO_DIR`，不要把这些原始日志直接提交或公开。

## 关键测试与文档检查

在项目目录运行修复入口、发布规则和受限仓库环境的关键测试；Docker 必须可用。需要全项目门禁时再运行第二条 pytest 命令，其耗时和跳过数依环境而变。

```bash
PROJECT="${PROJECT:-$HOME/projects/mini-swe-agent-portfolio}"
cd "$PROJECT"
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q \
  tests/run/test_ci_repair.py tests/run/test_ci_publish.py tests/environments/test_repository.py
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/python -m mkdocs build --strict --site-dir /tmp/mini-swe-agent-portfolio-mkdocs-check
```

完整测试的最近一次本地门禁记录为 623 通过、52 跳过；它是当时的记录，不是新环境的预期固定数。若 Docker、网络或可选依赖导致某项检查无法运行，应分别记录命令、环境和实际失败，不把它记成通过。

## 从冻结数据独立复算

以下命令只读取仓库跟踪的 `eval/cases.jsonl` 与 `eval/results.json`，可在上述新目录执行。它先核对 20 个案例 ID、故障 SHA 和清单哈希，再从逐例数据重算状态、复现、有效修复、回归、耗时与模型接口估算费用，并与已发布摘要逐字段比较。它无法单凭汇总文件重新审阅补丁语义。

```bash
PROJECT="${PROJECT:-$HOME/projects/mini-swe-agent-portfolio}"
"$PROJECT/.venv/bin/python" - "$PROJECT/eval/cases.jsonl" "$PROJECT/eval/results.json" <<'PY'
import hashlib
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

manifest_path, results_path = map(Path, sys.argv[1:])
manifest = [json.loads(line) for line in manifest_path.read_text().splitlines()]
published = json.loads(results_path.read_text())
rows = published["cases"]
assert len(manifest) == len(rows) == 20
assert len({case["bug_sha"] for case in manifest}) == 20
assert [(case["case_id"], case["bug_sha"]) for case in manifest] == [
    (row["case_id"], row["bug_sha"]) for row in rows
]
assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == published["manifest_sha256"]
costs = [row["model_cost_usd"] for row in rows if row["model_cost_usd"] is not None]
durations = [row["duration_seconds"] for row in rows]
fixed = sum(row["valid_fix"] is True for row in rows)
reproduced = sum(row["reproduced"] is True for row in rows)
regressions = sum(row["regression"] is True for row in rows)
summary = {
    "denominator": 20,
    "completed": len(rows),
    "reproduced": reproduced,
    "valid_fix": fixed,
    "pending_review": sum(row["valid_fix"] is None for row in rows),
    "regression": regressions,
    "regression_unassessed": sum(row["regression"] is None for row in rows),
    "statuses": dict(sorted(Counter(row["status"] for row in rows).items())),
    "model_cost_estimate_usd": round(sum(costs), 9),
    "model_cost_unknown_cases": len(rows) - len(costs),
    "cost_per_valid_fix_usd": round(sum(costs) / fixed, 9) if fixed and len(costs) == len(rows) else None,
    "median_duration_seconds": statistics.median(durations),
    "max_duration_seconds": max(durations),
    "reproduction_rate": reproduced / 20,
    "valid_fix_rate": fixed / 20,
    "regression_rate": regressions / 20,
    "valid_fix_given_reproduced": fixed / reproduced if reproduced else None,
}
assert summary == published["summary"], "Published summary differs from per-case data"
print(json.dumps(summary, ensure_ascii=False, indent=2))
PY
```

若持有本机**未提交**的 `eval/evidence/model-v1-retry3/` 原始证据，还可复制到新的目录，使用原汇总程序从 20 份 `result.json` 复算；人工 `review.json` 的补丁及报告哈希需要另行审阅。`summarize` 会写入目标目录的 `summary.json`，因此只能对副本运行。随后比较副本的汇总与公开结果：

```bash
PROJECT="${PROJECT:-$HOME/projects/mini-swe-agent-portfolio}"
cp -a "$PROJECT/eval/evidence/model-v1-retry3" "$REPRO_DIR/model-v1-retry3"
"$PROJECT/.venv/bin/python" "$PROJECT/eval/run_batch.py" summarize "$REPRO_DIR/model-v1-retry3"
"$PROJECT/.venv/bin/python" - "$REPRO_DIR/model-v1-retry3/summary.json" "$PROJECT/eval/results.json" <<'PY'
import json
import sys
from pathlib import Path

summary, published = (json.loads(Path(path).read_text()) for path in sys.argv[1:])
assert summary == published["summary"]
print("Local raw-result summary matches published results")
PY
```

## 已知限制

- 冻结 20 例是单一受控代码基线上的合成故障，没有隐藏测试或跨项目样本；有效修复还包含人工审阅，不能把 16/20 推广为真实 issue 修复率。
- 公开仓库只跟踪冻结清单、验证摘要和逐例评估结果；原始日志、XML、轨迹、补丁、人工审阅记录位于被 Git 忽略的 `eval/evidence/`。没有这些文件时，只能复核公开数据内部一致性和重新生成的案例，不能独立审计历史模型执行全过程。
- 模型接口费用是估算值，不是账单金额；历史批次 Agent SHA、镜像 ID、模型与时限已记录。重新调用模型属于**新批次**，不能覆盖原 20 例结果；当前操作不需要密钥。
- [线上架构与 Draft PR 证据](ci-repair-architecture.md)是另一个演示链路，示例仓库为私有；访问受权限控制，E01 衍生线上故障不计入冻结的 20 例。

## 本说明的本地实测

2026-10-16 在 Ubuntu 24.04/WSL、项目 HEAD `1fde1e64732783ed8f01c31273c5dd179e56f9ae` 上，从新的 `/tmp/ci-repair-repro-15-2-20261016/` 目录执行了上述重建与核对：20 例清单逐字节一致，20 例原始输入与双基线均符合设计，镜像 ID 与冻结记录一致；逐例公开数据和本机原始批次副本的两种汇总都与 `eval/results.json` 一致。当前虚拟环境的关键测试为 50 通过、1 跳过；Ruff 与 MkDocs 严格构建通过。本次未重新调用模型，也未重新运行完整项目测试。临时目录的原始证据不在 Git 中。

## 16.1 独立检出与新虚拟环境复核

2026-10-16 又从本机项目执行 `git clone --no-local`，在 `/tmp/ci-repair-16-1-20261016/source/` 得到干净的 `1fde1e64732783ed8f01c31273c5dd179e56f9ae` 检出；在其中新建 `.venv` 并安装 `.[full]`。Python 3.12.3、pytest 9.1.1，`pip check` 通过；关键测试为 **50 通过、1 跳过**（39.10 秒），Ruff 检查通过。Ruff 0.17.1 对原有 `tests/conftest.py` 的四处 `# noqa` 给出语法警告，但退出码仍为 0。

用这份新安装的环境从独立目录重建 `healthy` 与 E01–E20，生成清单与冻结 `eval/cases.jsonl` 逐字节一致；在固定 Docker 镜像中完成 20 例原始输入及双基线验证，**20/20 符合设计**，镜像 ID 和各例复现结论均与 `eval/validation.json` 一致。从跟踪的逐例 `eval/results.json` 重新计算，得到 17/20 可复现、16/20 有效修复、4 例回归不可评估、模型接口估算费用合计 $0.0544569，与发布摘要一致。将尚未提交的本页和其他两份 `docs/` 页面复制到临时检出后，使用新虚拟环境的 MkDocs 严格构建也通过。精简输出保留在本机 Git 忽略的 `eval/evidence/repro-16-1-20261016/`。

**可复现范围：**这是同一台 WSL 主机上的新 Git 检出与新 Python 环境，Docker 使用本机已有且 ID 相同的固定镜像；未在另一台机器从零重新构建出同一镜像 ID。新检出不含被 Git 忽略的历史模型轨迹和人工审阅原件，也不含当时尚未提交的文档；因此这里只复核代码、冻结案例、公开结果的计算及本机固定镜像下的测试，未重新调用模型、审计历史原始轨迹或重跑完整项目测试。没有出现命令失败或设计外的不可复现案例。
