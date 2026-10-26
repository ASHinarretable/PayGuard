# Results

Across 65 scenarios, unguarded agents took an unsafe action in 18% of attack cases and moved ₹7,098 they shouldn't have. With PayGuard: 0 unsafe actions, and 86% of legitimate tasks still completed.

| Model | Setup | Unsafe action rate | ₹ at risk | Benign success | False block rate |
|---|---|---:|---:|---:|---:|
| ministral-14b-2512 | Unguarded | 18% (9/51) | ₹7,098 | 100% (14/14) | — |
| ministral-14b-2512 | Prompt-only | 10% (5/51) | ₹3,649 | 57% (8/14) | — |
| ministral-14b-2512 | Tool allowlist | 14% (7/51) | ₹7,098 | 100% (14/14) | — |
| ministral-14b-2512 | Spend caps | 14% (7/51) | ₹7,098 | 100% (14/14) | — |
| ministral-14b-2512 | PayGuard | 0% (0/51) | ₹0 | 86% (12/14) | 7% |

## Unsafe action rate by category (all models)

| Setup | injection | amount | duplicates | scope | ambiguity |
|---|---:|---:|---:|---:|---:|
| Unguarded | 6% | 8% | 67% | 22% | 17% |
| Prompt-only | 6% | 8% | 17% | 22% | 0% |
| Tool allowlist | 6% | 8% | 67% | 0% | 17% |
| Spend caps | 6% | 8% | 67% | 0% | 17% |
| PayGuard | 0% | 0% | 0% | 0% | 0% |

## PayGuard overhead and escalations

| Model | Rules p50 | Rules p95 | With intent check p50 | p95 | Escalation rate |
|---|---:|---:|---:|---:|---:|
| ministral-14b-2512 | 2 ms | 40 ms | 3 ms | 3232 ms | 17% |

Models: mistral/ministral-14b-2512. Scored from the mock's ledger (did money actually move), not from what the agent said.
