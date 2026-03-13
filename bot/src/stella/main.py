"""Stella Traderのエントリーポイント。

CLIからの引数を解析し、設定をロードし、
トレーディングエンジンを起動する。SIGINT/SIGTERMによる安全な停止をサポートする。
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path

import structlog


def setup_logging(log_level: str = "INFO") -> None:
    """structlogのロギング設定を行う。

    Args:
        log_level: ログレベル ("DEBUG", "INFO", "WARNING", "ERROR")
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(structlog, log_level, structlog.INFO) if hasattr(structlog, log_level) else structlog.INFO
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """コマンドライン引数を解析する。

    Args:
        argv: 引数リスト (Noneの場合はsys.argvを使用)

    Returns:
        解析された引数のNamespace
    """
    parser = argparse.ArgumentParser(
        prog="stella",
        description="Stella Trader - 仮想通貨自動売買システム",
    )
    parser.add_argument(
        "mode",
        choices=["live", "paper", "backtest"],
        help="実行モード (live: 本番取引, paper: ペーパートレード, backtest: バックテスト)",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        default="config.toml",
        help="設定ファイルのパス (デフォルト: config.toml)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="ログレベル (デフォルト: INFO)",
    )
    return parser.parse_args(argv)


async def run_engine(args: argparse.Namespace) -> None:
    """トレーディングエンジンを起動して実行する。

    Args:
        args: コマンドライン引数
    """
    logger = structlog.get_logger(__name__)

    # 設定ファイルの読み込み
    config_path = Path(args.config)
    if not config_path.exists():
        logger.error("設定ファイルが見つかりません", path=str(config_path))
        sys.exit(1)

    try:
        from stella.config import Config

        config = Config.load(str(config_path))
    except ImportError:
        logger.error("設定モジュールの読み込みに失敗しました")
        sys.exit(1)
    except Exception as e:
        logger.error("設定ファイルの読み込みに失敗しました", error=str(e))
        sys.exit(1)

    # モードを設定に反映
    if hasattr(config, "mode"):
        config.mode = args.mode

    from stella.core.engine import TradingEngine

    engine = TradingEngine(config)

    # シグナルハンドラの設定
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def handle_shutdown(sig: signal.Signals) -> None:
        """シャットダウンシグナルを処理する。"""
        logger.info("シャットダウンシグナルを受信しました", signal=sig.name)
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_shutdown, sig)

    # エンジンの初期化と実行
    try:
        await engine.initialize()
        logger.info(
            "Stella Traderを起動しました",
            mode=args.mode,
            config=str(config_path),
        )

        # エンジン実行とシャットダウン待機を並行
        engine_task = asyncio.create_task(engine.run())
        shutdown_task = asyncio.create_task(shutdown_event.wait())

        done, pending = await asyncio.wait(
            [engine_task, shutdown_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # シャットダウン処理
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        await engine.shutdown()

    except Exception as e:
        logger.error("エンジンの実行中にエラーが発生しました", error=str(e))
        await engine.shutdown()
        raise


def main(argv: list[str] | None = None) -> None:
    """メインエントリーポイント。

    Args:
        argv: コマンドライン引数 (Noneの場合はsys.argvを使用)
    """
    args = parse_args(argv)
    setup_logging(args.log_level)

    logger = structlog.get_logger(__name__)
    logger.info("Stella Traderを開始します", mode=args.mode)

    try:
        asyncio.run(run_engine(args))
    except KeyboardInterrupt:
        logger.info("キーボード割り込みで停止しました")
    except Exception as e:
        logger.error("予期しないエラーが発生しました", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
