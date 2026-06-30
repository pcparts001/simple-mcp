#!/usr/bin/env python3
"""
Simple MCP Server Demo - Fully Compliant with MCP Specification
ゲートウェイ経由での問題に対応したバージョン
"""

import json
import traceback
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from typing import Any, Dict


# 設定ファイルの読み込み
def load_config(config_path="mcp_server_config.json"):
    """MCP Server設定ファイルを読み込む"""
    default_config = {
        "host": "0.0.0.0",
        "port": 9000,
        "server_info": {
            "name": "simple-demo-server",
            "version": "1.0.0"
        },
        "secret_file_path": "./mcp-server-data/secret_notes.txt",
        "check_maintenance_description": "Tool for checking maintenance information",
        "check_maintenance_prefix": "maintenance information",
        "oauth": {
            "enabled": False
        }
    }

    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                user_config = json.load(f)
                default_config.update(user_config)
            print(f"✅ Loaded config from {config_path}")
        except Exception as e:
            print(f"⚠️  Failed to load config file: {e}")
            print(f"   Using default configuration")
    else:
        print(f"⚠️  Config file not found: {config_path}")
        print(f"   Using default configuration")
        print(f"   Create config from example: cp mcp_server_config.json.example mcp_server_config.json")

    return default_config


def ensure_secret_notes_file(config):
    """secret_notes.txtが存在しない場合、exampleからコピーする"""
    secret_file_path = config.get('secret_file_path', './mcp-server-data/secret_notes.txt')
    example_file_path = secret_file_path + '.example'

    if not os.path.exists(secret_file_path):
        if os.path.exists(example_file_path):
            import shutil
            shutil.copy(example_file_path, secret_file_path)
            print(f"✅ Created secret_notes.txt from example: {secret_file_path}")
        else:
            print(f"⚠️  Warning: Neither secret_notes.txt nor secret_notes.txt.example found")
            print(f"   Expected location: {secret_file_path}")
            # 空ファイルを作成
            with open(secret_file_path, 'w', encoding='utf-8') as f:
                f.write("[Daily Maintenance Log]\nStatus: No data\n")
            print(f"✅ Created empty secret_notes.txt: {secret_file_path}")


class OAuthVerifier:
    """OAuth 2.1 Bearer トークン検証（JWKS ローカル検証・RS256）。

    OAuth が有効な場合のみ構築され、Authorization ヘッダの Bearer トークンを
    IdP の公開鍵(JWKS)で検証する。無効時は認証をパススルーする。
    """

    def __init__(self, oauth_config):
        self.enabled = bool(oauth_config.get("enabled", False))
        self.issuer = (oauth_config.get("issuer") or "").strip()
        self.jwks_uri = (oauth_config.get("jwks_uri") or "").strip()
        self.audience = (oauth_config.get("audience") or "").strip()
        self.scopes = set(oauth_config.get("scopes", []) or [])

        self._jwt = None
        self._jwk_client = None
        if self.enabled:
            try:
                import jwt as _jwt
                from jwt import PyJWKClient
            except ImportError as e:
                raise RuntimeError(
                    "OAuth is enabled but PyJWT is not installed. "
                    "Install dependencies: pip install PyJWT cryptography"
                ) from e
            self._jwt = _jwt
            cache_ttl = int(oauth_config.get("jwks_cache_seconds", 600))
            try:
                # PyJWKClient 2.10+ は lifespan、2.8/2.9 は cache_ttl
                self._jwk_client = PyJWKClient(self.jwks_uri, lifespan=cache_ttl)
            except TypeError:
                self._jwk_client = PyJWKClient(self.jwks_uri, cache_ttl=cache_ttl)

    def verify(self, token):
        """Bearer トークンを検証し、claims(dict)を返す。無効なら例外を送出。"""
        if not self.enabled:
            return {}

        # kid に対応する署名鍵を JWKS から取得（キャッシュ利用）
        signing_key = self._jwk_client.get_signing_key_from_jwt(token)

        decode_kwargs = {"algorithms": ["RS256"]}
        if self.issuer:
            decode_kwargs["issuer"] = self.issuer
        if self.audience:
            decode_kwargs["audience"] = self.audience
        else:
            decode_kwargs["options"] = {"verify_aud": False}

        claims = self._jwt.decode(token, signing_key.key, **decode_kwargs)

        # scope 検証（スペース区切りを想定）
        if self.scopes:
            token_scopes = set(str(claims.get("scope", "")).split())
            missing = self.scopes - token_scopes
            if missing:
                raise ValueError(f"Missing required scopes: {sorted(missing)}")

        return claims


class MCPRequestHandler(BaseHTTPRequestHandler):
    """MCPリクエストを処理するHTTPハンドラー（MCP仕様完全準拠）"""

    # サーバーの機能を宣言
    capabilities = {
        "tools": {},      # ツール機能を提供
        "prompts": {},    # プロンプト機能を提供
        "resources": {}   # リソース機能を提供
    }

    # 設定（クラス変数として設定）
    server_config = {}
    oauth_verifier = None  # OAuth 検証器（None または disabled の場合は認証無効）

    def do_OPTIONS(self):
        """OPTIONSリクエストの処理（CORS対応）"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, Mcp-Protocol-Version')
        self.end_headers()

    # --- OAuth 認証ヘルパ ---
    def _is_public_path(self):
        """認証不要のパスかを判定"""
        path = self.path.split("?")[0].rstrip("/")
        if path == "/.well-known/oauth-protected-resource":
            return True
        return False

    def _send_unauthorized(self, description="Invalid token"):
        """401 Unauthorized を返す（WWW-Authenticate ヘッダ付き）"""
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        www = 'Bearer error="invalid_token"'
        if description:
            www += f', error_description="{description}"'
        self.send_header("WWW-Authenticate", www)
        self.end_headers()
        body = json.dumps({
            "error": "invalid_token",
            "error_description": description,
        })
        self.wfile.write(body.encode("utf-8"))

    def _authenticate(self):
        """認証が必要なリクエストで Bearer を検証。
        認証無効または成功なら True、失敗(401応答済み)なら False。"""
        verifier = self.oauth_verifier
        if verifier is None or not verifier.enabled:
            return True

        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            self._send_unauthorized("Missing Bearer token")
            return False

        token = auth[len("Bearer "):].strip()
        try:
            verifier.verify(token)
            return True
        except Exception as e:
            self._send_unauthorized(str(e))
            return False

    def _serve_protected_resource_metadata(self):
        """RFC 9728 Protected Resource Metadata を返す"""
        host_header = self.headers.get("Host", "")
        port = self.server_config.get("port", 9000)
        resource_url = f"http://{host_header}" if host_header else f"http://localhost:{port}"
        oauth_cfg = self.server_config.get("oauth", {}) or {}
        metadata = {
            "resource": resource_url,
            "authorization_servers": [oauth_cfg["issuer"]] if oauth_cfg.get("issuer") else [],
            "bearer_methods_supported": ["header"],
            "resource_documentation": resource_url,
            "scopes_supported": oauth_cfg.get("scopes", []) or [],
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"))

    def do_GET(self):
        """GETリクエストの処理（ヘルスチェック + OAuth メタデータ）"""
        path = self.path.split("?")[0].rstrip("/")

        # Protected Resource Metadata (RFC 9728) — 認証不要
        if path == "/.well-known/oauth-protected-resource":
            self._serve_protected_resource_metadata()
            return

        print(f"\n{'='*60}")
        print(f"📥 Received GET request")
        print(f"   Path: {self.path}")
        print(f"{'='*60}\n")

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        response = {
            "status": "ok",
            "server": "MCP Server",
            "version": "1.0.0",
            "protocolVersion": "2024-11-05",
            "message": "Server is running. Use POST method for MCP requests."
        }
        self.wfile.write(json.dumps(response, ensure_ascii=False, indent=2).encode('utf-8'))

    def do_POST(self):
        """POSTリクエストの処理"""
        # OAuth 認証（有効時のみ）
        if not self._authenticate():
            return
        try:
            # すべてのヘッダーを詳細に表示
            print(f"\n{'='*60}")
            print(f"📥 Received POST request")
            print(f"   Path: {self.path}")
            print(f"   Client: {self.client_address}")
            print(f"\n   All Headers:")
            for header, value in self.headers.items():
                print(f"      {header}: {value}")

            # Content-Lengthの取得
            content_length = int(self.headers.get('Content-Length', 0))
            print(f"\n   Content-Length: {content_length}")

            # Transfer-Encodingをチェック
            transfer_encoding = self.headers.get('Transfer-Encoding', '')
            print(f"   Transfer-Encoding: {transfer_encoding if transfer_encoding else 'None'}")

            # リクエストボディの読み取り
            request_body = ""

            if transfer_encoding.lower() == 'chunked':
                # chunked転送の場合
                print(f"   ⚠️  Chunked transfer encoding detected")
                chunks = []
                while True:
                    line = self.rfile.readline().decode('utf-8').strip()
                    chunk_size = int(line, 16) if line else 0
                    if chunk_size == 0:
                        break
                    chunk_data = self.rfile.read(chunk_size)
                    chunks.append(chunk_data)
                    self.rfile.read(2)  # \r\n を読み飛ばす
                request_body = b''.join(chunks).decode('utf-8')
            elif content_length > 0:
                # 通常の転送
                request_body = self.rfile.read(content_length).decode('utf-8')
            else:
                # Content-Lengthが0だが、データが来ているかもしれない
                # 念のため少し待って読み込みを試みる
                import select
                if select.select([self.rfile], [], [], 0.1)[0]:
                    try:
                        # 利用可能なデータを読み取る
                        request_body = self.rfile.read(8192).decode('utf-8', errors='ignore')
                        print(f"   ℹ️  Read data despite Content-Length: 0")
                    except:
                        pass

            print(f"   Body length (actual): {len(request_body)}")
            print(f"   Body: {request_body if request_body else '(empty)'}")
            print(f"{'='*60}\n")

            # ボディが空の場合の処理
            if not request_body or len(request_body) == 0:
                print(f"⚠️  WARNING: Empty request body received")
                print(f"   This is likely a gateway/proxy configuration issue.")
                print(f"   Possible causes:")
                print(f"   - Gateway is not forwarding the request body")
                print(f"   - Proxy buffering issue")
                print(f"   - HTTP/2 to HTTP/1.1 conversion problem")
                print()

                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "result": {
                        "status": "ok",
                        "serverInfo": self.server_config['server_info'],
                        "message": "MCP Server is ready. Please send valid JSON-RPC request.",
                        "debug": {
                            "received_headers": dict(self.headers.items()),
                            "content_length": content_length,
                            "body_received": False
                        }
                    }
                }

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(response, ensure_ascii=False).encode('utf-8'))
                return

            # JSONパース
            request = json.loads(request_body)

            # MCPリクエスト処理
            response = self.handle_mcp_request(request)

            print(f"\n{'='*60}")
            print(f"📤 Sending response")
            print(f"   Response: {json.dumps(response, indent=2, ensure_ascii=False)}")
            print(f"{'='*60}\n")

            # レスポンス送信
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(response, ensure_ascii=False).encode('utf-8'))

        except json.JSONDecodeError as e:
            error_msg = f"JSON Parse Error: {str(e)}"
            print(f"\n❌ ERROR: {error_msg}\n")

            error_response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": error_msg
                }
            }
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(error_response, ensure_ascii=False).encode('utf-8'))

        except Exception as e:
            error_msg = f"Internal Server Error: {str(e)}"
            print(f"\n❌ ERROR: {error_msg}")
            print(f"   Traceback:\n{traceback.format_exc()}\n")

            error_response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32603,
                    "message": error_msg
                }
            }
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(error_response, ensure_ascii=False).encode('utf-8'))

    def handle_mcp_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """MCPリクエストの処理"""
        method = request.get("method")
        params = request.get("params", {})
        request_id = request.get("id")

        print(f"🔧 Processing method: {method}")
        print(f"   Request ID: {request_id}")
        print(f"   Params: {params}")

        try:
            # 通知（レスポンス不要なメッセージ）
            if method == "notifications/initialized":
                print("   ℹ️  Received initialized notification")
                if request_id is None:
                    return None
                else:
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {}
                    }

            # リクエスト（レスポンスが必要）
            if method == "initialize":
                result = self.handle_initialize(params)
            elif method == "tools/list":
                result = self.handle_tools_list(params)
            elif method == "tools/call":
                result = self.handle_tools_call(params)
            elif method == "prompts/list":
                result = self.handle_prompts_list(params)
            elif method == "prompts/get":
                result = self.handle_prompts_get(params)
            elif method == "resources/list":
                result = self.handle_resources_list(params)
            elif method == "resources/read":
                result = self.handle_resources_read(params)
            elif method == "ping":
                result = {"status": "pong"}
            else:
                raise ValueError(f"Method not found: {method}")

            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result
            }

        except Exception as e:
            print(f"❌ Error in handle_mcp_request: {str(e)}")
            print(f"   Traceback:\n{traceback.format_exc()}")
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601 if "not found" in str(e).lower() else -32603,
                    "message": str(e)
                }
            }

    def handle_initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """初期化リクエストの処理"""
        print("   ✅ Handling initialize")
        client_info = params.get("clientInfo", {})
        print(f"      Client: {client_info.get('name', 'unknown')} v{client_info.get('version', 'unknown')}")

        return {
            "protocolVersion": "2024-11-05",
            "serverInfo": self.server_config['server_info'],
            "capabilities": self.capabilities
        }

    def handle_tools_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """利用可能なツールのリスト取得"""
        print("   ✅ Handling tools/list")
        # 設定からメンテナンスツールの説明を取得
        check_maintenance_desc = self.server_config.get('check_maintenance_description', 'Tool for checking maintenance information')
        return {
            "tools": [
                {
                    "name": "get_test_string",
                    "description": "Simple tool that returns a test string",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "prefix": {
                                "type": "string",
                                "description": "Prefix for the returned string (optional)"
                            }
                        }
                    }
                },
                {
                    "name": "echo",
                    "description": "Echoes back the input message",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "message": {
                                "type": "string",
                                "description": "Message to echo"
                            }
                        },
                        "required": ["message"]
                    }
                },
                {
                    "name": "check_maintenance",
                    "description": check_maintenance_desc,
                    "inputSchema": {
                        "type": "object",
                        "properties": {}
                    }
                }
            ]
        }

    def handle_tools_call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """ツールの実行"""
        print("   ✅ Handling tools/call")
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        print(f"      Tool: {tool_name}")
        print(f"      Arguments: {arguments}")

        if tool_name == "get_test_string":
            prefix = arguments.get("prefix", "Hello")
            result_text = f"{prefix} from MCP Server! This is a test string."
            return {
                "content": [
                    {
                        "type": "text",
                        "text": result_text
                    }
                ]
            }

        elif tool_name == "echo":
            message = arguments.get("message", "")
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Echo: {message}"
                    }
                ]
            }

        elif tool_name == "check_maintenance":
            file_path = self.server_config.get('secret_file_path', './data/secret_notes.txt')
            # 設定からプレフィックスを取得
            prefix = self.server_config.get('check_maintenance_prefix', 'maintenance information')

            try:
                if not os.path.exists(file_path):
                    error_msg = f"File not found: {file_path}"
                    print(f"      ❌ {error_msg}")
                    raise FileNotFoundError(error_msg)

                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()

                print(f"      📄 Read file: {file_path}")
                print(f"      📏 File size: {len(content)} characters")

                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"{prefix}:\n\n{content}"
                        }
                    ]
                }

            except FileNotFoundError as e:
                error_msg = str(e)
                print(f"      ❌ FileNotFoundError: {error_msg}")
                raise ValueError(error_msg)

            except PermissionError as e:
                error_msg = f"Permission denied reading file: {file_path}"
                print(f"      ❌ PermissionError: {error_msg}")
                raise ValueError(error_msg)

            except UnicodeDecodeError as e:
                error_msg = f"Encoding error reading file: {file_path}"
                print(f"      ❌ UnicodeDecodeError: {error_msg}")
                raise ValueError(error_msg)

            except Exception as e:
                error_msg = f"Error reading file: {str(e)}"
                print(f"      ❌ Exception: {error_msg}")
                raise ValueError(error_msg)

        else:
            raise ValueError(f"Unknown tool: {tool_name}")

    def handle_prompts_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """利用可能なプロンプトのリスト取得"""
        print("   ✅ Handling prompts/list")
        return {
            "prompts": [
                {
                    "name": "greeting",
                    "description": "Simple greeting prompt",
                    "arguments": [
                        {
                            "name": "name",
                            "description": "Name of the person to greet",
                            "required": False
                        }
                    ]
                }
            ]
        }

    def handle_prompts_get(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """特定のプロンプトを取得"""
        print("   ✅ Handling prompts/get")
        prompt_name = params.get("name")
        arguments = params.get("arguments", {})

        if prompt_name == "greeting":
            name = arguments.get("name", "World")
            return {
                "description": "Greeting prompt",
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": f"Hello, {name}!"
                        }
                    }
                ]
            }
        else:
            raise ValueError(f"Unknown prompt: {prompt_name}")

    def handle_resources_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """利用可能なリソースのリスト取得"""
        print("   ✅ Handling resources/list")
        return {
            "resources": [
                {
                    "uri": "demo://test-data",
                    "name": "Test Data",
                    "description": "Test data resource for demo purposes",
                    "mimeType": "text/plain"
                }
            ]
        }

    def handle_resources_read(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """特定のリソースを読み取り"""
        print("   ✅ Handling resources/read")
        resource_uri = params.get("uri")

        if resource_uri == "demo://test-data":
            return {
                "contents": [
                    {
                        "uri": resource_uri,
                        "mimeType": "text/plain",
                        "text": "This is test data provided by the MCP server.\nThis is a demonstration of the resource feature."
                    }
                ]
            }
        else:
            raise ValueError(f"Unknown resource: {resource_uri}")

    def log_message(self, format, *args):
        """ログメッセージの出力（無効化してカスタムログのみ使用）"""
        pass


def run_http_server(config):
    """HTTPサーバーの起動"""
    host = config['host']
    port = config['port']
    server_info = config['server_info']

    # OAuth 検証器の初期化（有効時のみ）
    oauth_cfg = config.get("oauth", {}) or {}
    verifier = None
    if oauth_cfg.get("enabled"):
        verifier = OAuthVerifier(oauth_cfg)
        print(f"🔒 OAuth enabled: issuer={verifier.issuer}")
    else:
        print(f"🔓 OAuth disabled (no authentication)")

    server_address = (host, port)
    httpd = ThreadingHTTPServer(server_address, MCPRequestHandler)

    # 設定をハンドラーに渡す
    MCPRequestHandler.server_config = config
    MCPRequestHandler.oauth_verifier = verifier

    print(f"\n{'='*60}")
    print(f"🚀 MCP Server (Fully Compliant) running on http://{host}:{port}/")
    print(f"   Access URL: http://localhost:{port}/mcp/")
    print(f"   Protocol Version: 2024-11-05")
    print(f"   Server: {server_info['name']} v{server_info['version']}")
    print(f"   ")
    print(f"   Supported capabilities:")
    print(f"   - Tools (get_test_string, echo, check_maintenance)")
    print(f"   - Prompts (greeting)")
    print(f"   - Resources (demo://test-data)")
    print(f"   ")
    print(f"   Ready to accept requests from MCP Gateway...")
    print(f"   ")
    print(f"   ⚠️  Gateway Troubleshooting:")
    print(f"   If you see 'Empty request body', check:")
    print(f"   1. Gateway forwarding configuration")
    print(f"   2. Request body buffering settings")
    print(f"   3. HTTP version compatibility (HTTP/2 vs HTTP/1.1)")
    print(f"{'='*60}\n")
    httpd.serve_forever()


if __name__ == "__main__":
    # 設定を読み込む
    config = load_config()

    # secret_notes.txt が存在しない場合、exampleからコピー
    ensure_secret_notes_file(config)

    # コマンドライン引数でポートを上書き
    if len(sys.argv) > 1:
        try:
            config['port'] = int(sys.argv[1])
            print(f"ℹ️  Port overridden by command line: {config['port']}")
        except ValueError:
            print(f"⚠️  Invalid port argument: {sys.argv[1]}")
            print(f"   Using config port: {config['port']}")

    run_http_server(config)
