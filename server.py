#!/usr/bin/env python3
"""Serveur de votes pour la clé d'identification (Python 3.8+, aucune dépendance).

Lancer :   python3 server.py
Variables d'environnement (toutes facultatives) :
  PORT=8765               port d'écoute
  HOST=127.0.0.1          adresse d'écoute (127.0.0.1 = derrière ton serveur web / reverse proxy)
  DB=cle.db               fichier SQLite
  ALLOWED_ORIGIN=*        origine autorisée (CORS) si la page est sur un autre domaine, ex. https://monsite.fr
  ADMIN_TOKEN=            si défini, GET /api/admin?token=... liste suggestions et signalements
  STATIC=index.html       page servie sur / (mettre vide pour désactiver)
  Les images sont servies depuis le dossier assets/ (à côté de server.py) sur /assets/...

API :
  GET  /api/state?voter=ID              -> {"counts":{clé:{"u":n,"d":n}}, "mine":{clé:"u"|"d"}}
  POST /api/vote     {voter,key,vote}   vote = "u" | "d" | null (retirer)
  POST /api/suggest  {voter,id,text}    (créée avec approved=0 : invisible tant que tu ne l'approuves pas)
  POST /api/report   {voter,key,reason,comment}
  GET  /admin                           page d'administration (ADMIN_TOKEN requis) : approuver / retirer les suggestions
  POST /api/admin/approve {token,id,approved}
"""
import json, os, re, sqlite3, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get('PORT', '8765'))
HOST = os.environ.get('HOST', '127.0.0.1')
DB = os.environ.get('DB', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cle.db'))
ORIGIN = os.environ.get('ALLOWED_ORIGIN', '*')
ADMIN = os.environ.get('ADMIN_TOKEN', '')
STATIC = os.environ.get('STATIC', 'index.html')
HERE = os.path.dirname(os.path.abspath(__file__))
VOTER_RE = re.compile(r'^[A-Za-z0-9\-]{8,64}$')
LOCK = threading.Lock()

def db():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    return c

def init():
    with db() as c:
        pass
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS votes(voter TEXT, key TEXT, vote TEXT CHECK(vote IN ('u','d')), ts INTEGER, PRIMARY KEY(voter,key));
        CREATE INDEX IF NOT EXISTS votes_key ON votes(key);
        CREATE TABLE IF NOT EXISTS suggestions(id INTEGER PRIMARY KEY AUTOINCREMENT, voter TEXT, node TEXT, text TEXT, ts INTEGER);
        CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY AUTOINCREMENT, voter TEXT, key TEXT, reason TEXT, comment TEXT, ts INTEGER);
        """)
        try: c.execute("ALTER TABLE suggestions ADD COLUMN approved INTEGER DEFAULT 0")
        except sqlite3.OperationalError: pass

# limitation simple : 60 écritures / minute / IP
HITS = {}
def too_many(ip):
    now = time.time(); h = [t for t in HITS.get(ip, []) if now - t < 60]
    h.append(now); HITS[ip] = h
    return len(h) > 60

def counts_for(c, key=None):
    q = "SELECT key, vote, COUNT(*) n FROM votes" + (" WHERE key=?" if key else "") + " GROUP BY key, vote"
    out = {}
    for r in c.execute(q, (key,) if key else ()):
        out.setdefault(r['key'], {'u': 0, 'd': 0})[r['vote']] = r['n']
    return out

ADMIN_HTML = '''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Suggestions</title>
<style>body{font:15px system-ui;max-width:760px;margin:20px auto;padding:0 12px}.s{border:1px solid #ccc;border-radius:8px;padding:8px 10px;margin:8px 0}.s.ok{background:#e6f6ec}.m{color:#666;font-size:12px}button{margin-right:6px;padding:4px 10px}</style>
<h2>Suggestions de critères</h2><div id=l>Chargement…</div>
<script>
const T=new URLSearchParams(location.search).get('token')||prompt('Token admin');
async function load(){const r=await fetch('/api/admin?token='+encodeURIComponent(T));if(!r.ok){l.textContent='Accès refusé';return}
const d=await r.json();l.innerHTML='';d.suggestions.forEach(x=>{const e=document.createElement('div');e.className='s'+(x.approved?' ok':'');
e.innerHTML='<div class=m>#'+x.id+' · étape : <b></b> · '+new Date(x.ts*1000).toLocaleString()+' · '+(x.approved?'VISIBLE':'en attente')+'</div><div class=t></div>';
e.querySelector('b').textContent=x.node;e.querySelector('.t').textContent=x.text;
const a=document.createElement('button');a.textContent=x.approved?'Retirer':'Approuver';a.onclick=async()=>{await fetch('/api/admin/approve',{method:'POST',body:JSON.stringify({token:T,id:x.id,approved:!x.approved})});load()};e.append(a);l.append(e)});
if(!d.suggestions.length)l.textContent='Aucune suggestion.'}
load();
</script>'''

class H(BaseHTTPRequestHandler):
    server_version = 'cle/1.0'
    def log_message(self, *a): pass
    def ip(self):
        return self.headers.get('X-Forwarded-For', self.client_address[0]).split(',')[0].strip()
    def send(self, code, obj, ctype='application/json; charset=utf-8'):
        body = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', ORIGIN)
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.end_headers(); self.wfile.write(body)
    def do_OPTIONS(self): self.send(204, b'')
    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path == '/api/state':
            voter = (q.get('voter') or [''])[0]
            with db() as c:
                mine = {}
                if VOTER_RE.match(voter):
                    mine = {r['key']: r['vote'] for r in c.execute("SELECT key, vote FROM votes WHERE voter=?", (voter,))}
                extra = {}
                for r in c.execute("SELECT id, node, text FROM suggestions WHERE approved=1 ORDER BY id"):
                    extra.setdefault(r['node'], []).append({'id': r['id'], 'text': r['text']})
                return self.send(200, {'counts': counts_for(c), 'mine': mine, 'extra': extra})
        if u.path == '/api/admin' and ADMIN and (q.get('token') or [''])[0] == ADMIN:
            with db() as c:
                return self.send(200, {
                    'suggestions': [dict(r) for r in c.execute("SELECT * FROM suggestions ORDER BY id DESC LIMIT 500")],
                    'reports': [dict(r) for r in c.execute("SELECT * FROM reports ORDER BY id DESC LIMIT 500")]})
        if u.path == '/admin' and ADMIN:
            return self.send(200, ADMIN_HTML.encode(), 'text/html; charset=utf-8')
        if u.path in ('/', '/index.html') and STATIC:
            p = os.path.join(HERE, STATIC)
            if os.path.exists(p):
                return self.send(200, open(p, 'rb').read(), 'text/html; charset=utf-8')
        if u.path.startswith('/assets/'):
            name = os.path.basename(u.path)
            p = os.path.join(HERE, 'assets', name)
            if re.match(r'^[\w\-.]+\.(jpg|png|webp|svg)$', name) and os.path.isfile(p):
                ct = {'jpg': 'image/jpeg', 'png': 'image/png', 'webp': 'image/webp', 'svg': 'image/svg+xml'}[name.rsplit('.', 1)[1]]
                body = open(p, 'rb').read()
                self.send_response(200); self.send_header('Content-Type', ct); self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'public, max-age=604800'); self.end_headers(); self.wfile.write(body); return
        self.send(404, {'error': 'not found'})
    def do_POST(self):
        if too_many(self.ip()): return self.send(429, {'error': 'trop de requêtes'})
        try:
            n = int(self.headers.get('Content-Length', '0'))
            if n > 20000: return self.send(413, {'error': 'trop gros'})
            d = json.loads(self.rfile.read(n) or b'{}')
        except Exception:
            return self.send(400, {'error': 'json invalide'})
        if urlparse(self.path).path == '/api/admin/approve':
            if not ADMIN or d.get('token') != ADMIN: return self.send(403, {'error': 'interdit'})
            with LOCK, db() as c:
                c.execute("UPDATE suggestions SET approved=? WHERE id=?", (1 if d.get('approved') else 0, int(d.get('id', 0))))
            return self.send(200, {'ok': True})
        voter = str(d.get('voter', ''))
        if not VOTER_RE.match(voter): return self.send(400, {'error': 'voter invalide'})
        now = int(time.time()); p = urlparse(self.path).path
        with LOCK, db() as c:
            if p == '/api/vote':
                key = str(d.get('key', ''))[:300]; vote = d.get('vote')
                if not key or vote not in ('u', 'd', None): return self.send(400, {'error': 'paramètres'})
                if vote is None: c.execute("DELETE FROM votes WHERE voter=? AND key=?", (voter, key))
                else: c.execute("INSERT INTO votes(voter,key,vote,ts) VALUES(?,?,?,?) ON CONFLICT(voter,key) DO UPDATE SET vote=excluded.vote, ts=excluded.ts", (voter, key, vote, now))
                return self.send(200, {'counts': counts_for(c, key) or {key: {'u': 0, 'd': 0}}})
            if p == '/api/suggest':
                text = str(d.get('text', '')).strip()[:400]
                if not text: return self.send(400, {'error': 'vide'})
                c.execute("INSERT INTO suggestions(voter,node,text,ts) VALUES(?,?,?,?)", (voter, str(d.get('id', ''))[:100], text, now))
                return self.send(200, {'ok': True})
            if p == '/api/report':
                c.execute("INSERT INTO reports(voter,key,reason,comment,ts) VALUES(?,?,?,?,?)", (voter, str(d.get('key', ''))[:300], str(d.get('reason', ''))[:50], str(d.get('comment', ''))[:400], now))
                return self.send(200, {'ok': True})
        self.send(404, {'error': 'not found'})

if __name__ == '__main__':
    init()
    print(f'Serveur sur http://{HOST}:{PORT}  (base : {DB})')
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
