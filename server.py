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

TOUT LE CONTENU (arbre, critères, notes, glossaire, images) est dans la base SQLite :
  tables meta, nodes, items, node_images, glossary (+ votes, suggestions, reports).
  Au premier lancement, la base est remplie depuis content.json (à côté de server.py).
  La page se construit à l'ouverture depuis GET /api/content.

API :
  GET  /api/content                     -> tout le contenu affichable (critères masqués exclus)
  GET  /api/state?voter=ID              -> {"counts":{clé:{"u":n,"d":n}}, "mine":{clé:"u"|"d"}}
  POST /api/vote     {voter,key,vote}   vote = "u" | "d" | null (retirer)
  POST /api/suggest  {voter,id,text}    (créée avec approved=0 : invisible tant que tu ne l'approuves pas)
  POST /api/report   {voter,key,reason,comment}
  GET  /admin                           page d'administration (ADMIN_TOKEN requis) : approuver / retirer les suggestions
  POST /api/admin/approve {token,id,approved}   approuver = créer le critère (visible pour tous)
  GET  /api/admin/nodes | /api/admin/items?node=ID ; POST /api/admin/item | /api/admin/node  (édition des critères)
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
        try: c.execute("ALTER TABLE suggestions ADD COLUMN item_id INTEGER")
        except sqlite3.OperationalError: pass
        c.executescript("""
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS nodes(id TEXT PRIMARY KEY, t TEXT, lat INTEGER DEFAULT 0, sexe TEXT, fam TEXT, tp INTEGER, ev TEXT, poly TEXT, bio TEXT, via TEXT);
        CREATE TABLE IF NOT EXISTS node_kids(parent TEXT, kid TEXT, pos INTEGER, PRIMARY KEY(parent,kid));  -- un nœud peut avoir plusieurs parents
        -- kind : tab (critères d'identification) | moi (compléments) | note | conf (confusions)
        -- grp : o (œil nu / loupe) | m (microscope) | a (autres) ; dist = critère distinctif au départ ; visible = affiché
        CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY AUTOINCREMENT, node_id TEXT, kind TEXT, pos INTEGER DEFAULT 0, text TEXT, grp TEXT DEFAULT 'a', dist INTEGER DEFAULT 0, visible INTEGER DEFAULT 1, source TEXT DEFAULT 'base');
        CREATE INDEX IF NOT EXISTS items_node ON items(node_id);
        CREATE TABLE IF NOT EXISTS node_images(node_id TEXT, pos INTEGER, key TEXT, caption TEXT);
        CREATE TABLE IF NOT EXISTS glossary(term TEXT PRIMARY KEY, data TEXT);
        """)
        if not c.execute("SELECT 1 FROM nodes LIMIT 1").fetchone():
            p = os.path.join(HERE, 'content.json')
            if os.path.exists(p): seed(c, p)

def seed(c, path):
    d = json.load(open(path, encoding='utf-8'))
    for k in ('ROOT', 'FAMS', 'TPS', 'TPSHORT'):
        c.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, json.dumps(d[k], ensure_ascii=False)))
    for nid, n in d['N'].items():
        for i, k in enumerate(n.get('kids', [])): c.execute("INSERT INTO node_kids VALUES(?,?,?)", (nid, k, i))
        c.execute("INSERT INTO nodes(id,t,lat,sexe,fam,tp,ev,poly,bio,via) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (nid, n.get('t'), 1 if n.get('lat') else 0, n.get('sexe'), n.get('fam'), n.get('tp'), n.get('ev'), n.get('poly'), n.get('bio'), n.get('via')))
        for kind in ('tab', 'moi', 'note', 'conf'):
            for i, text in enumerate(n.get(kind, [])):
                code = d['CRIT'].get(nid, {}).get(text, 'a') if kind == 'tab' else 'a'
                c.execute("INSERT INTO items(node_id,kind,pos,text,grp,dist) VALUES(?,?,?,?,?,?)", (nid, kind, i, text, code[0], 1 if code.endswith('!') else 0))
        for i, (k, cap) in enumerate(n.get('img', [])):
            c.execute("INSERT INTO node_images VALUES(?,?,?,?)", (nid, i, k, cap))
    for term, g in d['GLOSS'].items():
        c.execute("INSERT INTO glossary VALUES(?,?)", (term, json.dumps(g, ensure_ascii=False)))

def build_content(c):
    meta = {r['key']: json.loads(r['value']) for r in c.execute("SELECT * FROM meta")}
    N = {}
    for r in c.execute("SELECT * FROM nodes"):
        n = {'t': r['t'], 'kids': []}
        if r['lat']: n['lat'] = True
        for f in ('sexe', 'fam', 'tp', 'ev', 'poly', 'bio', 'via'):
            if r[f] not in (None, ''): n[f] = r[f]
        N[r['id']] = n
    for r in c.execute("SELECT parent, kid FROM node_kids ORDER BY parent, pos"):
        if r['parent'] in N: N[r['parent']]['kids'].append(r['kid'])
    CRIT, TABID = {}, {}
    field = {'tab': 'tab', 'moi': 'moi', 'note': 'note', 'conf': 'conf'}
    for r in c.execute("SELECT * FROM items WHERE visible=1 ORDER BY node_id, kind, pos, id"):
        n = N.get(r['node_id'])
        if n is None: continue
        n.setdefault(field[r['kind']], []).append(r['text'])
        if r['kind'] == 'tab':
            CRIT.setdefault(r['node_id'], {})[r['text']] = r['grp'] + ('!' if r['dist'] else '')
            TABID.setdefault(r['node_id'], {})[r['text']] = r['id']
    for r in c.execute("SELECT * FROM node_images ORDER BY node_id, pos"):
        if r['node_id'] in N: N[r['node_id']].setdefault('img', []).append([r['key'], r['caption']])
    GLOSS = {r['term']: json.loads(r['data']) for r in c.execute("SELECT * FROM glossary")}
    return {'ROOT': meta.get('ROOT', 'root'), 'N': N, 'FAMS': meta.get('FAMS', {}), 'TPS': meta.get('TPS', {}), 'TPSHORT': meta.get('TPSHORT', {}), 'CRIT': CRIT, 'TABID': TABID, 'GLOSS': GLOSS}

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

ADMIN_HTML = '''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Administration</title>
<style>body{font:15px system-ui;max-width:860px;margin:20px auto;padding:0 12px}h2{margin:18px 0 6px}.s{border:1px solid #ccc;border-radius:8px;padding:8px 10px;margin:8px 0}.s.ok{background:#e6f6ec}.m{color:#666;font-size:12px}button{margin-right:6px;padding:4px 10px}
nav button{font-weight:700}input[type=text],select{font:inherit;padding:3px 6px}.r{display:flex;gap:6px;align-items:center;margin:4px 0;flex-wrap:wrap}.r input[type=text]{flex:1;min-width:240px}.h{opacity:.5}</style>
<h2>Administration</h2><nav><button onclick="show('sg')">Suggestions</button><button onclick="show('ed')">Critères</button><button onclick="show('rp')">Signalements</button></nav>
<div id=sg></div><div id=ed hidden><div class=r><select id=nsel></select><button onclick="loadItems()">Ouvrir</button></div><div id=nbody></div></div><div id=rp hidden></div>
<script>
const T=new URLSearchParams(location.search).get('token')||prompt('Token admin');
const q=p=>fetch(p+(p.includes('?')?'&':'?')+'token='+encodeURIComponent(T)).then(r=>r.json());
const post=(p,d)=>fetch(p,{method:'POST',body:JSON.stringify(Object.assign({token:T},d))}).then(r=>r.json());
function show(k){['sg','ed','rp'].forEach(x=>document.getElementById(x).hidden=x!==k)}
const el=(t,a={},...k)=>{const e=document.createElement(t);Object.assign(e,a);k.forEach(x=>e.append(x));return e};
async function loadSug(){const d=await q('/api/admin');sg.innerHTML='<h3>Suggestions des visiteurs</h3>';
d.suggestions.forEach(x=>{const e=el('div',{className:'s'+(x.approved?' ok':'')});e.append(el('div',{className:'m',textContent:'#'+x.id+' · étape : '+x.node+' · '+new Date(x.ts*1000).toLocaleString()+' · '+(x.approved?'VISIBLE':'en attente')}),el('div',{textContent:x.text}),
el('button',{textContent:x.approved?'Retirer':'Approuver',onclick:async()=>{await post('/api/admin/approve',{id:x.id,approved:!x.approved});loadSug()}}));sg.append(e)});
if(!d.suggestions.length)sg.append('Aucune suggestion.');
rp.innerHTML='<h3>Signalements</h3>';d.reports.forEach(x=>rp.append(el('div',{className:'s'},el('div',{className:'m',textContent:'#'+x.id+' · '+x.reason+' · '+new Date(x.ts*1000).toLocaleString()}),el('div',{textContent:x.text||x.key}),el('div',{className:'m',textContent:x.comment||''}))));
if(!d.reports.length)rp.append('Aucun signalement.')}
async function loadNodes(){const n=await q('/api/admin/nodes');n.forEach(x=>nsel.append(el('option',{value:x.id,textContent:x.t+' ('+x.id+')'})))}
const KINDS={tab:'Critères',moi:'Compléments',note:'Notes',conf:'Confusions'};
async function loadItems(){const d=await q('/api/admin/items?node='+encodeURIComponent(nsel.value));nbody.innerHTML='';
const id=nsel.value;const nr=el('div',{className:'r'});const ti=el('input',{type:'text',value:d.node.t||''});const bi=el('input',{type:'text',value:d.node.bio||'',placeholder:'hôte / importance'});
nr.append('Nom :',ti,bi,el('button',{textContent:'Enregistrer',onclick:async()=>{await post('/api/admin/node',{id,t:ti.value,bio:bi.value});alert('ok')}}));nbody.append(nr);
Object.keys(KINDS).forEach(k=>{nbody.append(el('h3',{textContent:KINDS[k]}));d.items.filter(i=>i.kind===k).forEach(i=>nbody.append(row(i,k,id)));nbody.append(row({text:'',grp:'a',dist:0,visible:1},k,id,true))})}
function row(i,k,node,isNew){const r=el('div',{className:'r'+(i.visible?'':' h')});const t=el('input',{type:'text',value:i.text,placeholder:isNew?'Nouveau…':''});
const g=el('select');[['o','œil nu'],['m','microscope'],['a','autre']].forEach(([v,l])=>g.append(el('option',{value:v,textContent:l,selected:i.grp===v})));
const d=el('input',{type:'checkbox',checked:!!i.dist}),v=el('input',{type:'checkbox',checked:!!i.visible});
r.append(t);if(k==='tab')r.append(g,el('label',{},d,' distinctif'));r.append(el('label',{},v,' visible'),el('button',{textContent:isNew?'Ajouter':'Enregistrer',onclick:async()=>{if(!t.value.trim())return;
await post('/api/admin/item',{id:i.id,node,kind:k,text:t.value,grp:g.value,dist:d.checked,visible:v.checked});loadItems()}}));return r}
loadSug();loadNodes();
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
        if u.path == '/api/content':
            with db() as c:
                self.send_response(200)
                body = json.dumps(build_content(c), ensure_ascii=False).encode()
                self.send_header('Content-Type', 'application/json; charset=utf-8'); self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-cache'); self.send_header('Access-Control-Allow-Origin', ORIGIN)
                self.end_headers(); self.wfile.write(body); return
        if u.path.startswith('/api/admin/') and ADMIN and (q.get('token') or [''])[0] == ADMIN:
            with db() as c:
                if u.path == '/api/admin/nodes':
                    return self.send(200, [dict(r) for r in c.execute("SELECT id, t, fam FROM nodes ORDER BY t")])
                if u.path == '/api/admin/items':
                    node = (q.get('node') or [''])[0]
                    return self.send(200, {'node': dict(c.execute("SELECT * FROM nodes WHERE id=?", (node,)).fetchone() or {}),
                        'items': [dict(r) for r in c.execute("SELECT * FROM items WHERE node_id=? ORDER BY kind, pos, id", (node,))]})
        if u.path == '/api/state':
            voter = (q.get('voter') or [''])[0]
            with db() as c:
                mine = {}
                if VOTER_RE.match(voter):
                    mine = {r['key']: r['vote'] for r in c.execute("SELECT key, vote FROM votes WHERE voter=?", (voter,))}
                return self.send(200, {'counts': counts_for(c), 'mine': mine})
        if u.path == '/api/admin' and ADMIN and (q.get('token') or [''])[0] == ADMIN:
            with db() as c:
                return self.send(200, {
                    'suggestions': [dict(r) for r in c.execute("SELECT * FROM suggestions ORDER BY id DESC LIMIT 500")],
                    'reports': [dict(r, text=(c.execute("SELECT text FROM items WHERE id=?", (int(r['key'][1:]),)).fetchone() or [None])[0] if str(r['key']).startswith('c') and str(r['key'])[1:].isdigit() else r['key']) for r in c.execute("SELECT * FROM reports ORDER BY id DESC LIMIT 500")]})
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
        pth = urlparse(self.path).path
        if pth.startswith('/api/admin/'):
            if not ADMIN or d.get('token') != ADMIN: return self.send(403, {'error': 'interdit'})
            with LOCK, db() as c:
                if pth == '/api/admin/approve':
                    sid = int(d.get('id', 0)); r = c.execute("SELECT * FROM suggestions WHERE id=?", (sid,)).fetchone()
                    if not r: return self.send(404, {'error': 'introuvable'})
                    if d.get('approved'):
                        if r['item_id']: c.execute("UPDATE items SET visible=1 WHERE id=?", (r['item_id'],))
                        else:
                            pos = (c.execute("SELECT COALESCE(MAX(pos),0)+1 FROM items WHERE node_id=? AND kind='tab'", (r['node'],)).fetchone()[0])
                            cur = c.execute("INSERT INTO items(node_id,kind,pos,text,grp,dist,visible,source) VALUES(?,?,?,?,?,0,1,'visiteur')", (r['node'], 'tab', pos, r['text'], 'a'))
                            c.execute("UPDATE suggestions SET item_id=? WHERE id=?", (cur.lastrowid, sid))
                    elif r['item_id']: c.execute("UPDATE items SET visible=0 WHERE id=?", (r['item_id'],))
                    c.execute("UPDATE suggestions SET approved=? WHERE id=?", (1 if d.get('approved') else 0, sid))
                    return self.send(200, {'ok': True})
                if pth == '/api/admin/item':
                    text = str(d.get('text', '')).strip()[:600]; kind = d.get('kind', 'tab')
                    if kind not in ('tab', 'moi', 'note', 'conf') or not text: return self.send(400, {'error': 'paramètres'})
                    grp = d.get('grp') if d.get('grp') in ('o', 'm', 'a') else 'a'
                    vals = (text, kind, grp, 1 if d.get('dist') else 0, 0 if d.get('visible') is False or d.get('visible') == 0 else 1)
                    if d.get('id'):
                        c.execute("UPDATE items SET text=?, kind=?, grp=?, dist=?, visible=? WHERE id=?", vals + (int(d['id']),))
                        return self.send(200, {'id': int(d['id'])})
                    node = str(d.get('node', ''))
                    if not c.execute("SELECT 1 FROM nodes WHERE id=?", (node,)).fetchone(): return self.send(404, {'error': 'étape inconnue'})
                    pos = c.execute("SELECT COALESCE(MAX(pos),0)+1 FROM items WHERE node_id=? AND kind=?", (node, kind)).fetchone()[0]
                    cur = c.execute("INSERT INTO items(text,kind,grp,dist,visible,node_id,pos,source) VALUES(?,?,?,?,?,?,?,'admin')", vals + (node, pos))
                    return self.send(200, {'id': cur.lastrowid})
                if pth == '/api/admin/node':
                    nid = str(d.get('id', ''))
                    for f in ('t', 'bio', 'fam', 'ev', 'sexe', 'via'):
                        if f in d: c.execute(f"UPDATE nodes SET {f}=? WHERE id=?", (str(d[f]) if d[f] not in (None, '') else None, nid))
                    return self.send(200, {'ok': True})
            return self.send(404, {'error': 'not found'})
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
