"""fx-signal: USD/JPY FXシグナル通知ボット

使い方:
  uv run fx-signal run       # シグナル監視を開始
  uv run fx-signal check     # 今すぐシグナルを1回チェック
  uv run fx-signal backtest  # バックテストを実行
"""

import argparse
import sys

import structlog

from fx_signal.config import Config
from fx_signal.data import fetcher
from fx_signal.notify import line
from fx_signal.signals import ema_rsi

logger = structlog.get_logger()


def _check_once(cfg: Config) -> None:
    """シグナルを1回チェックして通知する。"""
    logger.info("シグナルチェック開始", pair=cfg.signal.pair)
    df = fetcher.fetch_ohlcv(cfg.signal.pair, cfg.signal.interval, cfg.signal.lookback_days)
    signal = ema_rsi.detect(df, cfg.signal)

    if signal:
        msg = signal.to_line_message()
        logger.info("シグナル検出", direction=signal.direction, price=signal.price)
        print(msg)
        line.send(cfg.line.token, msg)
    else:
        logger.info("シグナルなし", pair=cfg.signal.pair, latest_close=float(df["close"].iloc[-1]))


def _run_loop(cfg: Config) -> None:
    """スケジューラでシグナルを定期チェックする。"""
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler(timezone="Asia/Tokyo")
    minutes = cfg.scheduler.interval_minutes

    _check_once(cfg)

    scheduler.add_job(_check_once, "interval", minutes=minutes, args=[cfg])
    logger.info("スケジューラ開始", interval_minutes=minutes)

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("停止しました")


def _run_backtest(cfg: Config) -> None:
    """バックテストを実行して結果を表示する。"""
    from fx_signal.backtest import runner

    logger.info("バックテスト開始", pair=cfg.signal.pair, interval=cfg.signal.interval)
    df = fetcher.fetch_ohlcv(cfg.signal.pair, cfg.signal.interval, cfg.signal.lookback_days)
    logger.info("データ取得完了", rows=len(df), from_=str(df.index[0]), to=str(df.index[-1]))

    result = runner.run(df, cfg.signal)
    print(result.summary())


def main() -> None:
    parser = argparse.ArgumentParser(description="FXシグナル通知ボット (USD/JPY)")
    parser.add_argument(
        "command",
        choices=["run", "check", "backtest"],
        help="run=定期監視, check=1回チェック, backtest=バックテスト",
    )
    args = parser.parse_args()

    import structlog as sl
    sl.configure(
        processors=[
            sl.stdlib.add_log_level,
            sl.dev.ConsoleRenderer(),
        ]
    )

    cfg = Config()

    if args.command == "run":
        _run_loop(cfg)
    elif args.command == "check":
        _check_once(cfg)
    elif args.command == "backtest":
        _run_backtest(cfg)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
