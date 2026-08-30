#!/usr/bin/env python3
"""Crypto arbitrage trading bot simulator (ETH/USD, two synthetic venues)."""

import argparse
import logging
import random
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

DB_PATH = "crypto_arb_trading_cli.db"
LOG_PATH = "crypto_arb_trading_cli.log"
SAST = timezone(timedelta(hours=2))

TRADING_START = (9, 0)
TRADING_END = (16, 50)

VENUE_A = "VenueA"
VENUE_B = "VenueB"

logger = logging.getLogger("crypto_arb")


def setup_logging() -> None:
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(LOG_PATH)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console)


def setup_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            buy_venue TEXT NOT NULL,
            sell_venue TEXT NOT NULL,
            buy_price REAL NOT NULL,
            sell_price REAL NOT NULL,
            eth_amount REAL NOT NULL,
            usd_spent REAL NOT NULL,
            usd_received REAL NOT NULL,
            fees_usd REAL NOT NULL,
            profit_usd REAL NOT NULL,
            capital_after REAL NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def is_trading_window(ts: datetime) -> bool:
    if ts.weekday() >= 5:
        return False
    start = ts.replace(hour=TRADING_START[0], minute=TRADING_START[1], second=0, microsecond=0)
    end = ts.replace(hour=TRADING_END[0], minute=TRADING_END[1], second=0, microsecond=0)
    return start <= ts <= end


def next_window_start(ts: datetime) -> datetime:
    candidate = ts.replace(hour=TRADING_START[0], minute=TRADING_START[1], second=0, microsecond=0)
    if ts > candidate.replace(hour=TRADING_END[0], minute=TRADING_END[1]):
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


class PriceFeed:
    """Shared 'true' ETH/USD price with independent per-venue noise, so the two
    venues occasionally drift apart enough to create an arbitrage opportunity."""

    def __init__(self, start_price: float, venue_vol: float, shock_prob: float, shock_size: float):
        self.true_price = start_price
        self.venue_vol = venue_vol
        self.shock_prob = shock_prob
        self.shock_size = shock_size

    def step(self) -> None:
        self.true_price *= 1 + random.gauss(0, 0.0008)
        self.true_price = max(self.true_price, 1.0)

    def quote(self, venue: str) -> float:
        noise = random.gauss(0, self.venue_vol)
        if random.random() < self.shock_prob:
            noise += random.choice([-1, 1]) * self.shock_size
        return round(self.true_price * (1 + noise), 2)


@dataclass
class Portfolio:
    capital_usd: float
    trades: list = field(default_factory=list)

    @property
    def total_profit(self) -> float:
        return sum(t["profit_usd"] for t in self.trades)


def buy(price: float, usd_amount: float, fee_pct: float) -> tuple:
    """Spend usd_amount at price, minus a taker fee. Returns (eth_bought, fee_usd)."""
    fee_usd = usd_amount * fee_pct
    eth_bought = (usd_amount - fee_usd) / price
    return eth_bought, fee_usd


def sell(price: float, eth_amount: float, fee_pct: float) -> tuple:
    """Sell eth_amount at price, minus a taker fee. Returns (usd_received, fee_usd)."""
    gross_usd = eth_amount * price
    fee_usd = gross_usd * fee_pct
    return gross_usd - fee_usd, fee_usd


def try_arbitrage(
    ts: datetime,
    price_a: float,
    price_b: float,
    portfolio: Portfolio,
    conn: sqlite3.Connection,
    fee_pct: float,
    min_spread_pct: float,
    position_fraction: float,
) -> None:
    if price_a < price_b:
        buy_venue, sell_venue, buy_price, sell_price = VENUE_A, VENUE_B, price_a, price_b
    else:
        buy_venue, sell_venue, buy_price, sell_price = VENUE_B, VENUE_A, price_b, price_a

    spread_pct = (sell_price - buy_price) / buy_price
    if spread_pct <= min_spread_pct:
        return

    trade_usd = min(portfolio.capital_usd * position_fraction, portfolio.capital_usd)
    if trade_usd <= 0:
        return

    eth_bought, buy_fee = buy(buy_price, trade_usd, fee_pct)
    usd_received, sell_fee = sell(sell_price, eth_bought, fee_pct)
    profit_usd = usd_received - trade_usd
    portfolio.capital_usd += profit_usd

    record = {
        "timestamp": ts.isoformat(),
        "buy_venue": buy_venue,
        "sell_venue": sell_venue,
        "buy_price": buy_price,
        "sell_price": sell_price,
        "eth_amount": round(eth_bought, 6),
        "usd_spent": round(trade_usd, 2),
        "usd_received": round(usd_received, 2),
        "fees_usd": round(buy_fee + sell_fee, 2),
        "profit_usd": round(profit_usd, 2),
        "capital_after": round(portfolio.capital_usd, 2),
    }
    portfolio.trades.append(record)

    conn.execute(
        """INSERT INTO trades
           (timestamp, buy_venue, sell_venue, buy_price, sell_price, eth_amount,
            usd_spent, usd_received, fees_usd, profit_usd, capital_after)
           VALUES (:timestamp, :buy_venue, :sell_venue, :buy_price, :sell_price,
                   :eth_amount, :usd_spent, :usd_received, :fees_usd, :profit_usd,
                   :capital_after)""",
        record,
    )
    conn.commit()

    logger.info(
        "TRADE spread=%.3f%% buy %.6f ETH on %s@%.2f -> sell on %s@%.2f "
        "| profit=$%.2f capital=$%.2f",
        spread_pct * 100,
        record["eth_amount"],
        buy_venue,
        buy_price,
        sell_venue,
        sell_price,
        profit_usd,
        portfolio.capital_usd,
    )


def print_summary(portfolio: Portfolio, starting_capital: float) -> None:
    n = len(portfolio.trades)
    wins = sum(1 for t in portfolio.trades if t["profit_usd"] > 0)
    losses = n - wins
    total_profit = portfolio.total_profit
    return_pct = (total_profit / starting_capital) * 100 if starting_capital else 0.0

    lines = [
        "",
        "=" * 50,
        "TRADING SUMMARY",
        "=" * 50,
        f"Starting capital:  ${starting_capital:,.2f}",
        f"Ending capital:    ${portfolio.capital_usd:,.2f}",
        f"Total P/L:         ${total_profit:,.2f} ({return_pct:+.3f}%)",
        f"Trades executed:   {n}  (wins: {wins}, losses: {losses})",
        "=" * 50,
    ]
    for line in lines:
        logger.info(line)


def run_simulation(args: argparse.Namespace) -> None:
    random.seed(args.seed)
    conn = setup_db()
    portfolio = Portfolio(capital_usd=args.capital)
    feed = PriceFeed(
        start_price=args.start_price,
        venue_vol=args.venue_vol,
        shock_prob=args.shock_prob,
        shock_size=args.shock_size,
    )

    now = datetime.now(SAST)
    ts = now if is_trading_window(now) else next_window_start(now)
    end_of_day = ts.replace(hour=TRADING_END[0], minute=TRADING_END[1], second=0, microsecond=0)

    logger.info(
        "Starting simulation: capital=$%.2f window=%s -> %s",
        args.capital,
        ts.isoformat(),
        end_of_day.isoformat(),
    )

    try:
        while ts <= end_of_day:
            feed.step()
            price_a = feed.quote(VENUE_A)
            price_b = feed.quote(VENUE_B)
            try_arbitrage(
                ts,
                price_a,
                price_b,
                portfolio,
                conn,
                fee_pct=args.fee,
                min_spread_pct=args.threshold,
                position_fraction=args.position_fraction,
            )
            time.sleep(args.tick_delay)
            ts += timedelta(minutes=args.tick_minutes)
    except KeyboardInterrupt:
        logger.info("Simulation interrupted by user.")
    finally:
        print_summary(portfolio, args.capital)
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulated ETH/USD cross-venue arbitrage bot.")
    parser.add_argument("--capital", type=float, default=1000.0, help="Starting USD capital.")
    parser.add_argument("--start-price", type=float, default=3000.0, help="Starting ETH/USD true price.")
    parser.add_argument("--fee", type=float, default=0.001, help="Per-leg taker fee, e.g. 0.001 = 0.1%%.")
    parser.add_argument("--threshold", type=float, default=0.004, help="Min spread to trade, e.g. 0.004 = 0.4%%.")
    parser.add_argument("--position-fraction", type=float, default=0.2, help="Fraction of capital risked per trade.")
    parser.add_argument("--venue-vol", type=float, default=0.0015, help="Per-venue price noise std dev.")
    parser.add_argument("--shock-prob", type=float, default=0.05, help="Probability of a larger per-tick dislocation.")
    parser.add_argument("--shock-size", type=float, default=0.01, help="Magnitude of a dislocation shock.")
    parser.add_argument("--tick-minutes", type=int, default=1, help="Simulated minutes advanced per tick.")
    parser.add_argument("--tick-delay", type=float, default=0.02, help="Real seconds to sleep between ticks.")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducible runs.")
    return parser.parse_args()


def main() -> None:
    setup_logging()
    args = parse_args()
    run_simulation(args)


if __name__ == "__main__":
    main()
