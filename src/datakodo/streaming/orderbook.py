"""Maintain a real-time L2 order book from snapshot + delta updates.

Receives an initial snapshot and then applies incremental deltas
(additions, updates, deletions) to keep a local book current.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from datakodo.core.schemas import OrderBook, OrderBookLevel


@dataclass
class OrderBookMaintainer:
    """Tracks an L2 order book in real time from snapshot + deltas.

    A full snapshot is stored once; subsequent delta messages patch
    individual price levels (insert, update, or remove when size hits
    zero).
    """

    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)

    def apply_snapshot(self, snapshot: OrderBook) -> None:
        """Replace the entire book with a fresh snapshot."""
        self.bids = {level.price: level.size for level in snapshot.bids}
        self.asks = {level.price: level.size for level in snapshot.asks}

    def apply_delta(
        self,
        side: str,
        price: float,
        size: float,
    ) -> None:
        """Patch a single price level from a delta update.

        A *size* of 0 removes the level. Otherwise the level is inserted
        or updated at *price*.
        """
        book = self.bids if side == "bid" else self.asks

        if size == 0.0:
            book.pop(price, None)
        else:
            book[price] = size

    def snapshot(self) -> OrderBook:
        """Return the current book as a canonical OrderBook snapshot."""
        return OrderBook(
            timestamp=datetime.now(UTC),
            bids=[OrderBookLevel(price=p, size=s) for p, s in self.bids.items()],
            asks=[OrderBookLevel(price=p, size=s) for p, s in self.asks.items()],
        )
