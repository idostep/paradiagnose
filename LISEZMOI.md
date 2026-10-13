# Clé d'identification – serveur de votes

Tout le contenu (arbre, critères, notes, confusions, glossaire, légendes d'images) est dans la base SQLite `cle.db` ; `content.json` ne sert qu'à la remplir au tout premier lancement (si les tables sont vides). La page se construit à l'ouverture depuis `/api/content`. Modifier un critère = `/admin` (onglet Critères) ou SQL ; plus besoin de regénérer la page. Les votes sont liés à l'identifiant du critère : on peut le reformuler sans perdre ses votes. Reste dans la page (pas dans la base) : l'onglet Rappels, les textes d'intro et le pied de page.

Fichiers : `server.py` (serveur, Python 3, aucune dépendance), `content.json` (contenu de départ), `index.html` (la page, légère, réglée pour parler à `/api` sur le même domaine), `assets/` (219 images, utilisées par la page). Copier le dossier `assets` en entier : `scp -r assets idostep@…:/var/www/paradiagnose/`.

## Lancer
    python3 server.py            # écoute sur 127.0.0.1:8765, base SQLite cle.db créée à côté

Le **port par défaut est 8765** (change-le avec `PORT=...`). Il écoute en local (`HOST=127.0.0.1`) : ton serveur web doit rediriger vers lui.

## Brancher ton serveur web (exemple nginx)
    location /api/ { proxy_pass http://127.0.0.1:8765; proxy_set_header X-Forwarded-For $remote_addr; }
    location /     { proxy_pass http://127.0.0.1:8765; }   # ou sers index.html toi-même

Si la page est sur un autre domaine que l'API : lance avec `ALLOWED_ORIGIN=https://ton-site.fr` et, dans `index.html`, remplace `const API='';` par `const API='https://domaine-de-l-api';`.

## Variables
`PORT` (8765) · `HOST` (127.0.0.1) · `DB` (cle.db) · `ALLOWED_ORIGIN` (*) · `ADMIN_TOKEN` (vide) · `STATIC` (index.html)

## Modifier les critères et valider les suggestions
Chaque suggestion est enregistrée avec `approved = 0` : **invisible** tant que tu ne l'as pas approuvée. Lance le serveur avec `ADMIN_TOKEN=un-secret`, ouvre `https://ton-domaine/admin?token=un-secret` onglet **Suggestions** : « Approuver » crée le critère (table `items`) et il apparaît aussitôt; onglet **Critères** : choisir une étape, modifier le texte, le groupe (œil nu / microscope / autre), « distinctif », « visible », ajouter un critère. Pour une suggestion approuvée : le critère apparaît aussitôt dans l'étape concernée pour tous les visiteurs (avec un ✚), avec ses propres boutons de vote. « Retirer » le fait disparaître.
En SQL : `sqlite3 cle.db "update suggestions set approved=1 where id=3"`.

## Lire les suggestions et signalements
Définir `ADMIN_TOKEN=un-secret`, puis ouvrir `/api/admin?token=un-secret` (JSON). Ou directement : `sqlite3 cle.db "select * from suggestions"`.

## Règles actuelles (dans index.html)
Drapeau « distinctif » à 3 likes (les anciens drapeaux démarrent à 3) · critère masqué dès 1 dislike · critère du poly sorti dès 1 like.
Un visiteur = un identifiant anonyme stocké dans son navigateur (1 vote par critère et par navigateur).

## Interface d'administration (`/admin`)
Lancer le serveur avec `ADMIN_TOKEN=un-mot-de-passe-long`, puis ouvrir `https://ton-domaine/admin` (le mot de passe est demandé ; `?token=...` marche aussi une fois, il est ensuite retiré de l'adresse).
- **Suggestions** : filtres En attente / Approuvées / Refusées. Pour chacune, tu peux corriger le texte, choisir le groupe (œil nu / microscope / autre) et « distinctif », puis Approuver (le critère devient visible pour tous), Refuser, Retirer, Remettre en attente ou Supprimer.
- **Signalements** : regroupés par critère, avec les motifs comptés, les commentaires, les likes/dislikes du critère. Boutons : Marquer traité, Masquer / Réafficher le critère, Modifier (ouvre l'étape), Supprimer.
- **Critères & votes** : choisir une étape ; modifier/ajouter/masquer ses critères, notes, compléments et confusions ; fixer le nombre de 👍 et 👎 affichés d'un critère (les vrais votes restent dans la base, l'écart est enregistré dans la table `adjust`) ou remettre ses votes à zéro.
