# Results

Across 52 scenarios, unguarded agents took an unsafe action in 22% of attack cases and moved ₹7,098 they shouldn't have. With PayGuard: 0 unsafe actions, and 92% of legitimate tasks still completed.

| Model | Setup | Unsafe action rate | ₹ at risk | Benign success | False block rate |
|---|---|---:|---:|---:|---:|
| ministral-14b-2512 | Unguarded | 22% (9/40) | ₹7,098 | 100% (12/12) | — |
| ministral-14b-2512 | Prompt-only | 10% (4/40) | ₹3,399 | 58% (7/12) | — |
| ministral-14b-2512 | PayGuard | 0% (0/40) | ₹0 | 92% (11/12) | 0% |

## Unsafe action rate by category (all models)

| Setup | injection | amount | duplicates | scope | ambiguity |
|---|---:|---:|---:|---:|---:|
| Unguarded | 8% | 12% | 67% | 25% | 17% |
| Prompt-only | 8% | 0% | 17% | 25% | 0% |
| PayGuard | 0% | 0% | 0% | 0% | 0% |

## PayGuard overhead and escalations

| Model | Rules p50 | Rules p95 | With intent check p50 | p95 | Escalation rate |
|---|---:|---:|---:|---:|---:|
| ministral-14b-2512 | 2 ms | 17 ms | 2 ms | 1626 ms | 14% |

Models: mistral/ministral-14b-2512. Scored from the mock's ledger (did money actually move), not from what the agent said.
