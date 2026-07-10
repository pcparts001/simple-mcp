#!/usr/bin/env bash
#
# MCP Server（標準版・Streamable HTTP 版 両方）の実行状態を表示する
#
# 使い方:
#   ./scripts/status.sh
#   （終了コード: いずれか起動中=0 / 両方停止=1）
set -uo pipefail

# スクリプトの場所を基準にプロジェクトルートへ移動
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

ANY_RUNNING=0

# 指定 PID ファイルのサーバー状態を表示
check_pidfile() {
    local pf="$1"
    local label="$2"
    local logf="$3"

    if [[ -f "$pf" ]]; then
        local pid
        pid="$(cat "$pf" 2>/dev/null || true)"
        if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
            echo "✅ $label は実行中です (PID: $pid)"
            echo "   ログ: tail -f $logf"
            ANY_RUNNING=1
            return
        fi
        echo "⚠️  $label のPIDファイルはありますが、プロセス (PID: ${pid:-empty}) は見つかりません"
        echo "   古いPIDファイルの可能性があります: rm $pf"
        return
    fi
    echo "🔴 $label は停止しています"
}

check_pidfile \
    "$PROJECT_ROOT/mcp-server.pid" \
    "MCP Server (Standard HTTP)" \
    "$PROJECT_ROOT/logs/server.log"
check_pidfile \
    "$PROJECT_ROOT/mcp-server-streamable.pid" \
    "MCP Server (Streamable HTTP)" \
    "$PROJECT_ROOT/logs/server-streamable.log"

if [[ "$ANY_RUNNING" = "1" ]]; then
    exit 0
else
    exit 1
fi
