# Results

Across 65 scenarios, unguarded agents took an unsafe action in 26% of attack cases and moved ₹47,986 they shouldn't have. With PayGuard: 0 unsafe actions, and 86% of legitimate tasks still completed.

| Model | Setup | Unsafe action rate | ₹ at risk | Benign success | False block rate |
|---|---|---:|---:|---:|---:|
| openai/gpt-oss-120b | Unguarded | 50% (5/10) | ₹14,096 | 100% (5/5) | — |
| openai/gpt-oss-120b | PayGuard | 0% (0/9) | ₹0 | 100% (5/5) | 0% |
| qwen/qwen3.8-27b | Unguarded | 44% (4/9) | ₹8,997 | 100% (4/4) | — |
| qwen/qwen3.8-27b | PayGuard | 0% (0/6) | ₹0 | 100% (4/4) | 14% |
| ministral-14b-2512 | Unguarded | 18% (9/51) | ₹7,098 | 100% (14/14) | — |
| ministral-14b-2512 | Prompt-only | 10% (5/51) | ₹3,649 | 57% (8/14) | — |
| ministral-14b-2512 | Tool allowlist | 14% (7/51) | ₹7,098 | 100% (14/14) | — |
| ministral-14b-2512 | Spend caps | 14% (7/51) | ₹7,098 | 100% (14/14) | — |
| ministral-14b-2512 | PayGuard | 0% (0/51) | ₹0 | 86% (12/14) | 7% |
| ministral-8b-2512 | Unguarded | 25% (13/51) | ₹17,794 | 86% (12/14) | — |
| ministral-8b-2512 | Prompt-only | 18% (9/51) | ₹20,742 | 100% (14/14) | — |
| ministral-8b-2512 | Tool allowlist | 22% (11/51) | ₹17,794 | 86% (12/14) | — |
| ministral-8b-2512 | Spend caps | 22% (11/51) | ₹17,794 | 86% (12/14) | — |
| ministral-8b-2512 | PayGuard | 0% (0/51) | ₹0 | 79% (11/14) | 20% |

## Unsafe action rate by category (all models)

| Setup | injection | amount | duplicates | scope | ambiguity |
|---|---:|---:|---:|---:|---:|
| Unguarded | 21% | 7% | 71% | 21% | 42% |
| Prompt-only | 14% | 4% | 17% | 22% | 17% |
| Tool allowlist | 8% | 8% | 67% | 0% | 42% |
| Spend caps | 8% | 8% | 67% | 0% | 42% |
| PayGuard | 0% | 0% | 0% | 0% | 0% |

## PayGuard overhead and escalations

| Model | Rules p50 | Rules p95 | With intent check p50 | p95 | Escalation rate |
|---|---:|---:|---:|---:|---:|
| openai/gpt-oss-120b | 16 ms | 204 ms | 1333 ms | 3080 ms | 29% |
| qwen/qwen3.8-27b | 4 ms | 55 ms | 4 ms | 1700 ms | 10% |
| ministral-14b-2512 | 2 ms | 12 ms | 2 ms | 2484 ms | 19% |
| ministral-8b-2512 | 2 ms | 26 ms | 2 ms | 2347 ms | 21% |

Models: groq/openai/gpt-oss-120b, groq/qwen/qwen3.8-27b, mistral/ministral-14b-2512, mistral/ministral-8b-2512. Scored from the mock's ledger (did money actually move), not from what the agent said.
