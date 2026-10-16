# AthenaCtx context benchmark

This benchmark compares paired `with AthenaCtx` and `without AthenaCtx` approaches for (1) codebase understanding without documentation/comments and (2) diagnosing a seeded LRU-cache bug. It runs on the AthenaCtx repository and a multi-file bug fixture. Results and raw model responses live in `benchmarks/results/`. A simpler falsy-cache pilot is retained separately as `bug-cache`; the primary bug-fix trial is `bug-lru`.

## Arms

For each task there are four observations: a Hermes subagent with AthenaCtx, a Hermes subagent without AthenaCtx, local Ollama `spark-96k:latest` with AthenaCtx, and the same Ollama model without AthenaCtx. Each pair receives the same question and repository/fixture. Subagents return a concise report or proposed unified diff without changing files. The Ollama harness calls the local generation API at temperature 0 with thinking disabled, and records server-reported prompt/evaluation token counts and wall time.

The no-AthenaCtx arms must not invoke AthenaCtx or inspect its cache/index. The AthenaCtx arms scan/index first, then use those results to navigate. Documentation and source comments are excluded from the understanding prompt. Fixture code has neither comments nor docstrings.

## Tasks and scoring

Understanding: explain the CLI-to-analysis flow, major subsystem roles, and strongest implementation limitations. Score each report from 0–2 for each of those three dimensions (0 incorrect/missing, 1 partial, 2 correct with source evidence), plus 0–2 for uncertainty discipline; maximum 8. Match claims against source and tests after trials.

Bug fix: in `benchmarks/fixtures/bug-lru`, a capacity-2 LRU cache processes A=false, B=true, A, C=true, A. Every cache hit should refresh recency regardless of the cached value, so the reader should run exactly 3 times. Score root-cause identification, minimality/correctness of patch, and a valid verification command, each 0–2; maximum 6. The earlier `bug-cache` false-miss case is retained as a simpler pilot only.

## Context metric and limitations

The primary context measure is `prompt_eval_count` from Ollama for the local-model trials. `context_chars` is also recorded as a reproducible payload-size proxy. Report percentage reduction as `100 * (without_tokens - with_tokens) / without_tokens`; do not equate prompt tokens with the model's full 96k advertised capacity. For subagents, Hermes does not expose exact token totals. The saved results use elapsed time and tool-call counts from transcript instrumentation; logged tool-result characters are a lower-bound proxy because oversized outputs are truncated in the transcript. These proxies are not prompt-token measurements. No token savings are claimed for subagents.

This is a small, single-repetition exploratory benchmark, not a statistically powered result. The seeded LRU defect is more involved than the retained smoke-test pilot but remains a small controlled fixture, not a production-scale bug. Prompt ordering, model warm-up, hardware load, and AthenaCtx's support for particular files can affect outcomes. The local harness's AthenaCtx context is the compact structural index plus the CLI launcher and Shaft entrypoint source for understanding, and the structural index plus fixture source for bug fixes; it is not a tool-using agent that can adaptively request implementations. Interpret that arm as a context-compression test, not as a perfect simulation of an interactive AthenaCtx-enabled coding agent. Report unsupported files or failed model calls rather than treating them as successful trials.

For both bug fixtures, their regression tests are intentionally red before the fix. Proposed patches are checked in temporary copies, leaving fixtures buggy for repeatability.

## Reproduction

From the repository root, build `build/athenactx` and ensure Ollama is serving `spark-96k:latest`, then run:

```sh
python3 benchmarks/run_local.py
```

The harness invokes the compiled binary directly and writes raw responses and token/time telemetry to `benchmarks/results/local-spark-96k-rerun.json` (override with `--output`). For a large external GitHub codebase, index the checked-out source tree directly, for example:

```sh
time ./build/athenactx index /path/to/transformers/src/transformers
time ./build/athenactx index /path/to/transformers/src/transformers # warm-cache repeat
```

Record cold and warm timings separately, along with file/symbol counts and cache hit/miss counts; this throughput run is not an LLM-quality comparison. For manual agent repetitions, use the same prompts and record reports under `benchmarks/results/` without changing fixtures between arms. Run `python3 -m unittest test_cache.py` from `benchmarks/fixtures/bug-lru/`; it should fail before the fix. The simpler pilot test is in `benchmarks/fixtures/bug-cache/`.

## Recorded rerun (2026-10-16)

One paired local-model repetition on the AthenaCtx repository measured understanding at 34,724 tokens / 146.919 s without AthenaCtx versus 14,282 tokens / 90.480 s with it (58.9% fewer prompt tokens and 38.4% less wall time). The small bug-cache pilot rose from 392 to 901 prompt tokens while wall time fell from 8.094 to 6.793 s. The LRU fixture rose from 483 to 1,098 tokens and from 5.156 to 6.463 s. These are single observations; answer quality was not independently scored in this rerun, so they are exploratory rather than general performance claims. Raw data: `local-spark-96k-rerun.json`.

The GitHub corpus was `huggingface/transformers`, `src/transformers` (3,097 tracked `.py` files; the workspace index discovered 3,099 supported files). Direct native CLI indexing took 292.094 s cold (3,099 misses) and 56.702 s warm (3,099 hits), producing about 142 MB of JSON and 1,021 error-severity diagnostics. A separate per-file scan pass completed 3,097 attempts in 83.191 s: 2,056 returned Python results, 1,009 returned `unknown`, and 32 produced invalid/empty JSON. Treat indexing time as a throughput measure with substantial parser failures, not as successful coverage of all files. Results: `transformers-index-direct-rerun.json` and `transformers-scan-rerun.json`.
