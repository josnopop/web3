-- Replay only the frozen pool. The @wallets array is produced before the replay
-- starts, hashed to frozen_wallet_pool.sha256, and may not be changed after 00:00 UTC+8.

WITH txs AS (
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
  WHERE block_timestamp >= TIMESTAMP('2026-09-02 16:00:00+00')
    AND block_timestamp <  TIMESTAMP('2026-09-03 16:00:00+00')
    AND err IS NULL
    AND (
      SELECT a.pubkey
      FROM UNNEST(accounts) AS a WITH OFFSET off
      WHERE a.signer
      ORDER BY off
      LIMIT 1
    ) IN UNNEST(@wallets)
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
pre_bal AS (
  SELECT
    t.block_slot, t.block_timestamp, t.signature, t.wallet,
    b.mint,
    SAFE_DIVIDE(CAST(b.amount AS BIGNUMERIC), POW(10, b.decimals)) AS pre_amount
  FROM txs t, UNNEST(t.pre_token_balances) b
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
  FROM txs t, UNNEST(t.post_token_balances) b
  WHERE b.owner = t.wallet
    AND b.mint NOT IN (
      'So11111111111111111111111111111111111111112',
      'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
      'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB'
    )
),
delta AS (
  SELECT
    COALESCE(p.block_slot, q.block_slot) AS block_slot,
    COALESCE(p.block_timestamp, q.block_timestamp) AS block_timestamp,
    COALESCE(p.signature, q.signature) AS signature,
    COALESCE(p.wallet, q.wallet) AS wallet,
    COALESCE(p.mint, q.mint) AS mint,
    CAST(COALESCE(q.post_amount,0)-COALESCE(p.pre_amount,0) AS FLOAT64) AS token_delta
  FROM pre_bal p
  FULL OUTER JOIN post_bal q
    USING(block_slot, block_timestamp, signature, wallet, mint)
  WHERE COALESCE(q.post_amount,0) != COALESCE(p.pre_amount,0)
),
native AS (
  SELECT
    t.signature,
    t.wallet,
    CAST(COALESCE((
      SELECT SUM(CAST(b.after - b.before AS FLOAT64))/1e9
      FROM UNNEST(t.balance_changes) b
      WHERE b.account=t.wallet
    ),0) AS FLOAT64) AS sol_delta
  FROM txs t
)
SELECT
  d.block_timestamp,
  d.block_slot,
  d.signature,
  d.wallet,
  d.mint,
  CASE
    WHEN d.token_delta > 0 AND n.sol_delta < 0 THEN 'BUY'
    WHEN d.token_delta < 0 AND n.sol_delta > 0 THEN 'SELL'
    ELSE 'OTHER'
  END AS side,
  ABS(d.token_delta) AS token_amount,
  ABS(n.sol_delta) AS quote_sol,
  SAFE_DIVIDE(ABS(n.sol_delta), ABS(d.token_delta)) AS observed_sol_per_token
FROM delta d
JOIN native n USING(signature,wallet)
WHERE (d.token_delta > 0 AND n.sol_delta < 0)
   OR (d.token_delta < 0 AND n.sol_delta > 0)
ORDER BY block_timestamp, block_slot, signature;
