# ComptaPièces

ComptaPièces est une application comptable web multi-entreprises, avec un client Windows qui ouvre le service dans une fenêtre dédiée. En production, les données sont stockées dans Neon/PostgreSQL et les pièces justificatives dans Cloudinary privé. Le mode local de développement utilise SQLite. Aucun compte ou mouvement de démonstration n’est généré.

## Démarrage

1. Installer Python 3.11 ou 3.12.
2. Double-cliquer sur `Lancer_ComptaPieces.bat`.
3. Au premier lancement, accepter l’installation des dépendances Python (Internet requis uniquement pour cette étape).
4. Dans le navigateur ouvert, créer le compte administrateur et la fiche de l’entreprise.
5. Pour les prochains lancements, le logiciel conserve la base dans `db.sqlite3`. Avec Cloudinary configuré, les pièces sont conservées dans le cloud privé ; sans Cloudinary, elles restent dans `private_media/`.

Pour activer Cloudinary, copie la variable complète `CLOUDINARY_URL` depuis le même environnement produit dans le fichier `.env` (ne la partage pas dans un message). Après modification, ferme puis relance le logiciel.

La fenêtre du terminal doit rester ouverte pendant l’utilisation. L’adresse est limitée à `127.0.0.1` : aucun accès externe n’est ouvert. La base SQLite et les fichiers sont réellement persistés sur le PC.

## Fonctions opérationnelles

- Inscription publique d’un cabinet ou d’une entreprise ; son créateur devient administrateur de cet espace.
- Un compte peut administrer plusieurs entreprises ; les membres et écritures restent isolés par entreprise.
- Console globale réservée aux administrateurs de la plateforme : vue des espaces inscrits et gestion des administrateurs globaux.
- Plusieurs entreprises par compte, isolation des données entre entreprises et sélection de l’entreprise active.
- Comptes Comptable, Responsable et Auditeur, création d’utilisateurs avec mot de passe temporaire à usage unique, changement de rôle et suspension d’accès.
- Plan SYSCOHADA révisé complet (1 330 comptes) par entreprise, recherche par numéro ou intitulé, ajout de comptes personnalisés et contrôle d’unicité.
- Exercices comptables, création, clôture et blocage des écritures dans un exercice fermé.
- Saisie multi-lignes en partie double avec contrôle débit/crédit. Les écritures du comptable restent en attente ; le responsable les valide ou les rejette avant leur intégration. Les écritures comptabilisées sont conservées ; une correction crée une contrepassation.
- Journal filtrable par numéro de pièce comptable généré, référence de facture, numéro de compte, libellé, journal et dates ; détails, pièces et historique des actions.
- Grand livre par compte avec solde antérieur et solde progressif débit moins crédit.
- Rapprochement bancaire manuel avec mouvements non rapprochés, calcul de l’écart et correction des états enregistrés.
- Import de PDF et d’images, capture depuis une caméra compatible, téléchargement authentifié et empreinte SHA-256.
- Export réel du journal en XLSX et PDF ; archive ZIP avec journal CSV et pièces.
- Sauvegarde d’entreprise téléchargeable au format ZIP, contenant ses comptes, écritures et pièces.
- Interface responsive, animations légères et indicateur de chargement.

## Règles de stockage et démarrage

En développement local, SQLite conserve les données sur le PC. En production, configurer PostgreSQL via `DATABASE_URL`. Les pièces Cloudinary ne sont jamais servies par une URL publique : elles sont téléchargées après contrôle de l’utilisateur connecté et de l’entreprise. Une sauvegarde ZIP téléchargeable est disponible dans l’écran Exports.

Un serveur partagé PostgreSQL exige une configuration et un déploiement spécifiques. Ne pas exposer le serveur Django de développement sur Internet ; le lanceur Windows le lie uniquement à `127.0.0.1`.

## Déploiement Render + Neon

Le projet utilise un **Web Service Render** (application Django dynamique), Neon pour PostgreSQL et Cloudinary pour les pièces privées. Sur Render, utiliser `bash scripts/build_render.sh` comme commande de build et `python manage.py migrate --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --access-logfile -` comme commande de démarrage. Ajouter dans l’environnement Render `DATABASE_URL`, `CLOUDINARY_URL` (ou les trois variables Cloudinary), `DJANGO_SECRET_KEY` générée par Render et `DJANGO_DEBUG=0`. Render fournit automatiquement `RENDER_EXTERNAL_HOSTNAME`, pris en charge par Django pour l’hôte et le CSRF en HTTPS. La connexion est limitée à cinq essais incorrects par utilisateur et adresse IP sur quinze minutes.

Les cabinets et entreprises s’inscrivent depuis `/inscription/`. Le compte qui termine l’inscription devient administrateur de son espace et peut ajouter d’autres entreprises. Pour créer le premier administrateur global de la plateforme, définir temporairement dans Render `PLATFORM_ADMIN_SETUP_TOKEN` avec un secret aléatoire d’au moins 32 caractères. Si `DJANGO_SETUP_TOKEN` est encore présent, il peut servir temporairement à cette initialisation. Ouvrir `/plateforme/installation/`, saisir le jeton et choisir un nouveau mot de passe. Après création, supprimer le jeton temporaire de Render. La console globale se trouve ensuite à `/plateforme/`; elle n’est accessible qu’aux comptes administrateurs globaux.

## Client Windows

Le client Windows ouvre le service hébergé dans une fenêtre dédiée. Il nécessite Internet et le runtime Microsoft Edge WebView2 ; les données restent dans Neon et les pièces dans Cloudinary. Pour reconstruire l’installateur sur Windows avec Python 3.11, PyInstaller et Inno Setup 6, exécuter `powershell -ExecutionPolicy Bypass -File desktop/build_windows.ps1`. L’installeur se trouve ensuite dans `dist/ComptaPieces-Setup-1.0.0.exe`. Ce client n’est pas encore un logiciel hors ligne.

Ne jamais committer `.env`, `db.sqlite3`, `private_media/` ni les clés de service. L’offre Render gratuite a un système de fichiers éphémère : SQLite et les fichiers locaux n’y sont pas persistants. Utiliser la base Neon et vérifier que les justificatifs partent bien dans le stockage Cloudinary avant tout essai avec des données réelles.

## Comptabilité et conformité

Le journal, le grand livre, le rapprochement et la balance sont calculés sur les données persistées. Les états réglementaires détaillés, la TVA, la paie, la facturation, la gestion des stocks et la certification fiscale ne sont pas encore inclus. Le logiciel ne doit pas être présenté comme certifié SYSCOHADA ou comme remplaçant la validation d’un professionnel comptable local.
