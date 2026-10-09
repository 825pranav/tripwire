Eval over 20 simulated days (seeds 1000-1019, 120-minute sessions, 6,562,271 events).

| method | type | precision | recall | F1 | alerts | episodes detected |
|---|---|---|---|---|---|---|
| rules | spoofing | 1.000 | 0.767 | 0.868 | 89 | 46/60 |
| rules | layering | 1.000 | 0.875 | 0.933 | 71 | 35/40 |
| rules | wash | 1.000 | 1.000 | 1.000 | 40 | 40/40 |
| rules | marking_close | 1.000 | 0.850 | 0.919 | 17 | 17/20 |
| rules | overall | 1.000 | 0.863 | 0.926 | 217 | 138/160 |

Throughput (in-process, single thread): simulator 264,210 events/s, detectors 649,771 events/s.
