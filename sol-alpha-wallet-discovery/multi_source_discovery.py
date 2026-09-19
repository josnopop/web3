from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SOL_ADDR_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


@dataclass
class Evidence:
    wallet: str
    source: str
    kind: str
    live_only: bool
    metadata: dict[str, Any]


@dataclass
class SourceRun:
    source: str
    enabled: bool
    ok: bool
    count: int
    reason: str = ""


def _request_json(url: str, *, headers=None, method="GET", body=None, timeout=30):
    data = None
    req_headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, headers=req_headers, data=data, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _walk_wallets(obj: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k).lower()
            if (
                isinstance(v, str)
                and SOL_ADDR_RE.fullmatch(v)
                and any(x in key for x in ("wallet", "address", "trader", "signer", "maker"))
            ):
                out.add(v)
            out.update(_walk_wallets(v))
    elif isinstance(obj, list):
        for item in obj:
            out.update(_walk_wallets(item))
    return out


def _walk_mints(obj: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k).lower()
            if (
                isinstance(v, str)
                and SOL_ADDR_RE.fullmatch(v)
                and any(
                    x in key
                    for x in (
                        "mint",
                        "tokenaddress",
                        "token_address",
                        "contractaddress",
                        "contract_address",
                    )
                )
            ):
                out.add(v)
            out.update(_walk_mints(v))
    elif isinstance(obj, list):
        for item in obj:
            out.update(_walk_mints(item))
    return out


def okx_candidates() -> list[Evidence]:
    key = os.getenv("OKX_API_KEY")
    secret = os.getenv("OKX_SECRET_KEY")
    passphrase = os.getenv("OKX_API_PASSPHRASE")
    if not (key and secret and passphrase):
        raise RuntimeError(
            "missing OKX_API_KEY/OKX_SECRET_KEY/OKX_API_PASSPHRASE"
        )
    project = os.getenv("OKX_PROJECT_ID")
    base = "https://web3.okx.com"
    path = "/api/v6/dex/market/leaderboard/list"
    evidence: dict[str, Evidence] = {}

    # smart money / whale / new wallet / sniper / Pump smart money
    for wallet_type in (3, 4, 5, 7, 10):
        for timeframe in (1, 3, 4):  # 1D / 7D / 1M
            for sort_by in (1, 2, 5):  # PnL / win rate / ROI
                params = {
                    "chainIndex": "501",
                    "timeFrame": str(timeframe),
                    "sortBy": str(sort_by),
                    "walletType": str(wallet_type),
                }
                qs = urllib.parse.urlencode(params)
                request_path = path + "?" + qs
                ts = (
                    datetime.now(timezone.utc)
                    .isoformat(timespec="milliseconds")
                    .replace("+00:00", "Z")
                )
                msg = ts + "GET" + request_path
                sig = base64.b64encode(
                    hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()
                ).decode()
                headers = {
                    "OK-ACCESS-KEY": key,
                    "OK-ACCESS-SIGN": sig,
                    "OK-ACCESS-TIMESTAMP": ts,
                    "OK-ACCESS-PASSPHRASE": passphrase,
                }
                if project:
                    headers["OK-ACCESS-PROJECT"] = project
                payload = _request_json(base + request_path, headers=headers)
                if str(payload.get("code")) != "0":
                    raise RuntimeError(
                        f"OKX code={payload.get('code')} msg={payload.get('msg')}"
                    )
                for row in payload.get("data") or []:
                    wallet = row.get("walletAddress") or row.get("address")
                    if not wallet or not SOL_ADDR_RE.fullmatch(wallet):
                        continue
                    hit = {
                        "wallet_type": wallet_type,
                        "timeframe": timeframe,
                        "sort_by": sort_by,
                        "realized_pnl_usd": row.get("realizedPnlUsd"),
                        "win_rate": row.get("winRate"),
                        "txs": row.get("txs"),
                        "roi": row.get("profitRate"),
                        "last_active_timestamp": row.get("lastActiveTimestamp"),
                    }
                    if wallet not in evidence:
                        evidence[wallet] = Evidence(
                            wallet,
                            "OKX Onchain Smart Money",
                            "leaderboard",
                            True,
                            {"hits": [hit]},
                        )
                    else:
                        evidence[wallet].metadata.setdefault("hits", []).append(hit)
                time.sleep(0.05)
    return list(evidence.values())


def dexscreener_seed_mints() -> set[str]:
    mints: set[str] = set()
    for endpoint in (
        "https://api.dexscreener.com/token-profiles/latest/v1",
        "https://api.dexscreener.com/token-profiles/recent-updates/v1",
        "https://api.dexscreener.com/token-boosts/latest/v1",
        "https://api.dexscreener.com/token-boosts/top/v1",
    ):
        payload = _request_json(endpoint)
        rows = payload if isinstance(payload, list) else [payload]
        for row in rows:
            if not isinstance(row, dict) or str(row.get("chainId")).lower() != "solana":
                continue
            mint = row.get("tokenAddress")
            if mint and SOL_ADDR_RE.fullmatch(mint):
                mints.add(mint)
    return mints


def cielo_seed_mints(limit: int = 50) -> set[str]:
    key = os.getenv("CIELO_API_KEY")
    if not key:
        raise RuntimeError("missing CIELO_API_KEY")
    params = urllib.parse.urlencode({
        "chain": "solana",
        "interval": "1h",
        "limit": min(limit, 50),
    })
    payload = _request_json(
        "https://feed-api.cielo.finance/api/v1/trending-tokens?" + params,
        headers={"X-API-KEY": key},
    )
    mints = _walk_mints(payload)
    # Cielo responses can use generic address/token keys. Accept Solana-looking
    # addresses from token-shaped records as seeds, never as wallet evidence.
    if not mints:
        def walk_token_addresses(obj: Any) -> set[str]:
            found: set[str] = set()
            if isinstance(obj, dict):
                tokenish = any(x in str(k).lower() for k in obj for x in ("token", "mint", "contract"))
                if tokenish:
                    for k, v in obj.items():
                        if (
                            isinstance(v, str)
                            and SOL_ADDR_RE.fullmatch(v)
                            and str(k).lower() in ("address", "token", "token_address", "mint")
                        ):
                            found.add(v)
                for v in obj.values():
                    found.update(walk_token_addresses(v))
            elif isinstance(obj, list):
                for v in obj:
                    found.update(walk_token_addresses(v))
            return found
        mints = walk_token_addresses(payload)
    return mints


def solanatracker_candidates(
    mints: list[str], per_token_limit: int = 100
) -> list[Evidence]:
    key = os.getenv("SOLANATRACKER_API_KEY")
    if not key:
        raise RuntimeError("missing SOLANATRACKER_API_KEY")
    evidence: dict[str, Evidence] = {}
    for mint in mints:
        rows = _request_json(
            f"https://data.solanatracker.io/top-traders/{mint}",
            headers={"x-api-key": key},
        )
        for row in (rows or [])[:per_token_limit]:
            wallet = row.get("wallet")
            if not wallet or not SOL_ADDR_RE.fullmatch(wallet):
                continue
            hit = {
                "mint": mint,
                "realized": row.get("realized"),
                "unrealized": row.get("unrealized"),
                "total": row.get("total"),
                "total_invested": row.get("total_invested"),
            }
            if wallet not in evidence:
                evidence[wallet] = Evidence(
                    wallet,
                    "Solana Tracker",
                    "token_top_trader",
                    True,
                    {"tokens": [hit]},
                )
            else:
                evidence[wallet].metadata.setdefault("tokens", []).append(hit)
        time.sleep(0.05)
    return list(evidence.values())


def birdeye_seed_mints(limit: int = 100) -> tuple[list[Evidence], set[str]]:
    key = os.getenv("BIRDEYE_API_KEY")
    if not key:
        raise RuntimeError("missing BIRDEYE_API_KEY")
    url = (
        "https://public-api.birdeye.so/smart-money/v1/token/list?"
        + urllib.parse.urlencode({"limit": limit})
    )
    payload = _request_json(
        url,
        headers={"X-API-KEY": key, "x-chain": "solana"},
    )
    mints = _walk_mints(payload)
    wallets = _walk_wallets(payload)
    return (
        [
            Evidence(w, "Birdeye Smart Money", "smart_money", True, {})
            for w in wallets
        ],
        mints,
    )


def nansen_candidates(limit: int = 100) -> list[Evidence]:
    key = os.getenv("NANSEN_API_KEY")
    if not key:
        raise RuntimeError("missing NANSEN_API_KEY")
    body = {
        "chains": ["solana"],
        "pagination": {"page": 1, "per_page": min(limit, 100)},
        "order_by": [{"field": "value_usd", "direction": "DESC"}],
    }
    payload = _request_json(
        "https://api.nansen.ai/api/v1/smart-money/dex-trades",
        headers={"apiKey": key},
        method="POST",
        body=body,
    )
    return [
        Evidence(w, "Nansen Smart Money", "dex_trade", True, {})
        for w in sorted(_walk_wallets(payload))
    ]


def bitquery_candidates(mints: list[str], per_token_limit: int = 100) -> list[Evidence]:
    token = os.getenv("BITQUERY_API_KEY") or os.getenv("BITQUERY_TOKEN")
    if not token:
        raise RuntimeError("missing BITQUERY_API_KEY/BITQUERY_TOKEN")
    endpoint = os.getenv("BITQUERY_GRAPHQL_URL", "https://streaming.bitquery.io/graphql")
    evidence: dict[str, Evidence] = {}
    query = """
    query TopTraders($mint: String!, $limit: Int!) {
      Solana(dataset: realtime) {
        DEXTradeByTokens(
          where: {
            Trade: {
              Currency: { MintAddress: { is: $mint } }
              Side: { Currency: { MintAddress: { is: "So11111111111111111111111111111111111111112" } } }
            }
            Transaction: { Result: { Success: true } }
          }
          orderBy: { descendingByField: "volume" }
          limit: { count: $limit }
        ) {
          Trade { Account { Owner } }
          buys: count(if: { Trade: { Side: { Type: { is: buy } } } })
          sells: count(if: { Trade: { Side: { Type: { is: sell } } } })
          volume: sum(of: Trade_Side_AmountInUSD)
          trades: count
        }
      }
    }
    """
    auth = token if token.lower().startswith("bearer ") else "Bearer " + token
    for mint in mints:
        payload = _request_json(
            endpoint,
            headers={"Authorization": auth},
            method="POST",
            body={"query": query, "variables": {"mint": mint, "limit": min(per_token_limit, 100)}},
        )
        if payload.get("errors"):
            raise RuntimeError(str(payload["errors"])[:500])
        rows = (((payload.get("data") or {}).get("Solana") or {}).get("DEXTradeByTokens") or [])
        for row in rows:
            owner = (((row.get("Trade") or {}).get("Account") or {}).get("Owner"))
            if not owner or not SOL_ADDR_RE.fullmatch(owner):
                continue
            hit = {
                "mint": mint,
                "buys": row.get("buys"),
                "sells": row.get("sells"),
                "volume_usd": row.get("volume"),
                "trades": row.get("trades"),
            }
            if owner not in evidence:
                evidence[owner] = Evidence(owner, "Bitquery", "token_top_trader", True, {"tokens": [hit]})
            else:
                evidence[owner].metadata.setdefault("tokens", []).append(hit)
        time.sleep(0.05)
    return list(evidence.values())


def codex_candidates(limit: int = 100) -> list[Evidence]:
    key = os.getenv("CODEX_API_KEY")
    if not key:
        raise RuntimeError("missing CODEX_API_KEY")
    endpoint = os.getenv("CODEX_GRAPHQL_URL", "https://graph.codex.io/graphql")
    header_name = os.getenv("CODEX_API_HEADER", "Authorization")
    evidence: dict[tuple[str, str], Evidence] = {}

    # Codex powers Defined and exposes trade-source IDs such as "axiom" and "defined".
    # Query all-wallet performance plus explicit Axiom/Defined views.
    for trade_source, source_name in (
        (None, "Codex Wallet Filter"),
        ("axiom", "Axiom via Codex"),
        ("defined", "Defined via Codex"),
    ):
        source_clause = (
            ""
            if trade_source is None
            else f'includeTradeSourceIds: ["{trade_source}"]'
        )
        query = f"""
        query DiscoverSolanaWallets($limit: Int!) {{
          filterWallets(input: {{
            filters: {{ networkId: 1399811149, swaps1w: {{ gte: 3 }} }}
            {source_clause}
            rankings: [{{ attribute: realizedProfitUsd1w, direction: DESC }}]
            limit: $limit
          }}) {{
            results {{
              address labels tradeSourceIds lastTransactionAt firstTransactionAt
              realizedProfitUsd1w realizedProfitPercentage1w winRate1w
              swaps1w uniqueTokens1w
              realizedProfitUsd30d realizedProfitPercentage30d winRate30d
              swaps30d uniqueTokens30d
            }}
          }}
        }}
        """
        payload = _request_json(
            endpoint,
            headers={header_name: key},
            method="POST",
            body={"query": query, "variables": {"limit": min(limit, 100)}},
        )
        if payload.get("errors"):
            raise RuntimeError(str(payload["errors"])[:500])
        rows = (((payload.get("data") or {}).get("filterWallets") or {}).get("results") or [])
        for row in rows:
            wallet = row.get("address")
            if wallet and SOL_ADDR_RE.fullmatch(wallet):
                evidence[(wallet, source_name)] = Evidence(
                    wallet,
                    source_name,
                    "wallet_filter",
                    True,
                    row,
                )
    return list(evidence.values())




def arkham_candidates(mints: list[str], per_token_limit: int = 100) -> list[Evidence]:
    key = os.getenv("ARKHAM_API_KEY")
    if not key:
        raise RuntimeError("missing ARKHAM_API_KEY")
    evidence: dict[str, Evidence] = {}
    for mint in mints:
        payload = _request_json(
            f"https://api.arkm.com/token/holders/solana/{mint}",
            headers={"API-Key": key},
        )
        wallets = sorted(_walk_wallets(payload))[:per_token_limit]
        for wallet in wallets:
            hit = {"mint": mint}
            if wallet not in evidence:
                evidence[wallet] = Evidence(
                    wallet, "Arkham Intel", "token_holder", True, {"tokens": [hit]}
                )
            else:
                evidence[wallet].metadata.setdefault("tokens", []).append(hit)
        time.sleep(0.05)
    return list(evidence.values())


def dune_candidates() -> list[Evidence]:
    key = os.getenv("DUNE_API_KEY")
    raw_ids = os.getenv("DUNE_QUERY_IDS", "")
    ids = [x.strip() for x in raw_ids.split(",") if x.strip().isdigit()]
    if not key or not ids:
        raise RuntimeError("missing DUNE_API_KEY or DUNE_QUERY_IDS")
    evidence: dict[str, Evidence] = {}
    for query_id in ids:
        payload = _request_json(
            f"https://api.dune.com/api/v1/query/{query_id}/results",
            headers={"X-Dune-API-Key": key},
        )
        for wallet in sorted(_walk_wallets(payload)):
            hit = {"query_id": int(query_id)}
            if wallet not in evidence:
                evidence[wallet] = Evidence(
                    wallet, "Dune", "query_result", True, {"queries": [hit]}
                )
            else:
                evidence[wallet].metadata.setdefault("queries", []).append(hit)
    return list(evidence.values())


def gmgn_candidates(limit: int = 200) -> list[Evidence]:
    exe = shutil.which("gmgn-cli")
    if not exe:
        raise RuntimeError("gmgn-cli not installed")
    cp = subprocess.run(
        [exe, "track", "smartmoney", "--chain", "sol", "--limit", str(limit), "--raw"],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(cp.stdout)
    return [Evidence(w, "GMGN", "smartmoney", True, {}) for w in sorted(_walk_wallets(payload))]


def json_bridge(path: str, source: str) -> list[Evidence]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Evidence(w, source, "json_bridge", True, {}) for w in sorted(_walk_wallets(payload))]


def merge(groups: list[list[Evidence]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for e in group:
            row = merged.setdefault(
                e.wallet,
                {"wallet": e.wallet, "sources": set(), "evidence": [], "source_count": 0},
            )
            row["sources"].add(e.source)
            row["evidence"].append({
                "source": e.source,
                "kind": e.kind,
                "live_only": e.live_only,
                "metadata": e.metadata,
            })
    out = []
    for row in merged.values():
        row["sources"] = sorted(row["sources"])
        row["source_count"] = len(row["sources"])
        row["discovery_priority"] = row["source_count"] * 10 + len(row["evidence"])
        out.append(row)
    out.sort(key=lambda x: (-x["discovery_priority"], -x["source_count"], x["wallet"]))
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mints-file", help="JSON/text token-mint source for token-top-trader adapters")
    p.add_argument("--degenradar-json")
    p.add_argument("--extra-json", action="append", default=[], help="SOURCE=path")
    p.add_argument("--out", required=True)
    p.add_argument("--no-okx", action="store_true")
    p.add_argument("--no-solanatracker", action="store_true")
    p.add_argument("--no-dexscreener", action="store_true")
    p.add_argument("--no-cielo", action="store_true")
    p.add_argument("--no-birdeye", action="store_true")
    p.add_argument("--no-nansen", action="store_true")
    p.add_argument("--no-codex", action="store_true")
    p.add_argument("--no-bitquery", action="store_true")
    p.add_argument("--no-gmgn", action="store_true")
    p.add_argument("--no-arkham", action="store_true")
    p.add_argument("--no-dune", action="store_true")
    args = p.parse_args()

    mints: set[str] = set()
    if args.mints_file:
        txt = Path(args.mints_file).read_text(encoding="utf-8")
        try:
            mints.update(_walk_mints(json.loads(txt)))
        except Exception:
            mints.update(
                x for x in re.findall(r"[1-9A-HJ-NP-Za-km-z]{32,44}", txt)
                if SOL_ADDR_RE.fullmatch(x)
            )

    groups: list[list[Evidence]] = []
    runs: list[SourceRun] = []

    def run_source(name: str, enabled: bool, fn):
        if not enabled:
            runs.append(SourceRun(name, False, False, 0, "disabled or credentials unavailable"))
            return
        try:
            rows = fn()
            groups.append(rows)
            runs.append(SourceRun(name, True, True, len(rows)))
        except Exception as e:
            runs.append(SourceRun(name, True, False, 0, f"{type(e).__name__}: {e}"))

    run_source(
        "OKX Onchain",
        not args.no_okx
        and all(os.getenv(k) for k in ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_API_PASSPHRASE")),
        okx_candidates,
    )

    if not args.no_dexscreener:
        try:
            more_mints = dexscreener_seed_mints()
            mints.update(more_mints)
            runs.append(SourceRun("DEX Screener", True, True, 0, f"seed_mints={len(more_mints)}"))
        except Exception as e:
            runs.append(SourceRun("DEX Screener", True, False, 0, f"{type(e).__name__}: {e}"))
    else:
        runs.append(SourceRun("DEX Screener", False, False, 0, "disabled"))

    if not args.no_cielo and os.getenv("CIELO_API_KEY"):
        try:
            more_mints = cielo_seed_mints()
            mints.update(more_mints)
            runs.append(SourceRun("Cielo", True, True, 0, f"seed_mints={len(more_mints)}"))
        except Exception as e:
            runs.append(SourceRun("Cielo", True, False, 0, f"{type(e).__name__}: {e}"))
    else:
        runs.append(SourceRun("Cielo", False, False, 0, "disabled or credentials unavailable"))

    if not args.no_birdeye and os.getenv("BIRDEYE_API_KEY"):
        try:
            rows, more_mints = birdeye_seed_mints()
            groups.append(rows)
            mints.update(more_mints)
            runs.append(SourceRun("Birdeye", True, True, len(rows), f"seed_mints={len(more_mints)}"))
        except Exception as e:
            runs.append(SourceRun("Birdeye", True, False, 0, f"{type(e).__name__}: {e}"))
    else:
        runs.append(SourceRun("Birdeye", False, False, 0, "disabled or credentials unavailable"))

    run_source(
        "Solana Tracker",
        not args.no_solanatracker and bool(os.getenv("SOLANATRACKER_API_KEY")) and bool(mints),
        lambda: solanatracker_candidates(sorted(mints)[:50]),
    )
    run_source(
        "Nansen",
        not args.no_nansen and bool(os.getenv("NANSEN_API_KEY")),
        nansen_candidates,
    )
    run_source(
        "Bitquery",
        not args.no_bitquery
        and bool(os.getenv("BITQUERY_API_KEY") or os.getenv("BITQUERY_TOKEN"))
        and bool(mints),
        lambda: bitquery_candidates(sorted(mints)[:50]),
    )
    run_source(
        "Codex/Axiom/Defined",
        not args.no_codex and bool(os.getenv("CODEX_API_KEY")),
        codex_candidates,
    )
    run_source(
        "Arkham",
        not args.no_arkham and bool(os.getenv("ARKHAM_API_KEY")) and bool(mints),
        lambda: arkham_candidates(sorted(mints)[:30]),
    )
    run_source(
        "Dune",
        not args.no_dune
        and bool(os.getenv("DUNE_API_KEY"))
        and bool(os.getenv("DUNE_QUERY_IDS")),
        dune_candidates,
    )
    run_source(
        "GMGN",
        not args.no_gmgn and shutil.which("gmgn-cli") is not None,
        gmgn_candidates,
    )

    if args.degenradar_json:
        run_source(
            "DegenRadar",
            Path(args.degenradar_json).exists(),
            lambda: json_bridge(args.degenradar_json, "DegenRadar"),
        )
    for spec in args.extra_json:
        source, pth = spec.split("=", 1)
        run_source(source, Path(pth).exists(), lambda p=pth, s=source: json_bridge(p, s))

    wallets = merge(groups)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "live/broad candidate discovery; never use live-only evidence as proof in historical freezes",
        "historical_backfill_allowed": False,
        "sources": [asdict(x) for x in runs],
        "seed_mint_count": len(mints),
        "unique_wallet_count": len(wallets),
        "wallets": wallets,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"UNIQUE_WALLETS={len(wallets)} "
        f"SOURCES_OK={sum(x.ok for x in runs)} "
        f"SEED_MINTS={len(mints)}"
    )
    for x in runs:
        print(f"{x.source}: enabled={x.enabled} ok={x.ok} count={x.count} {x.reason}")
    print(f"WROTE={out}")


if __name__ == "__main__":
    main()
