#!/usr/bin/env python3
"""
Alpha Radar - Catalyst/News Ranked Stock Scanner

Scans a broader liquid U.S. stock universe, fetches batch quote/history data via
Yahoo Finance (yfinance), recent headlines from Google News RSS, scores
catalysts/sentiment, and produces a ranked candidate list for the trading bot.
"""

import json
import math
import os
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import List, Dict, Any

import requests
import yfinance as yf
try:
    import market_data
except Exception:
    market_data = None


DEFAULT_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMD", "AMZN", "META", "GOOGL", "TSLA",
    "AVGO", "NFLX", "PLTR", "UBER", "JPM", "BAC", "GS", "WMT",
    "COST", "LLY", "UNH", "XOM", "CVX", "LRCX", "MU", "ANET",
    "PANW", "CRWD", "HOOD", "ORCL"
]

COMPANY_NAMES = {
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "Nvidia", "AMD": "Advanced Micro Devices",
    "AMZN": "Amazon", "META": "Meta", "GOOGL": "Alphabet", "TSLA": "Tesla",
    "AVGO": "Broadcom", "NFLX": "Netflix", "PLTR": "Palantir", "UBER": "Uber",
    "JPM": "JPMorgan", "BAC": "Bank of America", "GS": "Goldman Sachs", "WMT": "Walmart",
    "COST": "Costco", "LLY": "Eli Lilly", "UNH": "UnitedHealth", "XOM": "Exxon Mobil",
    "CVX": "Chevron", "LRCX": "Lam Research", "MU": "Micron", "ANET": "Arista Networks",
    "PANW": "Palo Alto Networks", "CRWD": "CrowdStrike", "HOOD": "Robinhood", "ORCL": "Oracle",
}

POSITIVE_KEYWORDS = {
    "beat": 3, "beats": 3, "upgrade": 3, "upgrades": 3, "raised": 3,
    "raises": 3, "guidance": 2, "buyback": 3, "partnership": 2,
    "deal": 2, "contract": 2, "approval": 3, "approved": 3,
    "launch": 2, "wins": 2, "record": 2, "growth": 2, "ai": 1,
    "backlog": 2, "demand": 2, "expansion": 2,
}

NEGATIVE_KEYWORDS = {
    "miss": -3, "misses": -3, "downgrade": -3, "downgrades": -3,
    "cut": -2, "cuts": -2, "lawsuit": -3, "probe": -3,
    "investigation": -3, "fraud": -4, "recall": -3, "warns": -2,
    "warning": -2, "delay": -2, "delays": -2, "weak": -2,
    "fall": -1, "falls": -1, "drop": -1, "drops": -1, "decline": -2,
}

CATALYST_HINTS = (
    "earnings", "guidance", "upgrade", "downgrade", "approval", "ai",
    "contract", "deal", "launch", "buyback", "partnership", "investigation"
)


class AlphaRadar:
    def __init__(self, config_path: str = "./config/autonomy_config.json"):
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)

        self.criteria = self.config.get('stock_criteria', {})
        self.candidates_path = "./data/candidates.json"
        self.max_candidates = int(
            self.config.get("consensus_rules", {}).get("max_candidates_to_llm")
            or self.config.get("scanner", {}).get("max_candidates")
            or 10
        )
        self.universe = DEFAULT_UNIVERSE
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36"
        })

    def fetch_quotes_bulk(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        # Prefer FMP when configured
        if market_data is not None:
            try:
                # Free FMP = singles only; never burn budget on full universe.
                bulk = market_data.quotes_bulk(
                    symbols, prefer="yfinance", allow_fmp_singles=False
                )
                if bulk:
                    for sym, q in bulk.items():
                        out[sym] = {
                            "name": q.get("name") or sym,
                            "price": q.get("price"),
                            "volume": q.get("volume") or 0.0,
                            "avg_volume": q.get("avg_volume") or q.get("volume") or 0.0,
                            "percent_change": q.get("percent_change") or 0.0,
                            "exchange": q.get("exchange") or "",
                            "source": q.get("source") or "yfinance",
                        }
                    if len(out) >= max(1, int(len(symbols) * 0.5)):
                        return out
            except Exception:
                out = {}
        hist = yf.download(
            tickers=" ".join(symbols),
            period="10d",
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=True,
            group_by="ticker",
        )

        for symbol in symbols:
            try:
                sym_hist = hist[symbol] if symbol in hist.columns.get_level_values(0) else None
                if sym_hist is None or sym_hist.empty:
                    continue
                closes = sym_hist["Close"].dropna()
                volumes = sym_hist["Volume"].dropna()
                if closes.empty or volumes.empty:
                    continue
                last_close = float(closes.iloc[-1])
                prev_close = float(closes.iloc[-2]) if len(closes) > 1 else last_close
                pct_change = ((last_close - prev_close) / prev_close * 100.0) if prev_close else 0.0
                today_volume = float(volumes.iloc[-1])
                avg_volume = float(volumes.tail(10).mean())
                out[symbol] = {
                    "symbol": symbol,
                    "name": COMPANY_NAMES.get(symbol, symbol),
                    "price": last_close,
                    "open": float(sym_hist["Open"].dropna().iloc[-1]),
                    "high": float(sym_hist["High"].dropna().iloc[-1]),
                    "low": float(sym_hist["Low"].dropna().iloc[-1]),
                    "volume": today_volume,
                    "avg_volume": avg_volume,
                    "percent_change": pct_change,
                    "exchange": "",
                }
            except Exception:
                continue
        return out

    def fetch_news(self, symbol: str, company_name: str) -> List[Dict[str, str]]:
        raw_query = f'("{symbol}" OR "{company_name}") stock when:3d'
        query = urllib.parse.quote_plus(raw_query)
        url = f'https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en'
        try:
            r = self.session.get(url, timeout=20)
            r.raise_for_status()
            root = ET.fromstring(r.text)
            items = []
            for item in root.findall('.//item')[:10]:
                title = (item.findtext('title') or '').strip()
                link = (item.findtext('link') or '').strip()
                pub_date = (item.findtext('pubDate') or '').strip()
                if not title:
                    continue
                title_l = title.lower()
                company_token = company_name.split()[0].lower()
                if symbol.lower() not in title_l and company_token not in title_l:
                    continue
                items.append({"title": title, "link": link, "published": pub_date})
            return items[:8]
        except Exception:
            return []

    def score_news(self, headlines: List[Dict[str, str]]) -> Dict[str, Any]:
        score = 0
        matched = []
        positive_hits = 0
        negative_hits = 0

        for item in headlines:
            title = item["title"].lower()
            local_matches = []
            for kw, pts in POSITIVE_KEYWORDS.items():
                if kw in title:
                    score += pts
                    positive_hits += 1
                    local_matches.append(kw)
            for kw, pts in NEGATIVE_KEYWORDS.items():
                if kw in title:
                    score += pts
                    negative_hits += 1
                    local_matches.append(kw)
            if any(h in title for h in CATALYST_HINTS):
                local_matches.append("catalyst")
            if local_matches:
                matched.append({"title": item["title"], "keywords": sorted(set(local_matches))})

        if score > 2:
            sentiment = "bullish"
        elif score < -2:
            sentiment = "bearish"
        else:
            sentiment = "mixed"

        return {
            "catalyst_score": score,
            "sentiment": sentiment,
            "positive_hits": positive_hits,
            "negative_hits": negative_hits,
            "matched_headlines": matched[:5],
        }

    def liquidity_score(self, avg_volume: float, price: float) -> float:
        if avg_volume <= 0 or price <= 0:
            return 0.0
        vol_component = min(70.0, math.log10(max(avg_volume, 1)) * 10 - 20)
        price_component = 30.0 if 5 <= price <= 1000 else 0.0
        return round(max(0.0, vol_component + price_component), 2)

    def rank_score(self, quote: Dict[str, Any], news_score: Dict[str, Any]) -> float:
        catalyst_component = abs(news_score["catalyst_score"]) * 4.0
        headline_component = min(10.0, len(news_score["matched_headlines"]) * 1.5)
        momentum_component = min(8.0, abs(quote.get("percent_change", 0.0)) * 0.8)
        liquidity_component = self.liquidity_score(quote.get("avg_volume", 0.0), quote.get("price", 0.0)) * 0.15
        return round(catalyst_component + headline_component + momentum_component + liquidity_component, 2)

    def _meets_criteria(self, quote: Dict[str, Any]) -> bool:
        price = quote.get('price', 0)
        avg_volume = quote.get('avg_volume', 0)
        if price < self.criteria.get('min_price', 5):
            return False
        if price > self.criteria.get('max_price', 1000):
            return False
        if avg_volume < self.criteria.get('min_avg_volume', 500000):
            return False
        return True

    def scan_market(self) -> List[Dict[str, Any]]:
        quotes = self.fetch_quotes_bulk(self.universe)
        candidates: List[Dict[str, Any]] = []

        for symbol in self.universe:
            quote = quotes.get(symbol)
            if not quote or not self._meets_criteria(quote):
                continue
            company_name = quote.get("name", COMPANY_NAMES.get(symbol, symbol))
            headlines = self.fetch_news(symbol, company_name)
            news_score = self.score_news(headlines)
            liq_score = self.liquidity_score(quote.get("avg_volume", 0.0), quote.get("price", 0.0))
            total_score = self.rank_score(quote, news_score)
            catalyst_summary = self.summarize_catalyst(news_score, headlines)
            candidates.append({
                "symbol": symbol,
                "name": company_name,
                "price": quote.get("price"),
                "volume": quote.get("volume"),
                "avg_volume": quote.get("avg_volume"),
                "percent_change": quote.get("percent_change"),
                "exchange": quote.get("exchange"),
                "sentiment": news_score["sentiment"],
                "catalyst_score": news_score["catalyst_score"],
                "liquidity_score": liq_score,
                "rank_score": total_score,
                "news_hits": len(headlines),
                "catalyst": catalyst_summary,
                "top_headlines": [h["title"] for h in headlines[:3]],
                "matched_headlines": news_score["matched_headlines"],
            })
            time.sleep(0.15)

        candidates.sort(key=lambda x: (x["rank_score"], x["liquidity_score"]), reverse=True)
        return candidates[: self.max_candidates]

    def summarize_catalyst(self, news_score: Dict[str, Any], headlines: List[Dict[str, str]]) -> str:
        if not headlines:
            return "No major recent catalyst headlines found"
        if news_score["matched_headlines"]:
            first = news_score["matched_headlines"][0]
            kws = ", ".join(first["keywords"][:3])
            return f"Headline-driven setup ({kws}): {first['title']}"
        return headlines[0]["title"]

    def save_candidates(self, candidates: List[Dict]):
        os.makedirs(os.path.dirname(self.candidates_path), exist_ok=True)
        output = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "count": len(candidates),
            "scanner": "alpha_radar_v2_news_ranked",
            "universe_size": len(self.universe),
            "candidates": candidates,
        }
        with open(self.candidates_path, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2)
        print(f"✓ Saved {len(candidates)} candidates to {self.candidates_path}")

    def run(self):
        print(f"\n{'='*60}")
        print(f"Alpha Radar Scan - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
        src = "fmp" if (market_data and getattr(market_data, "provider_name", lambda: "yfinance")() == "fmp") else "yfinance"
        print(f"Universe size: {len(self.universe)} | Quote source: {src} | News: Google RSS")
        print(f"{'='*60}\n")

        candidates = self.scan_market()
        print(f"Found {len(candidates)} ranked candidates:")
        for c in candidates:
            print(
                f"  • {c['symbol']}: ${c['price']:.2f} | Δ {c['percent_change']:.2f}% | "
                f"Score {c['rank_score']:.2f} | {c['sentiment']} | {c['catalyst']}"
            )
        self.save_candidates(candidates)
        print(f"\n{'='*60}\n")


if __name__ == "__main__":
    AlphaRadar().run()
