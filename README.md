# ComptaPièces

Application comptable locale pour Windows. La base et les écritures restent sur le PC. Les pièces justificatives sont envoyées vers Cloudinary privé quand `CLOUDINARY_URL` (ou les trois variables Cloudinary) est configuré ; sinon, elles restent dans `private_media/` sur le PC. Aucun compte ou mouvement de démonstration n’est généré.

## Démarrage

1. Installer Python 3.11 ou 3.12.
2. Double-cliquer sur `Lancer_ComptaPieces.bat`.
3. Au premier lancement, accepter l’installation des dépendances Python (Internet requis uniquement pour cette étape).
4. Dans le navigateur ouvert, créer le compte administrateur et la fiche de l’entreprise.
5. Pour les prochains lancements, le logiciel conserve la base dans `db.sqlite3`. Avec Cloudinary configuré, les pièces sont conservées dans le cloud privé ; sans Cloudinary, elles restent dans `private_media/`.

Pour activer Cloudinary, copie la variable complète `CLOUDINARY_URL` depuis le même environnement produit dans le fichier `.env` (ne la partage pas dans un message). Après modification, ferme puis relance le logiciel.

La fenêtre du terminal doit rester ouverte pendant l’utilisation. L’adresse est limitée à `127.0.0.1` : aucun accès externe n’est ouvert. La base SQLite et les fichiers sont réellement persistés sur le PC.

## Fonctions opérationnelles

- Première installation et inscription de l’administrateur ; connexion, déconnexion et changement de mot de passe.
- Plusieurs entreprises par compte, isolation des données entre entreprises et sélection de l’entreprise active.
- Comptes Comptable, Responsable et Auditeur, création d’utilisateurs avec mot de passe temporaire à usage unique, changement de rôle et suspension d’accès.
- Plan SYSCOHADA révisé complet (1 330 comptes) par entreprise, recherche par numéro ou intitulé, ajout de comptes personnalisés et contrôle d’unicité.
- Exercices comptables, création, clôture et blocage des écritures dans un exercice fermé.
- Saisie multi-lignes en partie double avec contrôle débit/crédit. Les écritures du comptable restent en attente ; le responsable les valide ou les rejette avant leur intégration. Les écritures comptabilisées sont conservées ; une correction crée une contrepassation.
- Journal filtrable par libellé, journal et dates ; détails, pièces et historique des actions.
- Import de PDF et d’images, capture depuis une caméra compatible, téléchargement authentifié et empreinte SHA-256.
- Export réel du journal en XLSX et PDF ; archive ZIP avec journal CSV et pièces.
- Sauvegarde d’entreprise téléchargeable au format ZIP, contenant ses comptes, écritures et pièces.
- Interface responsive, animations légères et indicateur de chargement.

## Règles de stockage et démarrage

La configuration par défaut utilise une vraie base SQLite locale, sans service cloud ni Internet pendant les opérations. Les pièces ne sont jamais servies par une URL publique : elles sont téléchargées après contrôle de l’utilisateur connecté et de l’entreprise. Une sauvegarde ZIP téléchargeable est disponible dans l’écran Exports.

Un serveur partagé PostgreSQL exige une configuration et un déploiement spécifiques. Ne pas exposer le serveur Django de développement sur Internet ; le lanceur Windows le lie uniquement à `127.0.0.1`.

## Déploiement Render + Neon

Le projet utilise un **Web Service Render** (application Django dynamique), Neon pour PostgreSQL et Cloudinary pour les pièces privées. Sur Render, utiliser `bash scripts/build_render.sh` comme commande de build et `python manage.py migrate --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --access-logfile -` comme commande de démarrage. Ajouter dans l’environnement Render `DATABASE_URL`, `CLOUDINARY_URL` (ou les trois variables Cloudinary), `DJANGO_SECRET_KEY` (générée par Render), `DJANGO_DEBUG=0` et un `DJANGO_SETUP_TOKEN` aléatoire généré par Render. Ce jeton autorise la toute première création du compte administrateur sur une base vide ; il n’est ni affiché ni enregistré. Render fournit automatiquement `RENDER_EXTERNAL_HOSTNAME`, pris en charge par Django pour l’hôte et le CSRF en HTTPS. La connexion est limitée à cinq essais incorrects par utilisateur et adresse IP sur quinze minutes.

Ne jamais committer `.env`, `db.sqlite3`, `private_media/` ni les clés de service. L’offre Render gratuite a un système de fichiers éphémère : SQLite et les fichiers locaux n’y sont pas persistants. Utiliser la base Neon et vérifier que les justificatifs partent bien dans le stockage Cloudinary avant tout essai avec des données réelles.

## Comptabilité et conformité

Le journal, le plan de comptes et la balance sont calculés sur les données persistées. Les états réglementaires détaillés, la TVA, la paie, la facturation, la gestion des stocks, les rapprochements bancaires et la certification fiscale ne sont pas encore inclus. Le logiciel ne doit pas être présenté comme certifié SYSCOHADA ou comme remplaçant la validation d’un professionnel comptable local.
