Eval over 10 simulated days (seeds 1000-1009, 120-minute sessions, 3,283,813 events).

| method | type | precision | recall | F1 | alerts | episodes detected |
|---|---|---|---|---|---|---|
| rules | spoofing | 1.000 | 0.767 | 0.868 | 42 | 23/30 |
| rules | layering | 1.000 | 0.950 | 0.974 | 34 | 19/20 |
| rules | wash | 1.000 | 1.000 | 1.000 | 20 | 20/20 |
| rules | marking_close | 1.000 | 0.900 | 0.947 | 9 | 9/10 |
| rules | overall | 1.000 | 0.887 | 0.940 | 105 | 71/80 |
| ml | spoofing | - | 0.000 | - | 0 | 0/30 |
| ml | layering | - | 0.000 | - | 0 | 0/20 |
| ml | wash | 1.000 | 0.750 | 0.857 | 25 | 15/20 |
| ml | marking_close | 1.000 | 1.000 | 1.000 | 10 | 10/10 |
| ml | overall | 0.285 | 0.312 | 0.298 | 123 | 25/80 |
| union | spoofing | 1.000 | 0.767 | 0.868 | 42 | 23/30 |
| union | layering | 1.000 | 0.950 | 0.974 | 34 | 19/20 |
| union | wash | 1.000 | 1.000 | 1.000 | 45 | 20/20 |
| union | marking_close | 1.000 | 1.000 | 1.000 | 19 | 10/10 |
| union | overall | 0.614 | 0.900 | 0.730 | 228 | 72/80 |
| fusion | spoofing | 1.000 | 0.767 | 0.868 | 27 | 23/30 |
| fusion | layering | 1.000 | 0.950 | 0.974 | 20 | 19/20 |
| fusion | wash | 1.000 | 1.000 | 1.000 | 48 | 20/20 |
| fusion | marking_close | 1.000 | 1.000 | 1.000 | 10 | 10/10 |
| fusion | overall | 0.724 | 0.900 | 0.802 | 145 | 72/80 |

Throughput (in-process, single thread): simulator 266,149 events/s, detectors 629,177 events/s.
