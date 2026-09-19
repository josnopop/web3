-- V0.3 historical market-wide discovery. All rows are strictly before @freeze_ts.
WITH dex_txs AS (
  SELECT
    block_slot, block_timestamp, signature, accounts, balance_changes,
    pre_token_balances, post_token_balances,
    (
      SELECT a.pubkey
      FROM UNNEST(accounts) AS a WITH OFFSET off
      WHERE a.signer
      ORDER BY off
      LIMIT 1
    ) AS wallet
  FROM `bigquery-public-data.crypto_solana_mainnet_us.Transactions`
  WHERE block_timestamp >= @lookback_start
    AND block_timestamp < @freeze_ts
    AND err IS NULL
    AND EXISTS (
      SELECT 1 FROM UNNEST(accounts) a
      WHERE a.pubkey IN UNNEST(@dex_program_ids)
    )
),
eligible_txs AS (
  SELECT * FROM dex_txs WHERE wallet IS NOT NULL
),
pre_bal AS (
  SELECT
    t.block_slot, t.block_timestamp, t.signature, t.wallet, b.mint,
    SAFE_DIVIDE(CAST(b.amount AS BIGNUMERIC), POW(10, b.decimals)) AS pre_amount
  FROM eligible_txs t, UNNEST(t.pre_token_balances) b
  WHERE b.owner = t.wallet
    AND b.mint NOT IN UNNEST(@quote_mints)
),
post_bal AS (
  SELECT
    t.block_slot, t.block_timestamp, t.signature, t.wallet, b.mint,
    SAFE_DIVIDE(CAST(b.amount AS BIGNUMERIC), POW(10, b.decimals)) AS post_amount
  FROM eligible_txs t, UNNEST(t.post_token_balances) b
  WHERE b.owner = t.wallet
    AND b.mint NOT IN UNNEST(@quote_mints)
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
clean_trades AS (
  SELECT
    d.block_slot, d.block_timestamp, d.signature, d.wallet, d.mint,
    CASE
      WHEN d.token_delta > 0 AND n.sol_delta < 0 THEN 'BUY'
      WHEN d.token_delta < 0 AND n.sol_delta > 0 THEN 'SELL'
    END AS side,
    IF(d.token_delta > 0 AND n.sol_delta < 0, -n.sol_delta, 0) AS quote_out_sol,
    IF(d.token_delta < 0 AND n.sol_delta > 0, n.sol_delta, 0) AS quote_in_sol
  FROM token_deltas d
  JOIN native_delta n USING (signature, wallet)
  WHERE (d.token_delta > 0 AND n.sol_delta < 0)
     OR (d.token_delta < 0 AND n.sol_delta > 0)
),
periods AS (
  SELECT 7 AS days UNION ALL SELECT 15 UNION ALL SELECT 30
),
window_trades AS (
  SELECT p.days, t.*
  FROM clean_trades t
  CROSS JOIN periods p
  WHERE t.block_timestamp >= TIMESTAMP_SUB(@freeze_ts, INTERVAL p.days DAY)
    AND (t.quote_out_sol >= 0.0005 OR t.quote_in_sol >= 0.0005)
),
token_stats AS (
  SELECT
    days, wallet, mint,
    COUNTIF(side='BUY') AS buys,
    COUNTIF(side='SELL') AS sells,
    SUM(quote_out_sol) AS spent_sol,
    SUM(quote_in_sol) AS received_sol,
    MIN(IF(side='BUY', block_timestamp, NULL)) AS first_buy_ts,
    MAX(IF(side='SELL', block_timestamp, NULL)) AS last_sell_ts
  FROM window_trades
  GROUP BY days, wallet, mint
),
token_closed AS (
  SELECT *,
    received_sol - spent_sol AS token_pnl,
    SAFE_DIVIDE(received_sol - spent_sol, NULLIF(spent_sol, 0)) AS token_roi
  FROM token_stats
  WHERE buys > 0 AND sells > 0
),
ranked_profit AS (
  SELECT *,
    ROW_NUMBER() OVER (
      PARTITION BY days, wallet
      ORDER BY GREATEST(token_pnl, 0) DESC
    ) AS rn,
    SUM(GREATEST(token_pnl, 0)) OVER (
      PARTITION BY days, wallet
    ) AS positive_profit
  FROM token_closed
),
wallet_base AS (
  SELECT
    days, wallet,
    COUNT(DISTINCT signature) AS dex_txs,
    COUNT(DISTINCT DATE(block_timestamp)) AS active_days,
    COUNT(DISTINCT mint) AS distinct_tokens
  FROM window_trades
  GROUP BY days, wallet
),
wallet_token AS (
  SELECT
    days, wallet,
    SUM(token_pnl) AS realized_pnl_sol,
    COUNTIF(token_pnl > 0) AS win_tokens,
    COUNT(*) AS closed_tokens,
    APPROX_QUANTILES(token_roi, 100)[OFFSET(50)] AS median_token_roi,
    APPROX_QUANTILES(
      TIMESTAMP_DIFF(last_sell_ts, first_buy_ts, MINUTE), 100
    )[OFFSET(50)] AS median_hold_minutes,
    COUNTIF(spent_sol >= 0.05 AND token_roi <= -0.90) AS rug_like_tokens
  FROM token_closed
  GROUP BY days, wallet
),
concentration AS (
  SELECT
    days, wallet,
    SAFE_DIVIDE(
      MAX(IF(rn=1, GREATEST(token_pnl,0), 0)),
      MAX(positive_profit)
    ) AS top1_profit_concentration,
    SAFE_DIVIDE(
      SUM(IF(rn<=3, GREATEST(token_pnl,0), 0)),
      MAX(positive_profit)
    ) AS top3_profit_concentration
  FROM ranked_profit
  GROUP BY days, wallet
),
minute_counts AS (
  SELECT days, wallet, TIMESTAMP_TRUNC(block_timestamp, MINUTE) AS minute, COUNT(*) AS n
  FROM window_trades
  GROUP BY days, wallet, minute
),
minute_features AS (
  SELECT days, wallet, MAX(n) AS max_trades_per_minute
  FROM minute_counts
  GROUP BY days, wallet
),
slot_counts AS (
  SELECT days, wallet, block_slot, COUNT(*) AS n
  FROM window_trades
  GROUP BY days, wallet, block_slot
),
slot_features AS (
  SELECT
    days, wallet,
    SAFE_DIVIDE(SUM(IF(n > 1, n, 0)), SUM(n)) AS same_slot_ratio
  FROM slot_counts
  GROUP BY days, wallet
)
SELECT
  b.days, b.wallet, b.dex_txs, b.active_days, b.distinct_tokens,
  COALESCE(t.realized_pnl_sol,0) AS realized_pnl_sol,
  COALESCE(t.win_tokens,0) AS win_tokens,
  COALESCE(t.closed_tokens,0) AS closed_tokens,
  COALESCE(t.median_token_roi,0) AS median_token_roi,
  COALESCE(c.top1_profit_concentration,0) AS top1_profit_concentration,
  COALESCE(c.top3_profit_concentration,0) AS top3_profit_concentration,
  COALESCE(CAST(t.median_hold_minutes AS FLOAT64),0) AS median_hold_minutes,
  COALESCE(t.rug_like_tokens,0) AS rug_like_tokens,
  COALESCE(s.same_slot_ratio,0) AS same_slot_ratio,
  COALESCE(m.max_trades_per_minute,0) AS max_trades_per_minute
FROM wallet_base b
LEFT JOIN wallet_token t USING(days,wallet)
LEFT JOIN concentration c USING(days,wallet)
LEFT JOIN slot_features s USING(days,wallet)
LEFT JOIN minute_features m USING(days,wallet)
WHERE b.dex_txs >= 5
  AND b.distinct_tokens >= 2
ORDER BY b.wallet, b.days;
