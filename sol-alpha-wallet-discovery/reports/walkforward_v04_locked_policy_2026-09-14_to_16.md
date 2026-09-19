# SOL Alpha Wallet Walk-forward V0.4 — locked single-alpha probe policy

## Locked policy

- Candidate discovery must be pre-cutoff; no next-day outcome may affect wallet selection.
- Final wallet classes only:
  - A / Copyable Wallet: may initiate a 0.03 SOL PROBE.
  - A- / Radar Wallet: may initiate a 0.03 SOL PROBE.
  - B+ / Confirmation Wallet: confirmation only; never initiates a position.
  - Rejected / incomplete history: no signal.
- A second independent qualified wallet within 15 minutes may upgrade total exposure to 0.10 SOL.
- Linked wallets count as one source.
- Friction: 1.5%.
- Existing hard risk rules remain: -18% hard stop; +50% begins scale-out; +100% further scale-out; final 20% trailing.
- For positions that do not reach a locked TP/SL threshold in the recovered observations, same-wallet subsequent on-chain sell is used only as a signal-layer exit proxy.
- Exact Token CA only; no ticker merging.
- Open/unresolved positions are excluded from realized PnL until closed.

## 2026-09-16 replay (freeze at 2026-09-15 16:00 UTC)

Strictly chain-qualified A/A- wallets with completed replay:
- 6SAzeAUF... — Copyable Wallet
- FVrq8ctq... — Radar Wallet

Realized PROBE results:
- Fd91C4...: +0.009608440 SOL
- Fxnc2i...: +0.002138813 SOL
- JGLw4v...: +0.004985772 SOL
- Zd8xgi...: -0.000277385 SOL
- fqW78J...: +0.002009405 SOL

Scorecard:
- Closed positions: 5
- Wins: 4
- Losses: 1
- Net realized: +0.018465044 SOL
- Deployed notional: 0.15 SOL
- Return on deployed notional: +12.31%

## 2026-09-15 replay (freeze at 2026-09-14 16:00 UTC)

Strictly qualified initiators confirmed so far:
- 6SAzeAUF... — A- Radar Wallet, score 77.9107
- 4EvYSYpt... — A- Radar Wallet, score 69.7005

Realized PROBE results:
- 4EvYSYpt / J2rrsW...: +0.007267796 SOL
- 6SAzeAUF / RdSJAd...: +0.002288056 SOL
- 6SAzeAUF / nnadFc...: +0.000310553 SOL
- 6SAzeAUF / Va1Jg8...: +0.002500698 SOL

Open/unrealized:
- 6SAzeAUF / fqW78J...: no same-day close; excluded from realized PnL.

Confirmed B+ only:
- BmCkRoBx...
- 3DUDTSL6...
- 9Ne9CG3K...

Rejected:
- tkGUThnn...
- CP4PeRPA...
- 54FksNZg...
- BfuFYhvC...
- DzvxjRT7...

Scorecard:
- Closed positions: 4
- Wins: 4
- Losses: 0
- Net realized: +0.012367103 SOL
- Closed-position deployed notional: 0.12 SOL
- Return on closed deployed notional: +10.31%

## Two-day realized scorecard

- Closed positions: 9
- Wins: 8
- Losses: 1
- Win rate: 88.89%
- Net realized: +0.030832147 SOL
- Closed-position deployed notional: 0.27 SOL
- Return on deployed notional: +11.42%

This is still a very small sample and is not a profitability claim.

## 2026-09-14 replay (freeze at 2026-09-13 16:00 UTC)

Replay complete:
- Successful tx: 226
- DEX tx: 177
- BUY rows: 102
- SELL rows: 28
- Buyer wallets: 6

Per user instruction, CKxSCwba... is not allowed to block the run and is excluded while unverified.

Strict rescore:
- zs6AGzba...: REJECT at Sep13 cutoff; its later +5.7% trade is correctly excluded.
- 6SAzeAUF...: strict Sep13-cutoff rescore currently running.
- BakXJNBF..., 7b9w4xv..., HRtVPhj4...: strict Sep13-cutoff rescore currently running.

Potential 6SAze realized proxy outcomes on Sep14, pending pre-cutoff class validation:
- +9.05%
- +8.97%
- +34.91%
- +33.71%
- If A/A- is confirmed, four 0.03 SOL probes would contribute +0.024194487 SOL after 1.5% friction.
