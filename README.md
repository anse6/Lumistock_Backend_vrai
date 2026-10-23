# 🛵 Lumistock - Backend de l'application de gestion de Stock

Bienvenue dans le backend de **Lumistock**, une application intelligente de gestion de stock conçue pour les boutiquiers, magasins et clients. Cette API REST robuste permet la gestion des stock, manageurs, administrateur, des clients, des statistiques commerciales, ainsi que l’analyse des zones de performance.

---

## 📁 Structure du projet

Lumistock-backend/
├── app.py # Entrée principale de l’application Flask
├── config.py # Configuration générale
├── dockerfile # Dockerfile pour la containerisation
├── env/ # Environnement virtuel (à ne pas versionner)
├── extensions.py # Extensions Flask (JWT, Mongo, etc.)
├── local.yml # Configuration locale MySQL + application
├── makefile # Commandes make pour le build et le lancement
├── models/ # Modèles de données via la couche SQL compatible
├── requirements.txt # Dépendances Python
├── routes/ # Fichiers de routes Flask (API)
├── run.py # Script de lancement principa
├── tests/ # Repertoire contenant les tests unitaires et d'intégration
├── schemas/ # Schémas de validation (Marshmallow)
├── services/ # Logique métier (statistiques, traitement, etc.)
├── uploads/ # Dossier pour les fichiers téléversés
├── utils/ # Fonctions utilitaires
└── README.md # Ce fichier

## 🚀 Lancement de l'application

### ▶️ Exécution en local

**Base de données**
Le backend utilise MySQL/MariaDB. La migration crée les tables et leurs index
SQL, sans renommer le contrat historique de l'API (`_id` reste `_id`).

````
2. **Créer et activer l’environnement virtuel**
```bash
python3 -m venv env
source env/bin/activate
````

3. **Installer les dépendances**

```bash
pip install -r requirements.txt
```

4. **Lancer le serveur**

```bash
python run.py
```

Initialiser ou vérifier le schéma :

```bash
python -m scripts.migrate_database
python -m scripts.migrate_database --check
```

# ou

```bash
flask run
```

### 🐳 Exécution avec Docker

1.  **Construire et démarrer le conteneur**

```bash
make build
```

### 🔒 Authentification & Sécurité

    JWT (JSON Web Tokens) est utilisé pour sécuriser les routes et authentifier les utilisateurs.

    Les utilisateurs peuvent être de différents types : admin, manager, livreur.

📦 Fonctionnalités clés

    📊 Statistiques dynamiques :

        Chiffre d’affaires total

        Nombre total de livraisons

        Nombre de clients uniques

        Nombre de produits différents

        Évolution par période (jour/semaine/mois/année)

        Répartition par zones (villes, quartiers)

    📍 Analyse géographique :

        Zones de vente maximale (top villes/quartiers)

        Répartition des revenus par secteur

🔧 Dépendances principales

    Flask

    Flask-JWT-Extended

    SQLAlchemy

    Marshmallow

    PyYAML

    Docker (optionnel)

    Python 3.10+

🧪 Tests

🧠 Bonnes pratiques

    Toutes les routes sont regroupées dans le dossier routes/.

    La logique métier est séparée dans services/ pour assurer une architecture propre.

    schemas/ permet la validation et sérialisation des données.

    models/ contient les interfaces historiques exposées par la couche SQL.

    utils/ regroupe les fonctions utilitaires réutilisables.

👨‍💻 Auteurs

Développé par l’équipe Dev de Klivar
Un projet conçu avec passion pour une logistique plus fluide et intelligente.
🌐 Dépôt GitHub
