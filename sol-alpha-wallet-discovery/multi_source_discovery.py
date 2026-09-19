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


