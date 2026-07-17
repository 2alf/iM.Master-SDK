"""
Serves a tiny page which polls for new phrases and speaks them with the
browser's built-in Speech Synthesis — so the voice literally comes from the
robot, no cloud TTS and no extra app.

Usage in a loop:
    from immaster.voice import VoiceServer
    voice = VoiceServer(port=8090); voice.start()
    # ... on the phone, open http://<pi-ip>:8090 and tap "Enable voice"
    voice.say("Hmm, a wall. Backing up.")

Stdlib only.
"""

from __future__ import annotations
import json
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>im.master voice</title>
<style>body{font-family:system-ui;background:#111;color:#eee;text-align:center;padding:2rem}
button{font-size:1.4rem;padding:1rem 2rem;border-radius:12px;border:0;background:#3a6;color:#fff}
#log{margin-top:1.5rem;opacity:.8;font-size:1.1rem;min-height:2rem}</style></head>
<body>
<h2>🤖 im.master voice</h2>
<button id=go>🔊 Enable voice</button>
<div id=log>tap the button, then keep this tab open</div>
<script>
let on=false;
document.getElementById('go').onclick=()=>{on=true;
  // browsers need a user gesture to unlock audio; speak once to prime it
  speechSynthesis.speak(new SpeechSynthesisUtterance('voice on'));
  document.getElementById('go').textContent='✅ voice active';
  poll();};
async function poll(){
  if(!on) return;
  try{
    const r=await fetch('/next',{cache:'no-store'});
    const j=await r.json();
    if(j.text){
      const u=new SpeechSynthesisUtterance(j.text);
      u.rate=1.05; speechSynthesis.speak(u);
      document.getElementById('log').textContent='🗣 '+j.text;
    }
  }catch(e){}
  setTimeout(poll,700);
}
</script></body></html>"""


class VoiceServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 8090):
        self.host, self.port = host, port
        self._queue: deque[str] = deque(maxlen=8)
        self._lock = threading.Lock()
        self._srv: ThreadingHTTPServer | None = None

    def say(self, text: str) -> None:
        text = (text or "").strip()
        if text:
            with self._lock:
                self._queue.append(text)

    def _pop(self) -> str | None:
        with self._lock:
            return self._queue.popleft() if self._queue else None

    def start(self) -> None:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code, body, ctype):
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def do_GET(self):
                if self.path.startswith("/next"):
                    txt = server._pop()
                    self._send(200, json.dumps({"text": txt or ""}).encode(),
                               "application/json")
                else:
                    self._send(200, _PAGE.encode(), "text/html; charset=utf-8")

            def do_POST(self):
                if self.path.startswith("/say"):
                    n = int(self.headers.get("Content-Length", 0))
                    raw = self.rfile.read(n) if n else b"{}"
                    try:
                        server.say(json.loads(raw).get("text", ""))
                    except Exception:
                        pass
                    self._send(200, b'{"ok":true}', "application/json")
                else:
                    self._send(404, b'{"error":"not found"}', "application/json")

            def log_message(self, *a):
                pass

        class _QuietServer(ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request, client_address):
                pass

        self._srv = _QuietServer((self.host, self.port), Handler)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def stop(self) -> None:
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()
            self._srv = None


def _demo():
    import time
    v = VoiceServer()
    v.start()
    print("open http://<pi-ip>:8090 on the phone, tap Enable voice")
    for phrase in ["Hello, I am im dot master.", "Exploring now.", "Is that a wall?"]:
        input("enter to speak next...")
        v.say(phrase)
        time.sleep(0.5)


if __name__ == "__main__":
    _demo()
