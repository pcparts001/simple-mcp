#!/usr/bin/env bash
#
# MCP Server をバックグラウンドで起動する（nohup + &）
# - 標準出力/標準エラーは logs/*.log へ追記（tail -f で確認）
# - 出力バッファリングを無効化（python -u）し、tail -f でリアルタイム表示
# - PID を mcp-server*.pid に保存
# - 常に1つのサーバーのみ起動。起動前に既存サーバー（両方）を停止する。
#
# 使い方:
#   ./scripts/start.sh              # 既存サーバー（標準 http.server 版）を設定ポートで起動
#   ./scripts/start.sh 9001         # 既存サーバーをポート指定で起動
#   ./scripts/start.sh stream       # Streamable HTTP 版を設定ポートで起動
#   ./scripts/start.sh stream 9001  # Streamable HTTP 版をポート指定で起動
set -euo pipefail

# スクリプトの場所を基準にプロジェクトルートへ移動（どこから実行しても OK）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"

# --- 引数解析（stream キーワードとポート番号を分離） ---
STREAM=0
PORT_ARG=""
for arg in "$@"; do
    case "$arg" in
        stream|--stream) STREAM=1 ;;
        *)
            # 数値ならポート、そうでなければ無視
            if [[ "$arg" =~ ^[0-9]+$ ]]; then
                PORT_ARG="$arg"
            fi
            ;;
    esac
done

# --- サーバー種別に応じたパス切り替え ---
if [[ "$STREAM" = "1" ]]; then
    SERVER_SCRIPT="$PROJECT_ROOT/mcpServer_streamable.py"
    VENV_DIR="venv-streamable"
    PID_FILE="$PROJECT_ROOT/mcp-server-streamable.pid"
    LOG_FILE="$LOG_DIR/server-streamable.log"
    VARIANT="Streamable HTTP"
else
    SERVER_SCRIPT="$PROJECT_ROOT/mcpServer.py"
    VENV_DIR="venv"
    PID_FILE="$PROJECT_ROOT/mcp-server.pid"
    LOG_FILE="$LOG_DIR/server.log"
    VARIANT="Standard HTTP"
fi

# --- 両サーバーの生存確認・停止（常時1サーバー化） ---
# 指定したサーバーとは関係なく、起動中のサーバーがあれば停止する。
stop_pidfile() {
    local pf="$1"
    if [[ -f "$pf" ]]; then
        local pid
        pid="$(cat "$pf" 2>/dev/null || true)"
        if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
            echo "🛑 起動中のサーバーを停止します (PID: $pid, $(basename "$pf"))"
            kill -TERM "$pid" 2>/dev/null || true
            for _ in $(seq 1 10); do
                kill -0 "$pid" 2>/dev/null || break
                sleep 1
            done
            if kill -0 "$pid" 2>/dev/null; then
                echo "⚠️  SIGTERM で終了しないため強制終了します (PID: $pid)"
                kill -KILL "$pid" 2>/dev/null || true
            fi
        fi
        rm -f "$pf"
    fi
}

stop_pidfile "$PROJECT_ROOT/mcp-server.pid"
stop_pidfile "$PROJECT_ROOT/mcp-server-streamable.pid"

# --- venv 有効化 ---
if [[ -f "$PROJECT_ROOT/$VENV_DIR/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/$VENV_DIR/bin/activate"
    PYTHON_BIN="python"
    echo "✅ $VENV_DIR を有効化しました"
else
    PYTHON_BIN="python3"
    echo "ℹ️  $VENV_DIR が見つかりません。システムの python3 を使用します"
fi

# --- 起動時の区切り線と時刻をログへ記録 ---
{
    echo ""
    echo "============================================================"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] MCP Server ($VARIANT) starting"
    echo "  Port: ${PORT_ARG:-<config default>}"
    echo "============================================================"
} >> "$LOG_FILE"

# --- バックグラウンド起動（-u でバッファリング無効化 → tail -f で即時表示） ---
# nohup によりターミナル切断後も継続動作
nohup "$PYTHON_BIN" -u "$SERVER_SCRIPT" ${PORT_ARG:+$PORT_ARG} >> "$LOG_FILE" 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"

# --- 起動直後の生存確認（即死していたら検知） ---
sleep 1
if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "❌ 起動に失敗しました。ログを確認してください: $LOG_FILE"
    rm -f "$PID_FILE"
    exit 1
fi

echo "✅ MCP Server ($VARIANT) をバックグラウンドで起動しました"
echo "   PID:  $SERVER_PID"
echo "   Port: ${PORT_ARG:-<config default (9000)>}"
echo ""
echo "   ログ確認:    tail -f $LOG_FILE"
echo "   状態確認:    ./scripts/status.sh"
echo "   停止:        ./scripts/stop.sh"
