-- 2026-09-03 historical forward test: wallet pool must use only data before
-- 2026-09-03 00:00:00 UTC+8 (2026-09-02 16:00:00 UTC).
-- Cost safety: this query is time-partition constrained and the Python runner dry-runs
-- it before execution with MAX_BYTES_BILLED.

WITH dex_txs AS (
  SELECT
    block_slot,
    block_timestamp,
    signature,
    accounts,
    balance_changes,
    pre_token_balances,
    post_token_balances,
    (
      SELECT a.pubkey
      FROM UNNEST(accounts) AS a WITH OFFSET off
      WHERE a.signer
      ORDER BY off
      LIMIT 1
    ) AS wallet
  FROM `bigquery-public-data.crypto_solana_mainnet_us.Transactions`
  WHERE block_timestamp >= TIMESTAMP('2026-08-03 16:00:00+00')
    AND block_timestamp <  TIMESTAMP('2026-09-02 16:00:00+00')
    AND err IS NULL
    AND EXISTS (
      SELECT 1
      FROM UNNEST(accounts) AS a
      WHERE a.pubkey IN (
        '6EF8rrecthR5Dkzf5NzcraK8xtoqf2QvF6C4Zss5F6P',
        'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA',
        'JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4',
        '675kPX9MHTjS2zt1qfr1NYHuzeP4f4VgFkZyJgB9wCt',
        'CAMMCzo5YL8w4VFF8KVHrK22GGUQpKHpHrG2GGLS6V',
        'LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo',
        'whirLbMiicVdio4qvUfM5KAg6CtB8VDbQ8tX1YDPJ2'
      )
    )
),
eligible_txs AS (
  -- "Full market" discovery starts from every signer that actually touched a covered
  -- Solana DEX/launch program in the lookback. No hand-picked wallet list.
  SELECT *
  FROM dex_txs
  WHERE wallet IS NOT NULL
),
pre_bal AS (
  SELECT
    t.block_slot, t.block_timestamp, t.signature, t.wallet,
    b.mint,
    SAFE_DIVIDE(CAST(b.amount AS BIGNUMERIC), POW(10, b.decimals)) AS pre_amount
  FROM eligible_txs t, UNNEST(t.pre_token_balances) b
  WHERE b.owner = t.wallet
    AND b.mint NOT IN (
      'So11111111111111111111111111111111111111112',
      'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
      'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB'
    )
),
post_bal AS (
  SELECT
    t.block_slot, t.block_timestamp, t.signature, t.wallet,
    b.mint,
    SAFE_DIVIDE(CAST(b.amount AS BIGNUMERIC), POW(10, b.decimals)) AS post_amount
  FROM eligible_txs t, UNNEST(t.post_token_balances) b
  WHERE b.owner = t.wallet
    AND b.mint NOT IN (
      'So11111111111111111111111111111111111111112',
      'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
      'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB'
    )
),
token_deltas AS (
  SELECT
    COALESCE(p.block_slot, q.block_slot) AS block_slot,
    COALESCE(p.block_timestamp, q.block_timestamp) AS block_timestamp,
    COALESCE(p.signature, q.signature) AS signature,
    COALESCE(p.wallet, q.wallet) AS wallet,
    COALESCE(p.mint, q.mint) AS mint,
    CAST(COALESCE(q.post_amount, 0) - COALESCE(p.pre_amount, 0) AS FLOAT64) AS token_delta
  FROM pre_bal p
  FULL OUTER JOIN post_bal q
    USING (block_slot, block_timestamp, signature, wallet, mint)
  WHERE COALESCE(q.post_amount, 0) != COALESCE(p.pre_amount, 0)
),
native_delta AS (
  SELECT
    t.signature,
    t.wallet,
    CAST(COALESCE((
      SELECT SUM(CAST(b.after - b.before AS FLOAT64)) / 1e9
      FROM UNNEST(t.balance_changes) b
      WHERE b.account = t.wallet
    ), 0) AS FLOAT64) AS sol_delta
  FROM eligible_txs t
),
trades AS (
  SELECT
    d.block_slot,
    d.block_timestamp,
    d.signature,
    d.wallet,
    d.mint,
    d.token_delta,
    n.sol_delta,
    CASE
      WHEN d.token_delta > 0 AND n.sol_delta < 0 THEN 'BUY'
      WHEN d.token_delta < 0 AND n.sol_delta > 0 THEN 'SELL'
      ELSE 'OTHER'
    END AS side,
    CASE
      WHEN d.token_delta > 0 AND n.sol_delta < 0 THEN -n.sol_delta
      ELSE 0
    END AS quote_out_sol,
    CASE
      WHEN d.token_delta < 0 AND n.sol_delta > 0 THEN n.sol_delta
      ELSE 0
    END AS quote_in_sol
  FROM token_deltas d
  JOIN native_delta n USING (signature, wallet)
  WHERE ABS(d.token_delta) > 0
),
clean_trades AS (
  SELECT *
  FROM trades
  WHERE side IN ('BUY','SELL')
    -- remove obvious zero-value dust / pure fee effects
    AND (quote_out_sol >= 0.0005 OR quote_in_sol >= 0.0005)
),
token_stats AS (
  SELECT
    wallet,
    mint,
    COUNTIF(side='BUY') AS buys,
    COUNTIF(side='SELL') AS sells,
    SUM(quote_out_sol) AS spent_sol,
    SUM(quote_in_sol) AS received_sol,
    MIN(IF(side='BUY', block_timestamp, NULL)) AS first_buy_ts,
    MAX(IF(side='SELL', block_timestamp, NULL)) AS last_sell_ts
  FROM clean_trades
  GROUP BY wallet, mint
),
wallet_minute AS (
  SELECT wallet, TIMESTAMP_TRUNC(block_timestamp, MINUTE) minute, COUNT(*) n
  FROM clean_trades
  GROUP BY wallet, minute
),
wallet_slot AS (
  SELECT wallet, block_slot, COUNT(*) n
  FROM clean_trades
  GROUP BY wallet, block_slot
),
wallet_base AS (
  SELECT
    wallet,
    COUNT(DISTINCT signature) AS dex_txs,
    COUNT(DISTINCT DATE(block_timestamp)) AS active_days,
    COUNT(DISTINCT mint) AS distinct_tokens,
    COUNTIF(side='BUY') AS buys,
    COUNTIF(side='SELL') AS sells,
    SUM(quote_out_sol) AS gross_quote_out_sol,
    SUM(quote_in_sol) AS gross_quote_in_sol
  FROM clean_trades
  GROUP BY wallet
),
wallet_token AS (
  SELECT
    wallet,
    SUM(IF(buys > 0 AND sells > 0, received_sol - spent_sol, 0)) AS realized_quote_pnl_sol,
    COUNTIF(buys > 0 AND sells > 0 AND received_sol > spent_sol) AS win_tokens,
    COUNTIF(buys > 0 AND sells > 0) AS closed_tokens,
    APPROX_QUANTILES(
      IF(first_buy_ts IS NOT NULL AND last_sell_ts IS NOT NULL,
         TIMESTAMP_DIFF(last_sell_ts, first_buy_ts, MINUTE), NULL), 100
    )[OFFSET(50)] AS median_hold_minutes,
    COUNTIF(
      buys > 0 AND sells > 0
      AND spent_sol >= 0.05
      AND SAFE_DIVIDE(received_sol - spent_sol, spent_sol) <= -0.90
    ) AS rug_like_tokens
  FROM token_stats
  GROUP BY wallet
),
slot_features AS (
  SELECT
    wallet,
    SAFE_DIVIDE(SUM(IF(n > 1, n, 0)), SUM(n)) AS same_slot_ratio
  FROM wallet_slot
  GROUP BY wallet
),
minute_features AS (
  SELECT wallet, MAX(n) AS max_trades_per_minute
  FROM wallet_minute
  GROUP BY wallet
)
SELECT
  b.wallet,
  b.dex_txs,
  b.active_days,
  b.distinct_tokens,
  b.buys,
  b.sells,
  b.gross_quote_out_sol,
  b.gross_quote_in_sol,
  COALESCE(t.realized_quote_pnl_sol, 0) AS realized_quote_pnl_sol,
  COALESCE(t.win_tokens, 0) AS win_tokens,
  COALESCE(t.closed_tokens, 0) AS closed_tokens,
  COALESCE(CAST(t.median_hold_minutes AS FLOAT64), 0) AS median_hold_minutes,
  COALESCE(t.rug_like_tokens, 0) AS rug_like_tokens,
  COALESCE(s.same_slot_ratio, 0) AS same_slot_ratio,
  COALESCE(m.max_trades_per_minute, 0) AS max_trades_per_minute
FROM wallet_base b
LEFT JOIN wallet_token t USING(wallet)
LEFT JOIN slot_features s USING(wallet)
LEFT JOIN minute_features m USING(wallet)
WHERE b.dex_txs >= 5
  AND b.distinct_tokens >= 2
ORDER BY realized_quote_pnl_sol DESC;
