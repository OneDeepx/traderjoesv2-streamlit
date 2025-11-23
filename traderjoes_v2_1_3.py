#!/usr/bin/env python3
"""
TraderJoes v2.1.3 - Professional Crypto Futures Trading Bot
P&L DISPLAY FIXED + REASONABLE STOPS + MTF + LEVERAGE
WITH UNIFIED MARKET DATA SERVICE

Created by: OneDeepx
Repository: github.com/OneDeepx/JT345

MAJOR IMPROVEMENTS IN v2.1.3:
- FIXED: P&L display shows both $ and % in closed trades
- FIXED: Total P&L properly includes all losses
- FIXED: Reasonable stop losses (2-5% price movement)
- FIXED: No duplicate trades on same symbol
- CRITICAL: Multi-timeframe analysis (1m to 1d)
- 3-5x leverage based on confidence
- Strategy-specific stop distances
- Extra room for volatile coins
- Proper percentage-based trailing stop
- Continuous trading operation
- NEW: Unified Market Data Service for clean networking
"""

import sys
import json
import asyncio
import logging
import traceback
import time
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
import threading
from collections import deque, defaultdict
from queue import Queue
import csv
import hashlib
from dataclasses import dataclass, field
from enum import Enum

# Import the unified market data service
from market_data_service import get_market_data_service, MarketDataService


# PyQt imports

# Import bot modules - using MarketDataService
from trading_engine import TradingEngine, TradingStrategyAnalyzer
# from binance_api import BinanceFuturesClient - removed (replaced by MarketDataService)
from validation_engine import ValidationEngine, RealisticExecutionSimulator


class TimeFrame(Enum):
    """Available timeframes for analysis"""
    M1 = '1m'    # Scalping
    M3 = '3m'    # Scalping
    M5 = '5m'    # Short-term
    M15 = '15m'  # Intraday
    M30 = '30m'  # Intraday
    H1 = '1h'    # Swing
    H2 = '2h'    # Swing
    H4 = '4h'    # Position
    H8 = '8h'    # Position
    H12 = '12h'  # Position
    D1 = '1d'    # Long-term


class MultiTimeframeAnalyzer:
    """Analyzes multiple timeframes to determine best strategy"""
    
    def __init__(self):
        self.timeframes = [
            TimeFrame.M1, TimeFrame.M5, TimeFrame.M15,
            TimeFrame.M30, TimeFrame.H1, TimeFrame.H4,
            TimeFrame.D1
        ]
        self.timeframe_data = {}
        self.timeframe_trends = {}
        self.timeframe_momentum = {}
        self.timeframe_volatility = {}
        
    async def analyze_all_timeframes(self, symbol: str, market_service: MarketDataService) -> Dict:
        """Analyze all timeframes for a symbol using Market Data Service"""
        analysis = {
            'symbol': symbol,
            'timestamp': datetime.now(),
            'timeframes': {},
            'alignment': None,
            'recommended_strategy': None,
            'confidence': 0,
            'details': {}
        }
        
        # Use specific timeframes available in Market Data Service
        available_timeframes = ['1m', '5m', '15m']  # These are subscribed in the service
        
        for tf_str in available_timeframes:
            try:
                # Get candles from Market Data Service
                df = market_service.get_candles(symbol, tf_str, 200)
                if not df.empty:
                    # Create TimeFrame enum value for analysis
                    tf = TimeFrame.M1 if tf_str == '1m' else TimeFrame.M5 if tf_str == '5m' else TimeFrame.M15
                    tf_analysis = self.analyze_timeframe(df, tf)
                    analysis['timeframes'][tf_str] = tf_analysis
                    self.timeframe_data[f"{symbol}_{tf_str}"] = df
            except Exception as e:
                logger.error(f"Error analyzing {symbol} {tf_str}: {e}")
        
        # Determine alignment and strategy
        analysis['alignment'] = self.calculate_alignment(analysis['timeframes'])
        analysis['recommended_strategy'] = self.determine_strategy(analysis)
        analysis['confidence'] = self.calculate_confidence(analysis)
        
        return analysis
    
    def analyze_timeframe(self, df: pd.DataFrame, timeframe: TimeFrame) -> Dict:
        """Analyze a single timeframe"""
        if df.empty or len(df) < 20:
            return {'trend': 'NEUTRAL', 'momentum': 0, 'volatility': 0}
        
        analysis = {}
        
        # Calculate trend (SMA comparison)
        df['SMA_20'] = df['close'].rolling(window=20).mean()
        df['SMA_50'] = df['close'].rolling(window=min(50, len(df))).mean()
        
        current_price = df['close'].iloc[-1]
        sma_20 = df['SMA_20'].iloc[-1]
        sma_50 = df['SMA_50'].iloc[-1] if len(df) >= 50 else sma_20
        
        # Determine trend
        if current_price > sma_20 > sma_50:
            analysis['trend'] = 'STRONG_UP'
        elif current_price > sma_20:
            analysis['trend'] = 'UP'
        elif current_price < sma_20 < sma_50:
            analysis['trend'] = 'STRONG_DOWN'
        elif current_price < sma_20:
            analysis['trend'] = 'DOWN'
        else:
            analysis['trend'] = 'NEUTRAL'
        
        # Calculate momentum (RSI)
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        analysis['momentum'] = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50
        
        # Calculate volatility (ATR as percentage)
        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift())
        low_close = abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = ranges.max(axis=1)
        atr = true_range.rolling(14).mean()
        analysis['volatility'] = (atr.iloc[-1] / current_price * 100) if not pd.isna(atr.iloc[-1]) else 0
        
        # Support and resistance
        analysis['support'] = df['low'].rolling(20).min().iloc[-1]
        analysis['resistance'] = df['high'].rolling(20).max().iloc[-1]
        
        # Volume analysis
        analysis['volume_trend'] = 'HIGH' if df['volume'].iloc[-1] > df['volume'].mean() else 'LOW'
        
        # MACD for additional confirmation
        exp1 = df['close'].ewm(span=12, adjust=False).mean()
        exp2 = df['close'].ewm(span=26, adjust=False).mean()
        analysis['macd'] = (exp1 - exp2).iloc[-1]
        analysis['macd_signal'] = 'BUY' if analysis['macd'] > 0 else 'SELL'
        
        return analysis
    
    def calculate_alignment(self, timeframes: Dict) -> str:
        """Calculate trend alignment across timeframes"""
        if not timeframes:
            return 'UNKNOWN'
        
        trends = []
        weights = {
            '1m': 0.5, '3m': 0.5, '5m': 1,
            '15m': 2, '30m': 2, '1h': 3,
            '2h': 3, '4h': 4, '8h': 4,
            '12h': 4, '1d': 5
        }
        
        weighted_score = 0
        total_weight = 0
        
        for tf, analysis in timeframes.items():
            if 'trend' in analysis:
                weight = weights.get(tf, 1)
                total_weight += weight
                
                if analysis['trend'] == 'STRONG_UP':
                    weighted_score += weight * 2
                elif analysis['trend'] == 'UP':
                    weighted_score += weight * 1
                elif analysis['trend'] == 'DOWN':
                    weighted_score += weight * -1
                elif analysis['trend'] == 'STRONG_DOWN':
                    weighted_score += weight * -2
        
        if total_weight == 0:
            return 'NEUTRAL'
        
        alignment_score = weighted_score / total_weight
        
        if alignment_score > 1.5:
            return 'STRONG_BULLISH'
        elif alignment_score > 0.5:
            return 'BULLISH'
        elif alignment_score < -1.5:
            return 'STRONG_BEARISH'
        elif alignment_score < -0.5:
            return 'BEARISH'
        else:
            return 'MIXED'
    
    def determine_strategy(self, analysis: Dict) -> str:
        """Determine best strategy based on multi-timeframe analysis"""
        alignment = analysis['alignment']
        timeframes = analysis['timeframes']
        
        # Get short-term and long-term analysis
        short_term_vol = 0
        long_term_trend = 'NEUTRAL'
        
        if '5m' in timeframes:
            short_term_vol = timeframes['5m'].get('volatility', 0)
        
        if '4h' in timeframes:
            long_term_trend = timeframes['4h'].get('trend', 'NEUTRAL')
        elif '1h' in timeframes:
            long_term_trend = timeframes['1h'].get('trend', 'NEUTRAL')
        
        # Strategy selection logic
        if alignment in ['STRONG_BULLISH', 'STRONG_BEARISH']:
            # Strong trend alignment - use trend following
            return 'TREND_FOLLOWING'
        
        elif short_term_vol > 2.0:
            # High volatility - use scalping
            if '1m' in timeframes and timeframes['1m'].get('momentum', 50) != 50:
                return 'SCALPING'
            else:
                return 'MOMENTUM'
        
        elif alignment == 'MIXED':
            # Mixed signals - look for mean reversion
            if '15m' in timeframes:
                rsi = timeframes['15m'].get('momentum', 50)
                if rsi > 70 or rsi < 30:
                    return 'MEAN_REVERSION'
            return 'BREAKOUT'
        
        elif alignment in ['BULLISH', 'BEARISH']:
            # Moderate trend - use momentum
            return 'MOMENTUM'
        
        else:
            # Neutral market - look for breakouts
            return 'BREAKOUT'
    
    def calculate_confidence(self, analysis: Dict) -> float:
        """Calculate confidence based on timeframe alignment"""
        alignment = analysis['alignment']
        base_confidence = 0.5
        
        # Alignment bonus
        if alignment in ['STRONG_BULLISH', 'STRONG_BEARISH']:
            base_confidence += 0.25
        elif alignment in ['BULLISH', 'BEARISH']:
            base_confidence += 0.15
        elif alignment == 'MIXED':
            base_confidence -= 0.10
        
        # Check for confluence across multiple timeframes
        timeframes = analysis['timeframes']
        confirming_timeframes = 0
        
        for tf, tf_analysis in timeframes.items():
            if tf in ['15m', '1h', '4h']:  # Key timeframes
                trend = tf_analysis.get('trend', 'NEUTRAL')
                if (alignment == 'BULLISH' and 'UP' in trend) or \
                   (alignment == 'BEARISH' and 'DOWN' in trend):
                    confirming_timeframes += 1
        
        base_confidence += confirming_timeframes * 0.05
        
        # Cap confidence
        return min(max(base_confidence, 0.3), 0.85)
    
    def get_entry_timeframe(self, strategy: str) -> str:
        """Get the best timeframe for entry based on strategy"""
        strategy_timeframes = {
            'SCALPING': '1m',
            'MOMENTUM': '5m',
            'MEAN_REVERSION': '15m',
            'TREND_FOLLOWING': '1h',
            'BREAKOUT': '15m'
        }
        return strategy_timeframes.get(strategy, '5m')


# PyQt imports

# Duplicate imports removed - using imports from top of file

@dataclass
class TradeV2:
    """Enhanced trade class with MTF tracking and LEVERAGE"""
    id: str
    symbol: str
    strategy: str
    side: str
    entry_price: float
    position_size: float  # Units of crypto
    position_size_usd: float  # LEVERAGED position value
    margin_used: float  # Actual balance used (1% of balance)
    leverage: float  # 3-5x based on confidence
    stop_loss: float
    take_profit: float
    entry_time: datetime
    current_price: float = 0
    highest_price: float = 0  # For tracking
    lowest_price: float = float('inf')  # For tracking
    max_pnl_percent: float = 0  # Track maximum P&L PERCENTAGE for trailing
    pnl: float = 0
    pnl_percent: float = 0  # Current P&L percentage
    fees: float = 0
    status: str = 'OPEN'
    validation_hash: str = ''  # For historical validation
    mtf_alignment: str = 'UNKNOWN'  # Multi-timeframe alignment
    mtf_confidence: float = 0  # MTF-based confidence
    exit_price: float = None
    exit_time: datetime = None
    close_reason: str = None
    duration: float = 0
    
    def calculate_pnl(self, current_price: float) -> float:
        """Calculate current P&L with LEVERAGE"""
        if self.side == 'BUY':
            price_change = current_price - self.entry_price
        else:
            price_change = self.entry_price - current_price
        
        # P&L is calculated on the LEVERAGED position
        return (price_change * self.position_size) - self.fees
    
    def calculate_pnl_percent(self, current_price: float) -> float:
        """Calculate P&L as percentage of MARGIN used"""
        pnl = self.calculate_pnl(current_price)
        # Percentage is based on actual margin used, not leveraged position
        return (pnl / self.margin_used) * 100


class MLStrategyOptimizer:
    """Enhanced ML with aggressive learning for continuous improvement"""
    
    def __init__(self):
        self.performance_history = deque(maxlen=100)
        self.strategy_weights = {
            'MOMENTUM': 1.0,
            'TREND_FOLLOWING': 1.0,
            'MEAN_REVERSION': 1.0,
            'SCALPING': 1.0,
            'BREAKOUT': 1.0
        }
        self.learning_rate = 0.02  # Doubled for faster learning
        self.min_samples = 10  # Reduced for quicker adaptation
        self.loss_multiplier = 1.5  # Learn more from losses
        self.total_trades = 0
        self.strategy_performance = defaultdict(lambda: {'wins': 0, 'losses': 0, 'total_pnl': 0})
        
    def update_weights(self, strategy: str, profit: bool, pnl_amount: float = 0):
        """Aggressively adjust strategy weights based on performance"""
        self.total_trades += 1
        
        # Track detailed performance
        if profit:
            self.strategy_weights[strategy] = min(2.5, self.strategy_weights[strategy] * 1.08)
            self.strategy_performance[strategy]['wins'] += 1
            self.strategy_performance[strategy]['total_pnl'] += pnl_amount
        else:
            # Learn MORE from losses for improvement
            self.strategy_weights[strategy] = max(0.05, self.strategy_weights[strategy] * 0.90)
            self.strategy_performance[strategy]['losses'] += 1
            self.strategy_performance[strategy]['total_pnl'] += pnl_amount
        
        # Normalize weights
        total = sum(self.strategy_weights.values())
        for key in self.strategy_weights:
            self.strategy_weights[key] /= total
        
        # Log learning progress
        logger.info(f"ML Learning #{self.total_trades}: {strategy} {'WIN' if profit else 'LOSS'} ${pnl_amount:.2f}")
        logger.info(f"Updated weights: {self.strategy_weights}")
        
        # Every 10 trades, analyze and report
        if self.total_trades % 10 == 0:
            self.analyze_performance()
    
    def analyze_performance(self):
        """Analyze and log performance for continuous improvement"""
        logger.info("=" * 50)
        logger.info("ML PERFORMANCE ANALYSIS - Data Collection Active")
        for strategy, perf in self.strategy_performance.items():
            total = perf['wins'] + perf['losses']
            if total > 0:
                win_rate = (perf['wins'] / total) * 100
                logger.info(f"{strategy}: {perf['wins']}/{total} wins ({win_rate:.1f}%) | P&L: ${perf['total_pnl']:.2f}")
        logger.info(f"TRADING CONTINUES - Gathering more data...")
        logger.info("=" * 50)
    
    def get_best_strategy(self, market_conditions: dict) -> Tuple[str, float]:
        """Select best strategy based on learned weights"""
        # Add some randomization to explore different strategies
        import random
        if random.random() < 0.15:  # 15% exploration rate
            # Occasionally try underperforming strategies for data
            strategy = random.choice(list(self.strategy_weights.keys()))
            return strategy, 0.60  # Lower confidence for exploration
        
        # Otherwise use best performing
        best_strategy = max(self.strategy_weights.items(), key=lambda x: x[1])
        return best_strategy[0], best_strategy[1]


class RiskManagerV2:
    """Enhanced risk management with LEVERAGE and continuous trading"""
    
    def __init__(self, initial_balance: float = 10000):
        self.initial_balance = initial_balance
        self.available_balance = initial_balance  # Track available balance
        self.risk_per_trade = 0.01  # Strict 1% risk (margin used)
        self.max_positions = 10  # Maximum 10 trades
        self.trailing_stop_percentage = 0.30  # 0.30 PERCENTAGE POINTS for trailing
        self.min_leverage = 3.0  # Minimum leverage
        self.max_leverage = 5.0  # Maximum leverage
        self.total_losses = 0  # Track but don't limit
        self.consecutive_losses = 0  # Track but don't suspend
        self.allow_trading = True  # ALWAYS TRUE - Never suspend
        
    def calculate_leverage(self, confidence: float, mtf_alignment: str = None) -> float:
        """Calculate leverage based on confidence (3x to 5x)"""
        # Base calculation: map confidence 0.4-0.8 to leverage 3-5
        if confidence < 0.4:
            confidence = 0.4
        elif confidence > 0.8:
            confidence = 0.8
        
        # Linear mapping: 0.4 conf = 3x, 0.8 conf = 5x
        leverage = 3.0 + ((confidence - 0.4) / 0.4) * 2.0
        
        # Boost for strong MTF alignment
        if mtf_alignment in ['STRONG_BULLISH', 'STRONG_BEARISH']:
            leverage += 0.5
        
        # Ensure within bounds
        leverage = max(self.min_leverage, min(self.max_leverage, leverage))
        
        return round(leverage, 1)
    
    def calculate_position_size_with_leverage(self, balance: float, leverage: float) -> tuple:
        """Calculate margin used and leveraged position size
        Returns: (margin_used, leveraged_position_usd)"""
        # Margin used is 1% of available balance
        margin_used = balance * self.risk_per_trade
        
        # Leveraged position is margin * leverage
        leveraged_position = margin_used * leverage
        
        # Safety check for very low balance
        if balance < 100:
            margin_used = min(10, balance * self.risk_per_trade)
            leveraged_position = margin_used * leverage
        
        return margin_used, leveraged_position
    
    def calculate_position_units(self, leveraged_position_usd: float, entry_price: float) -> float:
        """Calculate position size in units based on leveraged position"""
        if entry_price > 0:
            return leveraged_position_usd / entry_price
        return 0
    
    def update_available_balance(self, amount: float, operation: str = 'subtract'):
        """Update available balance when trades open/close
        Note: 'amount' is the MARGIN used, not the leveraged position"""
        if operation == 'subtract':
            self.available_balance -= amount
        else:  # add
            self.available_balance += amount
        
        # Never let balance go negative (use minimum trading amount)
        if self.available_balance < 10:
            logger.warning(f"Low balance: ${self.available_balance:.2f} - Continuing with minimum positions")
        
        logger.info(f"Available balance: ${self.available_balance:.2f} | Total losses: ${self.total_losses:.2f}")
    
    def can_trade(self) -> bool:
        """Always returns True - NO TRADING SUSPENSIONS"""
        return True  # ALWAYS allow trading for data collection
    
    def record_trade_result(self, pnl: float):
        """Record trade result but NEVER suspend trading"""
        if pnl < 0:
            self.total_losses += abs(pnl)
            self.consecutive_losses += 1
            logger.info(f"Loss recorded: ${abs(pnl):.2f} | Consecutive: {self.consecutive_losses} | Total: ${self.total_losses:.2f}")
        else:
            self.consecutive_losses = 0  # Reset on win
            logger.info(f"Win recorded: ${pnl:.2f} | Losses reset")
        
        # Log statistics but NEVER suspend
        logger.info(f"Trading continues - Data collection active")


class HistoricalValidator:
    """Validate trades with historical timestamp verification"""
    
    def __init__(self):
        self.validated_trades = {}
        self.validation_log = []
        
    def create_validation_hash(self, trade: TradeV2) -> str:
        """Create unique hash for trade validation"""
        data = f"{trade.symbol}_{trade.entry_price}_{trade.entry_time.isoformat()}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]
    
    def validate_entry(self, trade: TradeV2, market_data: pd.DataFrame) -> dict:
        """Validate trade entry with historical data"""
        validation = {
            'timestamp': trade.entry_time.isoformat(),
            'symbol': trade.symbol,
            'entry_price': trade.entry_price,
            'market_price_at_entry': 0,
            'price_match': False,
            'time_verified': False,
            'hash': self.create_validation_hash(trade)
        }
        
        try:
            # Find closest timestamp in market data
            if not market_data.empty and 'close' in market_data.columns:
                # Get price at entry time
                closest_idx = market_data.index.get_indexer([trade.entry_time], method='nearest')[0]
                if closest_idx >= 0 and closest_idx < len(market_data):
                    market_price = market_data.iloc[closest_idx]['close']
                    validation['market_price_at_entry'] = market_price
                    
                    # Check if prices match within 0.1%
                    price_diff = abs(market_price - trade.entry_price) / trade.entry_price
                    validation['price_match'] = price_diff < 0.001
                    
                    # Verify timestamp is reasonable (within 1 minute)
                    time_diff = abs((market_data.index[closest_idx] - trade.entry_time).total_seconds())
                    validation['time_verified'] = time_diff < 60
        
        except Exception as e:
            logger.error(f"Validation error: {e}")
        
        self.validation_log.append(validation)
        return validation

