Bench `after` at f44142f+dirty (2026-10-13T16:54:43+00:00), pinned CPU 0, one simulated day (seed 3000, 328,609 events).

| stage | events/s |
|---|---|
| matching engine (278,760 replayed commands) | 853,801 |
| rule detectors, in-process | 607,138 |
| ML window scorer, in-process | 915,191 |
| rules + ML, in-process | 344,500 |
| end to end through Kafka (max rate) | 83,422 |
| end to end through Kafka (paced 5,000 events/s) | 5,003 |

Alert latency, max rate (16 alerts): p50 1143.4 ms, p99 1781.9 ms, max 1781.9 ms (Kafka create time of the triggering event to alert ready, excluding the Postgres write).

Alert latency, paced 5,000 events/s (16 alerts): p50 63.3 ms, p99 130.6 ms, max 130.6 ms (Kafka create time of the triggering event to alert ready, excluding the Postgres write).
