"""Claude APIによるトレードシグナルのAI判断モジュール。

4Hトレンド方向・セッション・経済指標リスクを総合的に評価し、
エントリーの可否と理由を返す。
"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic
import structlog

from fx_signal.signals.base import Direction, Signal

logger = structlog.get_logger()

_JST = ZoneInfo("Asia/Tokyo")
_CLIENT = None


def _client() -> anthropic.Anthropic:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = anthropic.Anthropic()
    return _CLIENT


def judge(signal: Signal, trend_direction: str) -> tuple[bool, str]:
    """シグナルのエントリー可否をClaude APIで判断する。

    Returns:
        (go, reason): goがTrueなら通知する、reasonは判断理由
    """
    now_jst = datetime.now(_JST)
    direction_label = "買い（ロング）" if signal.direction == Direction.BUY else "売り（ショート）"

    prompt = f"""あなたはプロのFXトレーダーです。以下のUSD/JPYシグナルについて、エントリーすべきか判断してください。

## シグナル情報
- 方向: {direction_label}
- 現在価格: {signal.price:.3f} 円
- 利確目標: {signal.tp:.3f} 円
- 損切りライン: {signal.sl:.3f} 円
- 根拠: {signal.reason}

## 市場コンテキスト
- 現在時刻 (JST): {now_jst.strftime("%Y-%m-%d %H:%M")} ({now_jst.strftime("%A")})
- 4時間足トレンド: {trend_direction}

## 判断基準（3つ全て評価すること）

1. **トレンド整合性**
   - 上昇トレンド中 → 買いシグナルのみ有効（売りはスキップ）
   - 下降トレンド中 → 売りシグナルのみ有効（買いはスキップ）
   - 横ばい → 両方有効

2. **セッション品質**（JSTで判断）
   - 21:00〜02:00: NYセッション（最高品質）
   - 09:00〜15:00: 東京セッション（良質）
   - 15:00〜20:00: ロンドンセッション（良質）
   - 上記以外: 低流動性（推奨しない）

3. **経済指標リスク**
   - 今日・明日が以下に該当する場合はリスク高：
     * 毎月第1金曜日 = 米雇用統計(NFP)
     * 年8回のFOMC（3月・5月・6月・7月・9月・11月・12月）
     * 日銀会合（年8回）
     * その他 CPI、PCE、GDP など米国・日本の重要指標
   - 指標の1〜2時間前後は価格が急変しやすいため避ける

## 回答形式
必ずJSON形式で回答すること:
{{"go": true または false, "reason": "50文字以内の日本語で判断理由"}}

例: {{"go": true, "reason": "上昇トレンドと一致、NYセッション中、指標なし"}}
例: {{"go": false, "reason": "トレンド逆行（下降トレンドで買いシグナル）"}}"""

    try:
        resp = _client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()

        # JSON部分を抽出
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(text[start:end])
            go = bool(data.get("go", False))
            reason = str(data.get("reason", ""))
            logger.info("AI判断完了", go=go, reason=reason)
            return go, reason

    except Exception as e:
        logger.warning("AI判断に失敗、シグナルを通過させます", error=str(e))

    return True, "AI判断スキップ（エラー）"
