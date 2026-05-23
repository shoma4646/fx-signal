from dataclasses import dataclass, field
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
    tp: float | None = field(default=None)
    sl: float | None = field(default=None)

    def to_notification(self) -> tuple[str, str]:
        """(title, body) を返す。Mac通知用。"""
        is_buy = self.direction == Direction.BUY
        arrow = "↑" if is_buy else "↓"
        label = "買い" if is_buy else "売り"
        title = f"{arrow} {label}シグナル [{self.pair}]"

        ts = self.timestamp.strftime("%Y/%m/%d %H:%M")
        lines = [f"価格:  {self.price:.3f} 円"]

        if self.tp is not None:
            diff_tp = self.tp - self.price
            lines.append(f"利確:  {self.tp:.3f} 円  ({diff_tp:+.3f})")
        if self.sl is not None:
            diff_sl = self.sl - self.price
            lines.append(f"損切:  {self.sl:.3f} 円  ({diff_sl:+.3f})")

        lines.append(f"根拠: {self.reason}")
        lines.append(f"時刻: {ts}")

        return title, "\n".join(lines)
