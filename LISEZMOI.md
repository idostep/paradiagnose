# Clé d'identification – serveur de votes

Tout le contenu (arbre, critères, notes, confusions, glossaire, légendes d'images) est dans la base SQLite `cle.db` ; `content.json` ne sert qu'à la remplir au tout premier lancement (si les tables sont vides). La page se construit à l'ouverture depuis `/api/content`. Modifier un critère = `/admin` (onglet Critères) ou SQL ; plus besoin de regénérer la page. Les votes sont liés à l'identifiant du critère : on peut le reformuler sans perdre ses votes. Reste dans la page (pas dans la base) : l'onglet Rappels, les textes d'intro et le pied de page.

Fichiers : `server.py` (serveur, Python 3, aucune dépendance), `content.json` (contenu de départ), `index.html` (la page, légère, réglée pour parler à `/api` sur le même domaine), `assets/` (219 images, utilisées par la page). Copier le dossier `assets` en entier : `scp -r assets idostep@…:/var/www/paradiagnose/`.

## Lancer
    python3 server.py            # écoute sur 127.0.0.1:8765, base SQLite cle.db créée à côté

Le **port par défaut est 8765** (change-le avec `PORT=...`). Il écoute en local (`HOST=127.0.0.1`) : ton serveur web doit rediriger vers lui.

## Brancher ton serveur web (exemple nginx)
    location / { proxy_pass http://127.0.0.1:8765; proxy_set_header X-Forwarded-For $remote_addr; proxy_set_header X-Forwarded-Proto $scheme; client_max_body_size 100m; }   # ou sers index.html toi-même

Si la page est sur un autre domaine que l'API : lance avec `ALLOWED_ORIGIN=https://ton-site.fr` et, dans `index.html`, remplace `const API='';` par `const API='https://domaine-de-l-api';`.

## Variables
`PORT` (8765) · `HOST` (127.0.0.1) · `DB` (cle.db) · `ALLOWED_ORIGIN` (*) · `STATIC` (index.html)

## Compte administrateur (login + mot de passe)
    python3 server.py adduser ido      # crée le compte (mot de passe demandé, jamais affiché)
    python3 server.py passwd ido       # change le mot de passe
    python3 server.py users            # liste · deluser NOM pour supprimer
Puis ouvrir `https://ton-domaine/admin` et se connecter. La session est un cookie (HttpOnly, Secure si le proxy envoie `X-Forwarded-Proto https`).

## Interface d'administration (`/admin`)
- **Suggestions** : date/heure de chaque proposition ; filtres En attente / Approuvées / Refusées ; corriger le texte, choisir le groupe et « distinctif », Approuver, Refuser, Retirer, Remettre en attente, Supprimer (un par un, par sélection, ou « tout purger »).
- **Signalements** : regroupés par critère (motifs, commentaires, likes/dislikes) ; marquer traité, masquer/réafficher le critère, supprimer.
- **Votes** : vue « Par critère » (avec le détail de qui a liké ou disliké, initiales et date) et « Par personne » (tout ce qu’une personne a voté) ; fixer le nombre de 👍/👎 affichés d'un critère (les vrais votes restent dans la base, l'écart va dans `adjust`) ou remettre à zéro.
- **Critères** : modifier/ajouter/masquer les critères d'une étape.
- **Base de données** : **Exporter en JSON** (`cle-AAAAMMJJ-HHMM.json` : `{format, version, tables:{nom:[{colonne:valeur}]}}`, lisible et facile à convertir si le format change) ou en `.db` (copie SQLite brute). **Importer** accepte l'un ou l'autre (case + confirmation) et remplace le contenu ; à l'import JSON, les colonnes inconnues sont ignorées et les colonnes absentes prennent leur valeur par défaut. L'ancienne base est d'abord copiée à côté : `cle.db.avant-import-…`. Les comptes admin ne sont jamais exportés et sont conservés à l'import.

## Initiales des visiteurs
À la première visite la page demande des initiales (2–8 lettres), gardées dans un cookie d’un an (et localStorage). Elles sont enregistrées dans la table `voters` et affichées dans l’admin à côté des suggestions et signalements ; un lien en bas de page permet de les modifier.

## Sauvegarde
Fais un export de temps en temps (ou `cp cle.db sauvegarde.db` serveur arrêté).

## Règles actuelles (dans index.html)
Drapeau « distinctif » à 3 likes · critère masqué quand likes − dislikes ≤ −3 · critère du poly sorti dès 1 like net.
Un visiteur = un identifiant anonyme stocké dans son navigateur (1 vote par critère, like et dislike exclusifs).
