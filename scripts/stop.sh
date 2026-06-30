#!/usr/bin/env bash
#
# バックグラウンドで起動中の MCP Server を停止する
#
# 使い方:
#   ./scripts/stop.sh
set -euo pipefail

# スクリプトの場所を基準にプロジェクトルートへ移動
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

PID_FILE="$PROJECT_ROOT/mcp-server.pid"

# PIDファイルが無ければ起動していない
if [[ ! -f "$PID_FILE" ]]; then
    echo "ℹ️  PIDファイルが見つかりません（$PID_FILE）"
    echo "   MCP Server は起動していません"
    exit 0
fi

PID="$(cat "$PID_FILE" 2>/dev/null || true)"

if [[ -z "${PID:-}" ]]; then
    echo "ℹ️  PIDファイルが空です。削除します"
    rm -f "$PID_FILE"
    exit 0
fi

# プロセスが生存しているか確認
if ! kill -0 "$PID" 2>/dev/null; then
    echo "ℹ️  プロセス (PID: $PID) は既に停止しています"
    rm -f "$PID_FILE"
    exit 0
fi

# SIGTERM で安全に停止
echo "🛑 MCP Server (PID: $PID) を停止しています..."
kill -TERM "$PID" 2>/dev/null || true

# 最大10秒間、終了を待機
for i in $(seq 1 10); do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "✅ 停止しました (PID: $PID)"
        rm -f "$PID_FILE"
        exit 0
    fi
    sleep 1
done

# まだ生きていれば強制終了
echo "⚠️  SIGTERM で終了しないため、強制終了 (SIGKILL) します"
kill -KILL "$PID" 2>/dev/null || true
rm -f "$PID_FILE"
echo "✅ 強制終了しました (PID: $PID)"
