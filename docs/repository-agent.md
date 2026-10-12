# Repository agent prototype

Build the default isolated runner image before the first run:

```bash
docker build -f Dockerfile.repository -t mini-swe-agent-repository:latest .
```

The image contains this project's dependencies and pytest. To test another repository, build an image that also contains that repository's test dependencies and set `environment.image` to its tag. The runner has no network access, a read-only container filesystem, CPU and memory limits, and a writable mount of the selected repository.

The build keeps a pip download cache across retries and installs dependencies before copying source code. Source changes reuse the dependency layer. If a package host times out during the first build, rerun the same command; downloaded packages can be reused.

Run the repository agent from the repository you want it to work on. Set a default model with `MSWEA_MODEL_NAME` or pass `-m`:

```bash
mini -c repository.yaml -m "provider/model" -t "Fix the failing parser test" -o run.traj.json
```

The agent prompts before write, shell, test, and finish actions. `search_code`, `read_file`, and `list_symbols` do not require confirmation. The `--yolo` option disables confirmation. Use `environment.runner=local` only for a repository you trust, since local shell commands are not isolated.

In Docker mode, shell commands run in `/workspace`; structured file tools use paths relative to the repository. `apply_patch` accepts a standard unified diff with `--- a/path`, `+++ b/path`, and `@@` hunk headers.

Compare paired runs by trajectory instance ID (or by trajectory filename if no ID is present):

```bash
python -m minisweagent.run.benchmarks.compare baseline_runs candidate_runs
```

The report includes submission rate, model cost, calls, token usage, tool errors, model and tool time, and observed event duration. Submission is not a correctness result. To report resolved rates, pass two JSON files mapping instance IDs to boolean judgments with `--baseline-grades` and `--candidate-grades`. These judgments should come from the same external evaluator for both runs.
