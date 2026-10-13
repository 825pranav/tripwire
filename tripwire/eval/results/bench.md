Bench `check-full` at 633426f+dirty (2026-10-13T17:05:40+00:00), pinned CPU 0, one simulated day (seed 3000, 328,609 events).

| stage | events/s |
|---|---|
| matching engine (278,760 replayed commands) | 789,466 |
| rule detectors, in-process | 601,106 |
| ML window scorer, in-process | 915,773 |
| rules + ML, in-process | 342,632 |
| end to end through Kafka (max rate) | 96,835 |
| end to end through Kafka (paced 5,000 events/s) | 5,003 |

Alert latency, max rate (16 alerts): p50 1968.1 ms, p99 2576.8 ms, max 2576.8 ms (Kafka create time of the triggering event to alert ready, excluding the Postgres write).

Alert latency, paced 5,000 events/s (16 alerts): p50 83.0 ms, p99 99.9 ms, max 99.9 ms (Kafka create time of the triggering event to alert ready, excluding the Postgres write).
