Eval over 20 simulated days (seeds 1000-1019, 120-minute sessions, 6,562,271 events).

| method | type | precision | recall | F1 | alerts | episodes detected |
|---|---|---|---|---|---|---|
| rules | spoofing | 1.000 | 0.767 | 0.868 | 89 | 46/60 |
| rules | layering | 1.000 | 0.875 | 0.933 | 71 | 35/40 |
| rules | wash | 1.000 | 1.000 | 1.000 | 40 | 40/40 |
| rules | marking_close | 1.000 | 0.850 | 0.919 | 17 | 17/20 |
| rules | overall | 1.000 | 0.863 | 0.926 | 217 | 138/160 |
| ml | spoofing | - | 0.000 | - | 0 | 0/60 |
| ml | layering | - | 0.000 | - | 0 | 0/40 |
| ml | wash | 1.000 | 0.750 | 0.857 | 56 | 30/40 |
| ml | marking_close | 1.000 | 1.000 | 1.000 | 20 | 20/20 |
| ml | overall | 0.308 | 0.312 | 0.310 | 247 | 50/160 |
| union | spoofing | 1.000 | 0.767 | 0.868 | 89 | 46/60 |
| union | layering | 1.000 | 0.875 | 0.933 | 71 | 35/40 |
| union | wash | 1.000 | 1.000 | 1.000 | 96 | 40/40 |
| union | marking_close | 1.000 | 1.000 | 1.000 | 37 | 20/20 |
| union | overall | 0.631 | 0.881 | 0.736 | 464 | 141/160 |
| fusion | spoofing | 1.000 | 0.717 | 0.835 | 49 | 43/60 |
| fusion | layering | 1.000 | 0.850 | 0.919 | 37 | 34/40 |
| fusion | wash | 1.000 | 1.000 | 1.000 | 94 | 40/40 |
| fusion | marking_close | 1.000 | 1.000 | 1.000 | 20 | 20/20 |
| fusion | overall | 0.694 | 0.856 | 0.767 | 288 | 137/160 |

Throughput (in-process, single thread): simulator 229,186 events/s, detectors 621,511 events/s.
LLM citation accuracy (qwen2.5:7b-instruct, 10 sampled alerts): 89/89 cited orders valid (1.0); 10 reports verified (10 on the first try), 0 unverified, 0 fallback; median 50537 ms per report.
