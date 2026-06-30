# simple-mcp

MCP（Model Context Protocol）に準拠したシンプルな HTTP ベースの MCP Server です。
Python 標準ライブラリのみで動作し、ツール・プロンプト・リソースを提供します。

## 提供する機能

### Tools
| ツール名 | 説明 |
| --- | --- |
| `get_test_string` | テスト用文字列を返します（`prefix` オプション対応） |
| `echo` | 入力メッセージをそのまま返します |
| `check_maintenance` | `secret_notes.txt` のメンテナンス情報を返します |

### Prompts
| プロンプト名 | 説明 |
| --- | --- |
| `greeting` | 挨拶プロンプト（`name` 引数対応） |

### Resources
| URI | 説明 |
| --- | --- |
| `demo://test-data` | デモ用テストデータ |

### その他の機能
- **OAuth 2.1** Bearer トークン検証（JWKS / RS256）※オプション・デフォルト無効
- CORS 対応
- RFC 9728 Protected Resource Metadata エンドポイント（`/.well-known/oauth-protected-resource`）
- MCP プロトコルバージョン `2024-11-05`

## 必要環境
- Python 3.8 以上
- OAuth 認証を使う場合のみ追加パッケージが必要（`requirements.txt` 参照）

## セットアップ

```bash
# 仮想環境の作成
python3 -m venv venv
source venv/bin/activate

# 依存パッケージのインストール（OAuth を使わない場合は省略可）
pip install -r requirements.txt

# 設定ファイルの準備
cp mcp_server_config.json.example mcp_server_config.json

# メンテナンス情報ファイルの準備（自動生成されますが、手動で用意も可能）
mkdir -p mcp-server-data
cp mcp-server-data/secret_notes.txt.example mcp-server-data/secret_notes.txt
```

## 起動

### フォアグラウンド実行（手元での検証用）

```bash
# 設定ファイルのポートで起動（デフォルト: 9000）
python3 mcpServer.py

# ポートを指定して起動
python3 mcpServer.py 9001
```

起動後、`http://localhost:9000/` でリクエストを待ち受けます。

### バックグラウンド実行（本番運用 / Ubuntu 推奨）

`scripts/` 以下のスクリプトを使うと、ターミナルを切断しても継続動作するバックグラウンド起動ができます。
ログは `logs/server.log` に出力され、`tail -f` でリアルタイムに確認できます。

```bash
# 初回のみ：スクリプトに実行権限を付与
chmod +x scripts/*.sh

# 起動（設定ファイルのポート / デフォルト 9000）
./scripts/start.sh

# ポートを指定して起動
./scripts/start.sh 9001

# ログをリアルタイム確認（別ターミナルで実行）
tail -f logs/server.log

# 状態確認
./scripts/status.sh

# 停止
./scripts/stop.sh
```

> **補足**: バックグラウンド起動時は `python3 -u`（出力バッファリング無効化）で動かすため、
> `tail -f` で遅延なくログが表示されます。プロセスは PID ファイル（`mcp-server.pid`）で管理され、
> 二重起動は防止されます。

## 動作確認

```bash
# ヘルスチェック（GET）
curl http://localhost:9000/

# initialize リクエスト（POST）
curl -X POST http://localhost:9000/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'

# ツール一覧の取得
curl -X POST http://localhost:9000/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

# ツールの実行（echo）
curl -X POST http://localhost:9000/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"echo","arguments":{"message":"hello"}}}'
```

## 設定（`mcp_server_config.json`）

| 項目 | 説明 | デフォルト |
| --- | --- | --- |
| `host` | 待ち受けホスト | `0.0.0.0` |
| `port` | 待ち受けポート | `9000` |
| `server_info` | サーバー名・バージョン | `simple-demo-server v1.0.0` |
| `secret_file_path` | メンテナンス情報ファイルのパス | `./mcp-server-data/secret_notes.txt` |
| `check_maintenance_description` | `check_maintenance` ツールの説明文 | （例を参照） |
| `check_maintenance_prefix` | 返却時のプレフィックス文字列 | `maintenance information` |
| `oauth` | OAuth 認証設定（`enabled` / `issuer` / `jwks_uri` / `audience` / `scopes`） | `enabled: false` |

## ディレクトリ構成

```
simple-mcp/
├── mcpServer.py                          # MCP Server 本体
├── mcp_server_config.json.example        # 設定ファイル例
├── scripts/
│   ├── start.sh                          # バックグラウンド起動（nohup + PID管理）
│   ├── stop.sh                           # 停止
│   └── status.sh                         # 状態確認
├── mcp-server-data/
│   └── secret_notes.txt.example          # メンテナンス情報ファイル例
├── logs/                                 # ログ出力先（.gitignore 対象・実行時に生成）
├── requirements.txt                      # OAuth 利用時の依存パッケージ
└── README.md
```
