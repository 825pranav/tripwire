# Phase 8: profiling and optimisation

All numbers come from `python -m tripwire.eval.bench` (raw runs in `bench_history.jsonl`),
on one laptop (Intel i5-13500HX), process pinned to one performance core, one simulated
day (seed 3000, 328,609 events). Engine code did not change in this phase but measured
853k-980k events/s across runs, so treat differences under ~10% as noise.

## Profile

`py-spy` on rules + ML over a recorded day (`profiles/rules_ml_before.svg`,
`profiles/rules_ml_after.svg`). Before: about 40% of samples were inside
`IsolationForest.score_samples`. The streaming scorer calls it with a few windows at a
time, and sklearn pays a fixed cost per call (joblib dispatch over 200 trees and input
validation), about 6.7 ms for a 30-row batch.

## Changes

| change | why |
|---|---|
| `ml/fastforest.py`: score all trees for all rows at once with numpy over flattened tree arrays | same scores as sklearn (tested to 1e-12), 0.65 ms instead of 6.7 ms per 30-row batch |
| detector suite dispatches by event type (`wants`) | spoofing only needs cancels and wash only trades; skip the rest |
| market state caches best bid/ask and touch size per snapshot | every accepted order looked them up from the snapshot tuple |
| feature extractor caches the session bounds | the calendar was recomputed on every trade |

## In-process throughput (events/s)

| stage | before | after |
|---|---|---|
| rule detectors | 505,725 | 607,138 |
| ML window scorer | 560,475 | 915,191 |
| rules + ML | 242,326 | 344,500 |

## End to end through Kafka

Benchmarking end to end exposed three real problems, fixed in this phase:

1. **Events lost while catching up on a backlog.** librdkafka fetched large batches one
   partition at a time, so one topic ran far ahead of the others and the per-symbol
   reorder buffer hit its 10k cap and skipped ahead, dropping events. The cap is now
   1M and consumers fetch at most 64 KB per partition, which interleaves the topics.
   The "before" max-rate number (880 events/s) is therefore not a valid throughput
   figure; the run stalled until its deadline.
2. **Results depended on how symbols interleave.** Layering burst deadlines, ML window
   boundaries and the marking-the-close check were driven by *any* symbol's event time,
   so a symbol that fell behind had its windows closed early. They now track event time
   per symbol; `test_alerts_do_not_depend_on_how_symbols_are_interleaved` processes a day
   symbol by symbol and requires identical alerts. Both Kafka runs now raise the same 16
   alerts.
3. **Batching latency.** The consumer waited for up to 2,000 messages or 0.5 s per
   `consume` call; at 5,000 events/s that alone is ~400 ms. Now 500 messages / 50 ms.

| run | events/s | alerts | alert latency p50 / p99 |
|---|---|---|---|
| paced 5,000 events/s, before | 4,997 | 16 | 313.6 / 472.0 ms |
| paced 5,000 events/s, after | 5,003 | 16 | 63.3 / 130.6 ms |
| max rate, after | 83,422 | 16 | 1,143 / 1,782 ms (queueing behind the backlog) |

Alert latency is the Kafka create time of the triggering event to the alert being ready
in the consumer; it excludes the Postgres write the detector service does before
publishing. 16 alerts per run is a small sample, so p99 is close to the max.
