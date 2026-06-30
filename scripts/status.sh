#!/usr/bin/env bash
#
# MCP Server の実行状態を表示する
#
# 使い方:
#   ./scripts/status.sh
#   （終了コード: 起動中=0 / 停止中=1）
set -uo pipefail

# スクリプトの場所を基準にプロジェクトルートへ移動
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

PID_FILE="$PROJECT_ROOT/mcp-server.pid"
LOG_FILE="$PROJECT_ROOT/logs/server.log"

if [[ -f "$PID_FILE" ]]; then
    PID="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
        echo "✅ MCP Server は実行中です (PID: $PID)"
        echo "   ログ: tail -f $LOG_FILE"
        exit 0
    fi
    # PIDファイルはあるがプロセスは無い
    echo "⚠️  PIDファイルはありますが、プロセス (PID: ${PID:-empty}) は見つかりません"
    echo "   古いPIDファイルの可能性があります: rm $PID_FILE"
    exit 1
fi

echo "🔴 MCP Server は停止しています"
exit 1
