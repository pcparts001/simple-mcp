#!/usr/bin/env bash
#
# バックグラウンドで起動中の MCP Server（両方 = 標準版・Streamable HTTP 版）を停止する
#
# 使い方:
#   ./scripts/stop.sh
set -euo pipefail

# スクリプトの場所を基準にプロジェクトルートへ移動
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# 指定 PID ファイルのサーバーを停止（未起動なら何もしない）
stop_pidfile() {
    local pf="$1"
    [[ -f "$pf" ]] || return 0

    local pid
    pid="$(cat "$pf" 2>/dev/null || true)"

    if [[ -z "${pid:-}" ]]; then
        echo "ℹ️  PIDファイルが空です。削除します ($(basename "$pf"))"
        rm -f "$pf"
        return 0
    fi

    if ! kill -0 "$pid" 2>/dev/null; then
        echo "ℹ️  プロセス (PID: $pid, $(basename "$pf")) は既に停止しています"
        rm -f "$pf"
        return 0
    fi

    # SIGTERM で安全に停止
    echo "🛑 MCP Server (PID: $pid, $(basename "$pf")) を停止しています..."
    kill -TERM "$pid" 2>/dev/null || true

    # 最大10秒間、終了を待機
    for _ in $(seq 1 10); do
        if ! kill -0 "$pid" 2>/dev/null; then
            echo "✅ 停止しました (PID: $pid, $(basename "$pf"))"
            rm -f "$pf"
            return 0
        fi
        sleep 1
    done

    # まだ生きていれば強制終了
    echo "⚠️  SIGTERM で終了しないため、強制終了 (SIGKILL) します (PID: $pid)"
    kill -KILL "$pid" 2>/dev/null || true
    rm -f "$pf"
    echo "✅ 強制終了しました (PID: $pid, $(basename "$pf"))"
}

stop_pidfile "$PROJECT_ROOT/mcp-server.pid"
stop_pidfile "$PROJECT_ROOT/mcp-server-streamable.pid"
