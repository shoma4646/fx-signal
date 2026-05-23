from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Signal:
    direction: Direction
    pair: str
    price: float
    timestamp: datetime
    reason: str

    def to_line_message(self) -> str:
        emoji = "↑" if self.direction == Direction.BUY else "↓"
        label = "買い" if self.direction == Direction.BUY else "売り"
        ts = self.timestamp.strftime("%Y/%m/%d %H:%M")
        return (
            f"\n{emoji} {label}シグナル [{self.pair}]\n"
            f"価格: {self.price:.3f} 円\n"
            f"根拠: {self.reason}\n"
            f"時刻: {ts}"
        )
