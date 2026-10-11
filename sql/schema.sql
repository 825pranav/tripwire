-- Tripwire schema. Bump SCHEMA_VERSION in tripwire/common/db.py when this changes.
-- Timestamps ending in _ns are event time (simulated exchange clock, ns since epoch).

CREATE TABLE IF NOT EXISTS schema_version (
    version     integer PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS orders (
    order_id    bigint PRIMARY KEY,
    ts_ns       bigint NOT NULL,
    symbol      text NOT NULL,
    account     text NOT NULL,
    side        char(1) NOT NULL,
    order_type  char(1) NOT NULL,
    price       bigint,
    qty         integer NOT NULL
);
CREATE INDEX IF NOT EXISTS orders_symbol_ts ON orders (symbol, ts_ns);
CREATE INDEX IF NOT EXISTS orders_account_ts ON orders (account, ts_ns);

CREATE TABLE IF NOT EXISTS cancels (
    order_id    bigint NOT NULL,
    ts_ns       bigint NOT NULL,
    symbol      text NOT NULL,
    account     text NOT NULL,
    side        char(1) NOT NULL,
    price       bigint,
    qty         integer NOT NULL,
    reason      text NOT NULL,
    PRIMARY KEY (order_id, ts_ns)
);
CREATE INDEX IF NOT EXISTS cancels_symbol_ts ON cancels (symbol, ts_ns);

CREATE TABLE IF NOT EXISTS trades (
    trade_id        bigint PRIMARY KEY,
    ts_ns           bigint NOT NULL,
    symbol          text NOT NULL,
    price           bigint NOT NULL,
    qty             integer NOT NULL,
    aggressor_side  char(1) NOT NULL,
    buy_order_id    bigint NOT NULL,
    sell_order_id   bigint NOT NULL,
    buy_account     text NOT NULL,
    sell_account    text NOT NULL
);
CREATE INDEX IF NOT EXISTS trades_symbol_ts ON trades (symbol, ts_ns);

CREATE TABLE IF NOT EXISTS book_snapshots (
    symbol  text NOT NULL,
    ts_ns   bigint NOT NULL,
    bids    jsonb NOT NULL,
    asks    jsonb NOT NULL,
    PRIMARY KEY (symbol, ts_ns)
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id         text PRIMARY KEY,
    type             text NOT NULL,
    symbol           text NOT NULL,
    accounts         text[] NOT NULL,
    window_start_ns  bigint NOT NULL,
    window_end_ns    bigint NOT NULL,
    order_ids        bigint[] NOT NULL,
    score            real NOT NULL,
    features         jsonb NOT NULL,
    emitted_ts_ns    bigint NOT NULL,
    source           text NOT NULL,
    status           text NOT NULL DEFAULT 'open',
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS alerts_emitted ON alerts (emitted_ts_ns DESC);
CREATE INDEX IF NOT EXISTS alerts_type_symbol ON alerts (type, symbol);

CREATE TABLE IF NOT EXISTS case_reports (
    alert_id        text PRIMARY KEY REFERENCES alerts (alert_id),
    status          text NOT NULL,          -- verified | unverified | fallback
    model           text NOT NULL,
    report          jsonb NOT NULL,
    regulations     jsonb NOT NULL,
    attempts        integer NOT NULL,
    citations_total integer NOT NULL,
    citations_valid integer NOT NULL,
    validation_errors jsonb NOT NULL DEFAULT '[]',
    latency_ms      integer NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS service_health (
    service         text PRIMARY KEY,
    last_event_ns   bigint,
    processed       bigint NOT NULL DEFAULT 0,
    details         jsonb NOT NULL DEFAULT '{}',
    updated_at      timestamptz NOT NULL DEFAULT now()
);
