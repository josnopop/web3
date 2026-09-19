-- Replay strictly the immutable frozen pool during [@replay_start,@replay_end).
WITH txs AS (
  SELECT
    block_slot, block_timestamp, signature, accounts, balance_changes,
    pre_token_balances, post_token_balances,
    (
      SELECT a.pubkey
      FROM UNNEST(accounts) a WITH OFFSET off
      WHERE a.signer
      ORDER BY off
      LIMIT 1
    ) AS wallet
  FROM `bigquery-public-data.crypto_solana_mainnet_us.Transactions`
  WHERE block_timestamp >= @replay_start
    AND block_timestamp < @replay_end
    AND err IS NULL
    AND (
      SELECT a.pubkey
      FROM UNNEST(accounts) a WITH OFFSET off
      WHERE a.signer
      ORDER BY off
      LIMIT 1
    ) IN UNNEST(@wallets)
    AND EXISTS (
      SELECT 1 FROM UNNEST(accounts) a
      WHERE a.pubkey IN UNNEST(@dex_program_ids)
    )
),
pre_bal AS (
  SELECT
    t.block_slot,t.block_timestamp,t.signature,t.wallet,b.mint,
    SAFE_DIVIDE(CAST(b.amount AS BIGNUMERIC),POW(10,b.decimals)) AS pre_amount
  FROM txs t, UNNEST(t.pre_token_balances) b
  WHERE b.owner=t.wallet
    AND b.mint NOT IN UNNEST(@quote_mints)
),
post_bal AS (
  SELECT
    t.block_slot,t.block_timestamp,t.signature,t.wallet,b.mint,
    SAFE_DIVIDE(CAST(b.amount AS BIGNUMERIC),POW(10,b.decimals)) AS post_amount
  FROM txs t, UNNEST(t.post_token_balances) b
  WHERE b.owner=t.wallet
    AND b.mint NOT IN UNNEST(@quote_mints)
),
delta AS (
  SELECT
    COALESCE(p.block_slot,q.block_slot) AS block_slot,
    COALESCE(p.block_timestamp,q.block_timestamp) AS block_timestamp,
    COALESCE(p.signature,q.signature) AS signature,
    COALESCE(p.wallet,q.wallet) AS wallet,
    COALESCE(p.mint,q.mint) AS mint,
    CAST(COALESCE(q.post_amount,0)-COALESCE(p.pre_amount,0) AS FLOAT64) AS token_delta
  FROM pre_bal p
  FULL OUTER JOIN post_bal q
    USING(block_slot,block_timestamp,signature,wallet,mint)
  WHERE COALESCE(q.post_amount,0)!=COALESCE(p.pre_amount,0)
),
native AS (
  SELECT
    t.signature,t.wallet,
    CAST(COALESCE((
      SELECT SUM(CAST(b.after-b.before AS FLOAT64))/1e9
      FROM UNNEST(t.balance_changes)b
      WHERE b.account=t.wallet
    ),0) AS FLOAT64) AS sol_delta
  FROM txs t
)
SELECT
  d.block_timestamp,d.block_slot,d.signature,d.wallet,d.mint,
  CASE
    WHEN d.token_delta>0 AND n.sol_delta<0 THEN 'BUY'
    WHEN d.token_delta<0 AND n.sol_delta>0 THEN 'SELL'
    ELSE 'OTHER'
  END AS side,
  ABS(d.token_delta) AS token_amount,
  ABS(n.sol_delta) AS quote_sol,
  SAFE_DIVIDE(ABS(n.sol_delta),ABS(d.token_delta)) AS observed_sol_per_token
FROM delta d
JOIN native n USING(signature,wallet)
WHERE (d.token_delta>0 AND n.sol_delta<0)
   OR (d.token_delta<0 AND n.sol_delta>0)
ORDER BY block_timestamp,block_slot,signature;
