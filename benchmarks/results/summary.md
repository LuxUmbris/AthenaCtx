# Results summary (one run per arm)

Full Ollama prompts/responses and token telemetry are in `local-spark-96k.json`; Hermes subagent measurements are in `subagents.json`.

## Local `spark-96k:latest`

| Task | Context | Without AthenaCtx | With AthenaCtx | Change |
|---|---:|---:|---:|---:|
| Understand codebase | Prompt tokens | 31,467 | 11,223 | **64.33% fewer** |
| Understand codebase | Context characters | 120,545 | 38,056 | **68.43% fewer** |
| Understand codebase | Wall time | 113.697 s | 49.919 s | 56.09% lower |
| LRU bug fix (primary) | Prompt tokens | 483 | 1,098 | **127.33% more** |
| LRU bug fix (primary) | Context characters | 1,342 | 3,206 | **138.90% more** |
| LRU bug fix (primary) | Wall time | 4.872 s | 6.050 s | 24.18% higher |

On understanding, both arms produced a grounded subsystem map. The AthenaCtx answer misattributed the CLI entry file and made some claims beyond the supplied index; the baseline also made unsupported implementation claims. Treat the large token reduction as a context-compression result, not a quality win. On the LRU defect, both model arms identified that a cached `False` did not refresh `OrderedDict` recency. The AthenaCtx patch was minimal; the baseline patch was correct but retained a redundant recency update. Both passed the regression in an isolated copy. AthenaCtx context was larger because the fixture is small and the harness supplies the source alongside scan metadata. The original simpler falsy-cache pilot is retained in the JSON under `bugfix_pilot`.

## Hermes subagents

| Task | Arm | Elapsed | Tool calls | Logged tool-result characters* | Outcome |
|---|---|---:|---:|---:|---|
| Understand codebase | With AthenaCtx | 84.95 s | 31 | 10,824 | Grounded architecture summary; Python scan/index gaps caused direct-source fallback and extra calls |
| Understand codebase | Without AthenaCtx | 82.96 s | 14 | 5,595 | Similarly grounded summary by direct inspection |
| LRU bug fix | With AthenaCtx | 43.86 s | 14 | 5,317 | Correct root cause, patch, and verification |
| LRU bug fix | Without AthenaCtx | 30.99 s | 10 | 4,170 | Correct root cause, patch, and verification |

Elapsed times and tool-call counts above come from the delegation transcripts. The workers' narrative effort estimates did not agree with those measurements (for codebase understanding: arm A estimated 5–7 minutes/~22 calls, arm B 10–15 minutes/~11 invocations), so the estimates are excluded from the comparison.

Both subagents traced the false cache hit through the capacity-2 LRU sequence and found the bug. AthenaCtx added 12.87 seconds and 4 tool calls in this single trial; it did not reduce effort. The earlier, easier `bug-cache` pilot is also recorded in `subagents.json`.

\* Transcript tool-result characters are a lower-bound proxy, not exact context/token counts; some long outputs were truncated. Hermes does not expose per-subagent prompt-token totals.

## Takeaway

For broad codebase understanding, the local structural-index arm reduced measured prompt tokens by about 64%. For a small, precisely seeded LRU defect, index metadata increased context and effort in both local and subagent trials. The local model was faster on the broad indexed prompt, but the subagent arms showed no corresponding speedup. This is a single exploratory run on a controlled fixture, not a statistically powered result; the LRU scenario is harder than the retained pilot but is not a production-scale defect. See `../README.md` for methodology and limitations.
