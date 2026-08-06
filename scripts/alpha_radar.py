#!/usr/bin/env python3
"""
Alpha Radar - Stock Scanner (Farzad Pattern)
Scans market for liquid US stocks with catalysts, news, volume, sentiment
Does NOT trade - only creates candidate list
"""

import json
import os
from datetime import datetime, timezone
from typing import List, Dict, Any

class AlphaRadar:
    def __init__(self, config_path: str = "./config/autonomy_config.json"):
        with open(config_path, 'r') as f:
            self.config = json.load(f)
        
        self.criteria = self.config.get('stock_criteria', {})
        self.candidates_path = "./data/candidates.json"
    
    def scan_market(self) -> List[Dict[str, Any]]:
        """
        Scan market for candidates.
        
        In PAPER/LIVE mode, this would:
        1. Query financial APIs for volume movers
        2. Check news/Twitter for catalysts
        3. Filter by liquidity/spread criteria
        4. Rank by sentiment/momentum
        
        For now: Returns hardcoded high-liquidity candidates
        """
        # TODO: Integrate with financial APIs
        # - Polygon.io for market data
        # - Alpha Vantage for fundamentals
        # - News API for catalysts
        # - Twitter/X for sentiment
        
        candidates = [
            {
                "symbol": "TSLA",
                "price": 321.55,
                "volume": 27820813,
                "avg_volume": 39677916,
                "spread_pct": 0.03,
                "catalyst": "EV delivery numbers expected",
                "sentiment": "mixed",
                "liquidity_score": 95
            },
            {
                "symbol": "NVDA",
                "price": 125.50,
                "volume": 45000000,
                "avg_volume": 42000000,
                "spread_pct": 0.02,
                "catalyst": "AI chip demand strong",
                "sentiment": "bullish",
                "liquidity_score": 98
            },
            {
                "symbol": "AAPL",
                "price": 227.00,
                "volume": 35000000,
                "avg_volume": 40000000,
                "spread_pct": 0.01,
                "catalyst": "New product cycle",
                "sentiment": "neutral",
                "liquidity_score": 99
            },
            {
                "symbol": "AMD",
                "price": 165.00,
                "volume": 30000000,
                "avg_volume": 28000000,
                "spread_pct": 0.04,
                "catalyst": "Server chip wins",
                "sentiment": "bullish",
                "liquidity_score": 92
            },
            {
                "symbol": "MSFT",
                "price": 420.00,
                "volume": 25000000,
                "avg_volume": 24000000,
                "spread_pct": 0.01,
                "catalyst": "Azure AI growth",
                "sentiment": "bullish",
                "liquidity_score": 97
            }
        ]
        
        # Filter by criteria
        qualified = []
        for candidate in candidates:
            if self._meets_criteria(candidate):
                qualified.append(candidate)
        
        return qualified
    
    def _meets_criteria(self, candidate: Dict) -> bool:
        """Check if candidate meets trading criteria"""
        if candidate['price'] < self.criteria.get('min_price', 5):
            return False
        if candidate['price'] > self.criteria.get('max_price', 1000):
            return False
        if candidate['avg_volume'] < self.criteria.get('min_avg_volume', 500000):
            return False
        if candidate['spread_pct'] > self.criteria.get('max_spread_pct', 0.5):
            return False
        return True
    
    def save_candidates(self, candidates: List[Dict]):
        """Save candidates to file"""
        os.makedirs(os.path.dirname(self.candidates_path), exist_ok=True)
        
        output = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "count": len(candidates),
            "candidates": candidates
        }
        
        with open(self.candidates_path, 'w') as f:
            json.dump(output, f, indent=2)
        
        print(f"✓ Saved {len(candidates)} candidates to {self.candidates_path}")
    
    def run(self):
        """Main entry point"""
        print(f"\n{'='*60}")
        print(f"Alpha Radar Scan - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"{'='*60}\n")
        
        candidates = self.scan_market()
        
        print(f"Found {len(candidates)} qualified candidates:")
        for c in candidates:
            print(f"  • {c['symbol']}: ${c['price']:.2f} | Vol: {c['volume']:,} | {c['catalyst']}")
        
        self.save_candidates(candidates)
        print(f"\n{'='*60}\n")


if __name__ == "__main__":
    radar = AlphaRadar()
    radar.run()
