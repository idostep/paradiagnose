# Clé d'identification – serveur de votes

Fichiers : `server.py` (serveur, Python 3, aucune dépendance), `index.html` (la page, légère, réglée pour parler à `/api` sur le même domaine), `assets/` (219 images, utilisées par la page). Copier le dossier `assets` en entier : `scp -r assets idostep@…:/var/www/paradiagnose/`.

## Lancer
    python3 server.py            # écoute sur 127.0.0.1:8765, base SQLite cle.db créée à côté

Le **port par défaut est 8765** (change-le avec `PORT=...`). Il écoute en local (`HOST=127.0.0.1`) : ton serveur web doit rediriger vers lui.

## Brancher ton serveur web (exemple nginx)
    location /api/ { proxy_pass http://127.0.0.1:8765; proxy_set_header X-Forwarded-For $remote_addr; }
    location /     { proxy_pass http://127.0.0.1:8765; }   # ou sers index.html toi-même

Si la page est sur un autre domaine que l'API : lance avec `ALLOWED_ORIGIN=https://ton-site.fr` et, dans `index.html`, remplace `const API='';` par `const API='https://domaine-de-l-api';`.

## Variables
`PORT` (8765) · `HOST` (127.0.0.1) · `DB` (cle.db) · `ALLOWED_ORIGIN` (*) · `ADMIN_TOKEN` (vide) · `STATIC` (index.html)

## Valider les suggestions de critères
Chaque suggestion est enregistrée avec `approved = 0` : **invisible** tant que tu ne l'as pas approuvée. Lance le serveur avec `ADMIN_TOKEN=un-secret`, ouvre `https://ton-domaine/admin?token=un-secret` et clique sur « Approuver » : le critère apparaît aussitôt dans l'étape concernée pour tous les visiteurs (avec un ✚), avec ses propres boutons de vote. « Retirer » le fait disparaître.
En SQL : `sqlite3 cle.db "update suggestions set approved=1 where id=3"`.

## Lire les suggestions et signalements
Définir `ADMIN_TOKEN=un-secret`, puis ouvrir `/api/admin?token=un-secret` (JSON). Ou directement : `sqlite3 cle.db "select * from suggestions"`.

## Règles actuelles (dans index.html)
Drapeau « distinctif » à 3 likes (les anciens drapeaux démarrent à 3) · critère masqué dès 1 dislike · critère du poly sorti dès 1 like.
Un visiteur = un identifiant anonyme stocké dans son navigateur (1 vote par critère et par navigateur).
