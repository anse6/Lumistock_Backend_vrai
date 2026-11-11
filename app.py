# app.py
# Triggering reload
import os
import time
from flask import Flask, jsonify
from flask_jwt_extended import JWTManager
from flask_mail import Mail
from config import Config
from flask_cors import CORS
from extensions import db, jwt, mail
from werkzeug.exceptions import HTTPException
from bson.errors import InvalidId
from flasgger import Swagger
from utils.json_provider import LumistockJSONProvider



def _fixer_fuseau(fuseau):
    """Heure locale du processus = celle des magasins, quel que soit le réglage
    du serveur (souvent UTC sur un VPS) : sinon une vente faite à 0 h 30 à
    Douala serait rangée la veille."""
    if fuseau and hasattr(time, "tzset"):
        os.environ["TZ"] = fuseau
        time.tzset()


def _verifier_secrets(app):
    """Refuse de démarrer sans clés de signature : sans elles, n'importe qui
    pourrait fabriquer des tokens de connexion ou des liens de compte."""
    for cle in ("SECRET_KEY", "JWT_SECRET_KEY"):
        valeur = app.config.get(cle)
        if not valeur or len(str(valeur)) < 32:
            raise RuntimeError(
                f"{cle} manquante ou trop courte (32 caractères minimum) : "
                "définissez-la dans backend/.env, par exemple avec "
                "python -c \"import secrets; print(secrets.token_hex(48))\"")
    if app.config["SECRET_KEY"] == app.config["JWT_SECRET_KEY"]:
        raise RuntimeError("SECRET_KEY et JWT_SECRET_KEY doivent être différentes.")


# Routes ouvertes à un caissier (tout le reste lui répond 403).
ROUTES_CAISSIER = (
    "/caisse/", "/auth/", "/users/me", "/users/upload_avatar", "/users/settings/barcode",
    "/ai/", "/livraisons/tva", "/uploads/",
    # Écran de vente partagé avec l'admin : choisir ou créer le client (les
    # autres routes /clients/ refusent le rôle caissier).
    "/clients/",
    # Choix des unités vendues d'un article suivi par numéro de série (sans coût).
    "/numeros-serie/disponibles/",
)


def create_app(config=None):
    """Factory pour créer et configurer l'application Flask."""
    
    app = Flask(__name__, template_folder='templates', static_folder='static')
    app.json_provider_class = LumistockJSONProvider
    app.json = app.json_provider_class(app)
    app.config.from_object(Config)
    _fixer_fuseau(app.config.get("APP_TIMEZONE"))
    if config is not None:
        if isinstance(config, dict):
            app.config.update(config)
        else:
            app.config.from_object(config)
    app.config.setdefault("JWT_VERIFY_SUB", False)
    _verifier_secrets(app)
    # Derrière nginx, l'IP du client est dans X-Forwarded-For : sans ce
    # réglage, toutes les requêtes semblent venir de 127.0.0.1 (limiteur de
    # tentatives et journal des connexions faussés). TRUSTED_PROXIES = nombre
    # de proxys de confiance devant l'application (1 avec nginx).
    proxies = int(app.config.get("TRUSTED_PROXIES", 0) or 0)
    if proxies > 0:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxies, x_proto=proxies)
    # Créer automatiquement le dossier d'upload si nécessaire
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    # Créer automatiquement le dossier pour les images de staff si nécessaire
    os.makedirs(app.config["STAFF_IMAGE_FOLDER"], exist_ok=True)


    # Initialisation des extensions avec l'app Flask
    db.init_app(app)
    jwt.init_app(app)
    mail.init_app(app)
    
    # CORS : autoriser le front React à communiquer avec le backend
    # IMPORTANT: CORS doit être initialisé AVANT les blueprints pour gérer les requêtes OPTIONS
    configured_origins = app.config.get("ALLOWED_ORIGINS", "*")
    if isinstance(configured_origins, str) and configured_origins != "*":
        configured_origins = [origin.strip() for origin in configured_origins.split(",") if origin.strip()]

    CORS(app,
         supports_credentials=True, 
         origins=configured_origins,
         methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
         # X-Assistant-Jeton : jeton secret du visiteur dans le chat du site public.
         allow_headers=["Content-Type", "Authorization", "X-Assistant-Jeton"],
         expose_headers=["Content-Type", "Authorization"]
    )
    
    # Enregistrement des Blueprints
    from routes.magasin import magasin_bp
    from routes.auth import auth_bp
    from routes.user_routes import user_bp
    from routes.staff_routes import staff_bp
    from routes.categorie_routes import categorie_bp
    from routes.produit_routes import produit_bp
    from routes.client_route import client_bp
    from routes.approvisionnement_routes import approvisionnement_bp
    from routes.livraison_routes import livraison_bp
    from routes.dashboard_routes import dashboard_bp
    from routes.notification_routes import notification_bp
    from routes.ristourne_routes import ristourne_bp
    from routes.retour_routes import retour_bp
    from routes.ai_routes import ai_bp
    from routes.fournisseur_routes import fournisseur_bp
    from routes.stock_livreur_routes import stock_livreur_bp
    from routes.charge_routes import charge_bp
    from routes.asset_routes import asset_bp
    from routes.compta_routes import compta_bp
    from routes.facture_fournisseur_routes import facture_fournisseur_bp
    from routes.location_routes import location_bp
    from routes.unite_routes import unite_bp
    from routes.rapport_routes import rapport_bp
    from routes.messagerie_routes import messagerie_bp
    from routes.caisse_routes import caisse_bp
    from routes.contact_routes import contact_bp
    from routes.assistant_routes import assistant_bp
    from routes.support_routes import support_bp
    from routes.numeros_serie_routes import numeros_serie_bp
    
    
    app.register_blueprint(user_bp)
    app.register_blueprint(magasin_bp)
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(staff_bp, url_prefix='/staff')
    app.register_blueprint(categorie_bp)   
    app.register_blueprint(produit_bp)
    app.register_blueprint(client_bp)
    #app.register_blueprint(commande_bp)
    app.register_blueprint(approvisionnement_bp)
    app.register_blueprint(livraison_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(notification_bp)
    app.register_blueprint(ristourne_bp)
    app.register_blueprint(retour_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(fournisseur_bp)
    app.register_blueprint(stock_livreur_bp, url_prefix='/stock-livreur')
    app.register_blueprint(charge_bp, url_prefix='/charges')
    app.register_blueprint(asset_bp, url_prefix='/assets')
    app.register_blueprint(compta_bp, url_prefix='/compta')
    app.register_blueprint(facture_fournisseur_bp)
    app.register_blueprint(location_bp)
    app.register_blueprint(unite_bp, url_prefix='/unites')
    app.register_blueprint(rapport_bp)
    app.register_blueprint(messagerie_bp)
    app.register_blueprint(caisse_bp)
    app.register_blueprint(contact_bp)
    app.register_blueprint(assistant_bp)
    app.register_blueprint(support_bp)
    app.register_blueprint(numeros_serie_bp)

    # Un caissier n'accède qu'à sa caisse : liste blanche de routes, vérifiée
    # avant toute autre chose. Même une route qui n'aurait qu'un @jwt_required
    # ne peut donc pas lui montrer les données du magasin (prix d'achat,
    # marges, finances, ventes des autres).
    @app.before_request
    def _limiter_le_caissier():
        from flask import request
        from flask_jwt_extended import decode_token

        entete = request.headers.get("Authorization", "")
        if request.method == "OPTIONS" or not entete.startswith("Bearer "):
            return None
        try:
            role = decode_token(entete[7:]).get("role")
        except Exception:
            return None  # jeton invalide ou expiré : la route le refusera elle-même
        if role == "caissier" and not request.path.startswith(ROUTES_CAISSIER):
            return jsonify({"error": "Accès réservé : un caissier n'a accès qu'à sa caisse."}), 403
        return None

    # Envois programmés et relances automatiques de la messagerie clients.
    from services import messagerie_planification

    @app.cli.command("messagerie-planifier")
    def messagerie_planifier():
        """Exécute une fois les envois programmés et les relances dues
        (à appeler par cron si le planificateur intégré est désactivé)."""
        messagerie_planification.tick()

    if app.config.get("MESSAGERIE_PLANIFICATEUR") and not app.config.get("TESTING"):
        messagerie_planification.demarrer_planificateur(app)

    
    
    # Vérification de chaque token de connexion, à chaque requête.
    @jwt.token_in_blocklist_loader
    def check_if_token_revoked(jwt_header, jwt_payload):
        # Un JWT reste techniquement valide après sa création. On refuse donc :
        # - un token révoqué à la déconnexion (liste en base, partagée par tous
        #   les workers et conservée après un redémarrage) ;
        # - un token émis avant un changement de mot de passe, une
        #   désactivation ou une « déconnexion partout » (token_version) ;
        # - un compte ou un magasin désactivé/archivé depuis la connexion.
        try:
            from models.user_model import UserModel
            from models.magasin_model import MagasinModel
            from models.revoked_token_model import RevokedTokenModel

            if RevokedTokenModel.est_revoque(jwt_payload.get("jti")):
                return True
            identity = jwt_payload.get("sub")
            if isinstance(identity, dict):
                identity = identity.get("user_id") or identity.get("_id")
            user = UserModel.get_user_by_id(identity)
            if not user or not user.get("is_active", False):
                return True
            if int(jwt_payload.get("tv", 0) or 0) != int(user.get("token_version", 0) or 0):
                return True
            magasin_id = user.get("magasin_id")
            if magasin_id:
                magasin = MagasinModel.get_magasin_by_id(str(magasin_id))
                if not magasin or not magasin.get("is_active", False):
                    return True
        except Exception:
            # En cas d'identité ou de base invalide, refuser le jeton plutôt
            # que d'autoriser un accès dont l'état ne peut être vérifié.
            return True
        return False

    @jwt.revoked_token_loader
    def revoked_token_callback(jwt_header, jwt_payload):
        return jsonify({"msg": "Session expirée ou révoquée. Veuillez vous reconnecter."}), 401

    # Gestion des erreurs globales
    @app.errorhandler(HTTPException)
    def handle_http_exception(error):
        return jsonify({"error": error.description or str(error)}), error.code

    # Deux opérations simultanées se sont bloquées (deadlock) ou un verrou a
    # trop attendu : MySQL a annulé l'opération, rien n'est enregistré. Le
    # client peut simplement réessayer.
    from sqlalchemy.exc import OperationalError

    @app.errorhandler(OperationalError)
    def handle_operational_error(error):
        code = getattr(getattr(error, "orig", None), "args", [None])[0]
        if code in (1205, 1213):  # lock wait timeout, deadlock
            app.logger.warning("Conflit de verrou MySQL (%s) sur %s", code, (getattr(error, "statement", None) or "")[:120])
            return jsonify({"error": "Une autre opération était en cours sur les mêmes données. "
                                     "Rien n'a été enregistré : veuillez réessayer."}), 503
        app.logger.exception("Erreur de base de données")
        return jsonify({"error": "La base de données est momentanément indisponible. Réessayez."}), 503

    # Gestion des erreurs MongoDB InvalidId
    @app.errorhandler(InvalidId)
    def handle_invalid_id(error):
        return jsonify({"error": "ID non valide."}), 400

    
    # Route pour la documentation de l'API
    @app.route('/')
    def docs():
        return jsonify({
            "message": "Documentation de l'API",
            "url": "/apidocs/"
        }), 200
    
    swagger = Swagger(app, template={
        "swagger": "2.0",
        "info": {
            "title": "Delivery Management API",
            "description": "API de gestion de livraison avec rôles, JWT, création de magasin, etc.",
            "version": "1.0.0"
        },
        "basePath": "/",
        "schemes": ["http", "https"],
        "securityDefinitions": {
            "Bearer": {
                "type": "apiKey",
                "name": "Authorization",
                "in": "header",
                "description": "JWT Authorization header using the Bearer scheme. Example: **'Authorization: Bearer &lt;token&gt;'**"
            }
        },
        "security": [
            {
                "Bearer": []
            }
        ]
    })

    
    from flask import send_from_directory, current_app
    # Route pour servir les fichiers uploadés
    @app.route("/uploads/images/<filename>")
    def uploaded_file(filename):
        return send_from_directory(current_app.config["UPLOAD_FOLDER"], filename)
    
    #Route pour servir les images de staff
    @app.route("/uploads/staff_images/<filename>")
    def staff_uploaded_file(filename):
        return send_from_directory(current_app.config["STAFF_IMAGE_FOLDER"], filename)


    return app

# Point d'entrée
# Ce bloc est ignoré en mode Docker, utilisé uniquement si on exécute `python app.py` manuellement
if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=3006, debug=True)
