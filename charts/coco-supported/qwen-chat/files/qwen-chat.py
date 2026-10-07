#!/usr/bin/env python3
"""Local browser chat for encrypted Qwen; requires Python 3 and an authenticated oc."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import select
import subprocess
import urllib.error
import urllib.request

MODEL = 'qwen3-0.6b-encrypted'
SERVICE_PATH = '/api/v1/namespaces/gpu-workload/services/encrypted-qwen:8000/proxy'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    proxy = None
    server = None
    try:
        upstream = os.environ.get('QWEN_BACKEND')
        if not upstream:
            proxy = subprocess.Popen(['oc', 'proxy', '--address=127.0.0.1', '--port=0'],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if not select.select([proxy.stdout], [], [], 15)[0]:
                raise RuntimeError('oc proxy did not start; check your cluster login')
            match = re.search(r'127\.0\.0\.1:(\d+)', proxy.stdout.readline())
            if not match:
                raise RuntimeError('oc proxy failed; check your cluster login')
            upstream = 'http://127.0.0.1:' + match[1] + SERVICE_PATH
        upstream = upstream.rstrip('/')
        with urllib.request.urlopen(upstream + '/health', timeout=20) as response:
            if response.status != 200:
                raise RuntimeError('Qwen is not healthy')
        token = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(24)
        page = Path(__file__).with_suffix('.html').read_text().replace('__CHAT_TOKEN__', token).replace('__NONCE__', nonce).encode()

        class Handler(BaseHTTPRequestHandler):
            timeout = 30

            def log_message(self, *_):
                pass  # Do not log prompts, responses, or browser requests.

            def reply(self, status, payload, content_type='application/json'):
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Content-Security-Policy', "default-src 'none'; connect-src 'self'; style-src 'nonce-" + nonce + "'; script-src 'nonce-" + nonce + "'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
                self.end_headers()
                self.wfile.write(data)

            def host_allowed(self):
                return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', os.environ.get('PUBLIC_HOST')}

            def do_GET(self):
                if self.path == '/healthz':
                    return self.reply(200, {'status': 'ok'})
                if not self.host_allowed():
                    return self.reply(403, {'error': 'Invalid host'})
                if self.path == '/':
                    return self.reply(200, page, 'text/html; charset=utf-8')
                self.reply(404, {'error': 'Not found'})

            def do_POST(self):
                expected_origins = {None, f'http://127.0.0.1:{self.server.server_port}'}
                if os.environ.get('PUBLIC_HOST'):
                    expected_origins.add(os.environ.get('PUBLIC_SCHEME', 'http') + '://' + os.environ['PUBLIC_HOST'])
                if (not self.host_allowed() or self.headers.get('Origin') not in expected_origins
                        or not secrets.compare_digest(self.headers.get('X-Chat-Token', ''), token)):
                    return self.reply(403, {'error': 'Invalid local chat session; reload this page'})
                if self.path != '/api/chat':
                    return self.reply(404, {'error': 'Not found'})
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 65536:
                        raise ValueError('Request is too large')
                    if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                        raise ValueError('JSON content required')
                    body = json.loads(self.rfile.read(length))
                    messages = body.get('messages')
                    if not isinstance(messages, list) or not 1 <= len(messages) <= 100:
                        raise ValueError('Invalid message history')
                    for msg in messages:
                        if (not isinstance(msg, dict) or msg.get('role') not in ('user', 'assistant')
                                or not isinstance(msg.get('content'), str) or len(msg['content']) > 6000):
                            raise ValueError('Invalid message')
                    if messages[-1]['role'] != 'user' or not messages[-1]['content'].strip():
                        raise ValueError('Enter a message')
                    # Keep complete recent turns within the small demo model's context.
                    messages = [{'role': m['role'], 'content': m['content']} for m in messages[-9:]]
                    while len(messages) > 1 and sum(len(m['content']) for m in messages) > 5000:
                        messages = messages[2:]
                    payload = {'model': MODEL, 'messages': messages, 'temperature': 0.2,
                               'max_tokens': 384, 'chat_template_kwargs': {'enable_thinking': False}}
                    request = urllib.request.Request(upstream + '/v1/chat/completions',
                        data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
                    with urllib.request.urlopen(request, timeout=120) as response:
                        completion = json.load(response)
                    choice = completion['choices'][0]
                    self.reply(200, {'message': choice['message']['content'],
                                     'finish_reason': choice['finish_reason'], 'usage': completion.get('usage')})
                except (ValueError, KeyError, TypeError) as exc:
                    self.reply(400, {'error': str(exc)})
                except urllib.error.HTTPError as exc:
                    detail = exc.read(2048).decode(errors='replace')
                    self.reply(502, {'error': 'Model request failed. Try a shorter message or a new chat. ' + detail})
                except (OSError, TimeoutError):
                    self.reply(502, {'error': 'Cannot reach Qwen. Check your cluster connection and try again.'})

        server = ThreadingHTTPServer((os.environ.get('LISTEN_ADDRESS', '127.0.0.1'), args.port), Handler)
        print(f'Qwen chat: http://127.0.0.1:{server.server_port}', flush=True)
        print('Connected to encrypted Qwen on OpenShift. Ctrl-C stops chat and its API proxy.', flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if server:
            server.server_close()
        if proxy:
            proxy.terminate()
            proxy.wait(timeout=10)


if __name__ == '__main__':
    main()
