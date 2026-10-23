"""Routes de la caisse : vente au comptoir, sessions de caisse, statistiques du caissier,
et suivi des caisses par l'admin ou le manager.

Toutes les données sont limitées au magasin de l'utilisateur connecté ; un
caissier ne voit que ses propres ventes et sessions.
"""
from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from models.caisse_session_model import CaisseSessionModel
from models.livraison_model import LivraisonModel
from models.magasin_model import MagasinModel
from models.user_model import UserModel
from services import caisse_service as caisse
from services.caisse_service import ErreurCaisse
from utils.decorators import role_required
from utils.decorators import permission_requise

caisse_bp = Blueprint("caisse", __name__, url_prefix="/caisse")


def _moi():
    user = UserModel.get_user_by_id(get_jwt_identity())
    if not user or not user.get("magasin_id"):
        raise ErreurCaisse("Utilisateur non associé à un magasin.", 403)
    return user


@caisse_bp.errorhandler(ErreurCaisse)
def _erreur_caisse(erreur):
    return jsonify({"error": erreur.message}), erreur.statut


# --------------------------------------------------------------------------- contexte
def _texte(valeur):
    return valeur.strip() if isinstance(valeur, str) and valeur.strip() else None


def _magasin_ticket(magasin):
    """En-tête du ticket de caisse : identité et mentions légales du magasin (profil, Paramètres)."""
    # Adresse sur plusieurs lignes, sans répéter un morceau déjà écrit (ex. la ville dans la ligne 1).
    lignes = []
    for morceau in (magasin.get("adresse_ligne_1") or magasin.get("adresse"), magasin.get("adresse_ligne_2"),
                    magasin.get("quartier"),
                    ", ".join(filter(None, [" ".join(filter(None, [_texte(magasin.get("code_postal")),
                                                                    _texte(magasin.get("ville"))])),
                                            _texte(magasin.get("pays"))]))):
        morceau = _texte(morceau)
        if morceau and not any(morceau.lower() in deja.lower() for deja in lignes):
            lignes.append(morceau)
    return {
        "denomination": magasin.get("denomination"),
        "raison_sociale": _texte(magasin.get("raison_sociale")),
        "forme_juridique": _texte(magasin.get("forme_juridique")),
        "telephone": _texte(magasin.get("telephone")), "email": _texte(magasin.get("email")),
        "adresse": ", ".join(lignes) or None,
        "adresse_lignes": lignes,
        # Le profil enregistre numero_immatriculation (RCCM) et numero_fiscal (NIU).
        "niu": _texte(magasin.get("numero_fiscal")) or _texte(magasin.get("niu")),
        "rccm": _texte(magasin.get("numero_immatriculation")) or _texte(magasin.get("rccm")),
        "devise": str(magasin.get("devise") or "XAF").upper(),
        "logo": f"{request.host_url}uploads/images/{magasin['logo']}" if magasin.get("logo") else None,
    }


@caisse_bp.route("/contexte", methods=["GET"])
@jwt_required()
@role_required("caissier")
def contexte():
    """
    Contexte de l'écran de caisse : caissier, magasin (pour le ticket), paramètres, session ouverte.
    ---
    tags: [Caisse]
    responses:
      200: {description: Contexte de la caisse}
    """
    from services.tva_service import tva_du_magasin

    user = _moi()
    magasin = MagasinModel.get_magasin_by_id(str(user["magasin_id"])) or {}
    session = CaisseSessionModel.session_ouverte(user["_id"])
    return jsonify({
        "caissier": {"id": str(user["_id"]), "nom": caisse._nom(user), "email": user.get("email"),
                     "barcode_mode": bool(user.get("barcode_mode", True))},
        "magasin": _magasin_ticket(magasin),
        "parametres": caisse.parametres_caisse(magasin),
        "tva": tva_du_magasin(str(user["magasin_id"])),
        "client_de_passage_id": caisse.client_de_passage_id(user["magasin_id"]),
        "session": caisse.serialiser_session(session) if session else None,
    }), 200


@caisse_bp.route("/catalogue", methods=["GET"])
@jwt_required()
@role_required("caissier", "admin", "manager")
def catalogue():
    """
    Produits vendables par catégorie (prix de vente, stock, conditionnements ; jamais le prix d'achat).
    ---
    tags: [Caisse]
    responses:
      200: {description: Catalogue}
    """
    return jsonify(caisse.catalogue(_moi()["magasin_id"])), 200


# --------------------------------------------------------------------------- clients
@caisse_bp.route("/clients", methods=["GET"])
@jwt_required()
@role_required("caissier")
def clients():
    """
    Recherche de clients enregistrés (nom ou téléphone) pour une vente.
    ---
    tags: [Caisse]
    parameters:
      - {in: query, name: q, type: string}
    responses:
      200: {description: Clients}
    """
    return jsonify(caisse.clients(_moi()["magasin_id"], request.args.get("q"))), 200


@caisse_bp.route("/clients", methods=["POST"])
@jwt_required()
@role_required("caissier")
def creer_client():
    """
    Création rapide d'un client depuis la caisse (nom obligatoire, téléphone conseillé).
    ---
    tags: [Caisse]
    responses:
      201: {description: Client créé}
    """
    from services.client_service import create_client_service

    user = _moi()
    data = request.get_json(silent=True) or {}
    nom = str(data.get("nom") or "").strip()
    if not nom:
        raise ErreurCaisse("Le nom du client est requis.")
    resultat, statut = create_client_service({
        "nom": nom[:120], "telephone": str(data.get("telephone") or "").strip() or None,
        "magasin_id": str(user["magasin_id"]), "created_by": str(user["_id"]), "montant_en_caisse": 0,
    })
    if statut != 201:
        raise ErreurCaisse(resultat.get("message") or resultat.get("error") or "Le client n'a pas pu être créé.", statut)
    return jsonify({"id": resultat["client_id"], "nom": nom, "telephone": data.get("telephone") or "", "credit": 0}), 201


# --------------------------------------------------------------------------- ventes
@caisse_bp.route("/apercu", methods=["POST"])
@jwt_required()
@role_required("caissier")
def apercu():
    """
    Total exact d'un panier (réductions et TVA comprises), calculé par le serveur.
    ---
    tags: [Caisse]
    responses:
      200: {description: Montants du panier}
    """
    return jsonify(caisse.apercu(request.get_json(silent=True), _moi())), 200


@caisse_bp.route("/ventes", methods=["POST"])
@jwt_required()
@role_required("caissier")
def vendre():
    """
    Enregistre une vente au comptoir (caisse ouverte obligatoire).
    ---
    tags: [Caisse]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            produits: {type: array, items: {type: object}}
            client_id: {type: string}
            statut_paiement: {type: string, enum: ["payé", "partiellement payé", "non payé"]}
            paiements: {type: array, items: {type: object, properties: {methode: {type: string}, montant: {type: number}}}}
            reductions: {type: array, items: {type: object}}
            appliquer_tva: {type: boolean}
    responses:
      201: {description: Vente enregistrée (données du ticket)}
      400: {description: Vente refusée}
      409: {description: Caisse fermée ou stock insuffisant}
    """
    return jsonify(caisse.vendre(request.get_json(silent=True), _moi())), 201


@caisse_bp.route("/ventes", methods=["GET"])
@jwt_required()
@role_required("caissier")
def mes_ventes():
    """
    Ventes du caissier connecté sur une période.
    ---
    tags: [Caisse]
    parameters:
      - {in: query, name: periode, type: string, enum: [jour, semaine, mois, personnalisee]}
      - {in: query, name: debut, type: string}
      - {in: query, name: fin, type: string}
      - {in: query, name: statut, type: string, enum: [payee, credit, annulee]}
      - {in: query, name: q, type: string}
    responses:
      200: {description: Ventes et totaux}
    """
    a = request.args
    return jsonify(caisse.historique(_moi(), a.get("periode", "jour"), a.get("debut"), a.get("fin"),
                                     a.get("statut"), a.get("q"))), 200


@caisse_bp.route("/ventes/<vente_id>", methods=["GET"])
@jwt_required()
@role_required("caissier")
def ma_vente(vente_id):
    """
    Détail d'une vente du caissier (pour réimprimer le ticket).
    ---
    tags: [Caisse]
    responses:
      200: {description: Vente}
      404: {description: Vente introuvable}
    """
    return jsonify(caisse.serialiser_vente(caisse._vente_du_caissier(_moi(), vente_id, inclure_annulees=True))), 200


@caisse_bp.route("/tickets/<vente_id>", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def ticket_vente(vente_id):
    """
    Ticket de caisse (80 mm) d'une vente du magasin, pour l'admin et le manager.
    ---
    tags: [Caisse]
    description: >
      Mêmes données que le ticket imprimé par le caissier (vente + en-tête du
      magasin), pour réimprimer n'importe quelle vente du magasin sur une
      imprimante de caisse. Les ventes annulées sont comprises (mention sur le ticket).
    responses:
      200: {description: "{vente, magasin}"}
      404: {description: Vente introuvable}
    """
    user = _moi()
    vente_oid = caisse._oid(vente_id)
    vente = LivraisonModel.get_collection().find_one({"_id": vente_oid}) if vente_oid else None
    if not vente or str(vente.get("magasin_id")) != str(user["magasin_id"]):
        return jsonify({"error": "Vente introuvable."}), 404
    magasin = MagasinModel.get_magasin_by_id(str(user["magasin_id"])) or {}
    return jsonify({"vente": caisse.serialiser_vente(vente), "magasin": _magasin_ticket(magasin)}), 200


@caisse_bp.route("/ventes/<vente_id>/paiement", methods=["POST"])
@jwt_required()
@role_required("caissier")
def encaisser(vente_id):
    """
    Encaisse un paiement sur une vente à crédit du caissier.
    ---
    tags: [Caisse]
    responses:
      200: {description: Vente mise à jour}
    """
    data = request.get_json(silent=True) or {}
    return jsonify(caisse.ajouter_paiement(_moi(), vente_id, data.get("montant"), data.get("methode"))), 200


@caisse_bp.route("/ventes/<vente_id>/annuler", methods=["POST"])
@jwt_required()
@role_required("caissier")
@permission_requise("ventes.annuler")
def annuler(vente_id):
    """
    Annule une vente du caissier dans les 15 minutes qui suivent (motif obligatoire).
    ---
    tags: [Caisse]
    responses:
      200: {description: Vente annulée, articles remis en stock}
      403: {description: Délai dépassé}
    """
    return jsonify(caisse.annuler_vente(_moi(), vente_id, request.get_json(silent=True))), 200


@caisse_bp.route("/statistiques", methods=["GET"])
@jwt_required()
@role_required("caissier")
def statistiques():
    """
    Statistiques personnelles du caissier (aucune marge ni prix d'achat).
    ---
    tags: [Caisse]
    parameters:
      - {in: query, name: periode, type: string, enum: [jour, semaine, mois, personnalisee]}
    responses:
      200: {description: Statistiques}
    """
    a = request.args
    return jsonify(caisse.statistiques(_moi(), a.get("periode", "jour"), a.get("debut"), a.get("fin"))), 200


# --------------------------------------------------------------------------- session du caissier
@caisse_bp.route("/session", methods=["GET"])
@jwt_required()
@role_required("caissier")
def ma_session():
    """
    Session de caisse ouverte du caissier (chiffres en direct), ou null.
    ---
    tags: [Caisse]
    responses:
      200: {description: Session}
    """
    session = CaisseSessionModel.session_ouverte(_moi()["_id"])
    return jsonify({"session": caisse.serialiser_session(session) if session else None}), 200


@caisse_bp.route("/session/ouvrir", methods=["POST"])
@jwt_required()
@role_required("caissier")
def ouvrir():
    """
    Ouvre la caisse avec un fond de caisse.
    ---
    tags: [Caisse]
    responses:
      201: {description: Caisse ouverte}
      409: {description: Caisse déjà ouverte}
    """
    data = request.get_json(silent=True) or {}
    session = caisse.ouvrir_session(_moi(), data.get("fond_initial", 0), data.get("note"))
    return jsonify({"session": caisse.serialiser_session(session)}), 201


@caisse_bp.route("/session/fermer", methods=["POST"])
@jwt_required()
@role_required("caissier")
def fermer():
    """
    Ferme la caisse : le caissier déclare les espèces comptées ; l'écart est enregistré.
    ---
    tags: [Caisse]
    responses:
      200: {description: Caisse fermée}
    """
    user = _moi()
    session = caisse.session_requise(user)
    data = request.get_json(silent=True) or {}
    ferme = caisse.fermer_session(session, user, data.get("montant_compte"), data.get("note"))
    return jsonify({"session": caisse.serialiser_session(ferme)}), 200


# --------------------------------------------------------------------------- historique des sessions
@caisse_bp.route("/sessions", methods=["GET"])
@jwt_required()
@role_required("caissier", "admin", "manager")
def sessions():
    """
    Sessions de caisse : les siennes pour un caissier, toutes celles du magasin pour l'admin/manager.
    ---
    tags: [Caisse]
    parameters:
      - {in: query, name: caissier_id, type: string}
      - {in: query, name: statut, type: string, enum: [ouverte, fermee]}
      - {in: query, name: debut, type: string}
      - {in: query, name: fin, type: string}
    responses:
      200: {description: Sessions}
    """
    from services.rapport_service import periode

    user = _moi()
    a = request.args
    caissier_id = str(user["_id"]) if user["role"] == "caissier" else a.get("caissier_id") or None
    debut = fin = None
    if a.get("debut") or a.get("fin"):
        debut, fin = periode(a.get("debut"), a.get("fin"))
        debut, fin = caisse._vers_utc(debut), caisse._vers_utc(fin)
    liste = CaisseSessionModel.lister(user["magasin_id"], caissier_id, a.get("statut"), debut, fin)
    noms = {}
    donnees = [caisse.serialiser_session(s, avec_resume=True, noms=noms) for s in liste]
    fermees = [s for s in donnees if s["statut"] == "fermee"]
    return jsonify({
        "sessions": donnees,
        "totaux": {
            "sessions": len(donnees), "ouvertes": len(donnees) - len(fermees),
            "chiffre_affaires": caisse._arrondi(sum(caisse._num(s["resume"]["chiffre_affaires"]) for s in donnees)),
            "ecart_total": caisse._arrondi(sum(caisse._num(s["ecart"]) for s in fermees)),
            "sessions_avec_ecart": sum(1 for s in fermees if abs(caisse._num(s["ecart"])) >= 1),
        },
    }), 200


@caisse_bp.route("/sessions/<session_id>", methods=["GET"])
@jwt_required()
@role_required("caissier", "admin", "manager")
def session_detail(session_id):
    """
    Détail d'une session (opérations comprises).
    ---
    tags: [Caisse]
    responses:
      200: {description: Session}
      404: {description: Session introuvable}
    """
    user = _moi()
    session = CaisseSessionModel.get(session_id, user["magasin_id"])
    if not session or (user["role"] == "caissier" and str(session["caissier_id"]) != str(user["_id"])):
        raise ErreurCaisse("Session introuvable.", 404)
    return jsonify({"session": caisse.serialiser_session(session)}), 200


@caisse_bp.route("/sessions/<session_id>/fermer", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
def fermer_pour_caissier(session_id):
    """
    Ferme la caisse d'un caissier (oubli, absence) : montant compté et motif obligatoires.
    ---
    tags: [Caisse]
    responses:
      200: {description: Caisse fermée}
    """
    user = _moi()
    session = CaisseSessionModel.get(session_id, user["magasin_id"])
    if not session:
        raise ErreurCaisse("Session introuvable.", 404)
    data = request.get_json(silent=True) or {}
    ferme = caisse.fermer_session(session, user, data.get("montant_compte"), data.get("note"))
    return jsonify({"session": caisse.serialiser_session(ferme)}), 200


# --------------------------------------------------------------------------- réglages et équipe
@caisse_bp.route("/parametres", methods=["GET"])
@jwt_required()
@role_required("admin", "manager", "caissier")
def lire_parametres():
    """
    Paramètres de caisse du magasin.
    ---
    tags: [Caisse]
    responses:
      200: {description: Paramètres}
    """
    return jsonify(caisse.parametres_caisse(MagasinModel.get_magasin_by_id(str(_moi()["magasin_id"])))), 200


@caisse_bp.route("/parametres", methods=["PUT"])
@jwt_required()
# Les remises du caissier sont un droit d'accès : seul l'admin les règle
# (ici ou dans Paramètres > Accès).
@role_required("admin")
def modifier_parametres():
    """
    Modifie les paramètres de caisse (ex. autoriser les remises aux caissiers).
    ---
    tags: [Caisse]
    responses:
      200: {description: Paramètres enregistrés}
    """
    return jsonify(caisse.maj_parametres(_moi()["magasin_id"], request.get_json(silent=True))), 200


@caisse_bp.route("/caissiers", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def caissiers():
    """
    Caissiers du magasin : caisse ouverte ou non, chiffres du jour, limite de la licence.
    ---
    tags: [Caisse]
    responses:
      200: {description: Caissiers}
    """
    from services.user_service import max_caissiers

    user = _moi()
    magasin = MagasinModel.get_magasin_by_id(str(user["magasin_id"])) or {}
    liste = caisse.caissiers_du_magasin(user["magasin_id"])
    return jsonify({"caissiers": liste, "limite": max_caissiers(magasin), "utilises": len(liste)}), 200


# --------------------------------------------------------------------------- écrans de vente partagés avec l'admin
# Le caissier utilise les MÊMES écrans que l'admin et le manager (liste des
# ventes, choix des produits, vente finale). Ces routes renvoient exactement
# les mêmes réponses que leurs équivalents admin, limitées à SES ventes et
# sans prix d'achat ni coût de revient.
_CHAMPS_INTERDITS = {"prix_achat", "cout_achat_total", "lots_consommes", "methode_valorisation", "lots",
                     "mouvements_stock"}


def _sans_couts(valeur):
    if isinstance(valeur, dict):
        return {k: _sans_couts(v) for k, v in valeur.items() if k not in _CHAMPS_INTERDITS}
    if isinstance(valeur, list):
        return [_sans_couts(v) for v in valeur]
    return valeur


@caisse_bp.route("/stock_par_categorie", methods=["GET"])
@jwt_required()
@role_required("caissier")
def stock_par_categorie():
    """
    Stock du magasin par catégorie (même format que /managers/stock_par_categorie, sans prix d'achat).
    ---
    tags: [Caisse]
    responses:
      200: {description: Catégories et produits}
    """
    from services.produit_service import get_stock_par_categorie_total

    return jsonify(_sans_couts(get_stock_par_categorie_total({"magasin_id": str(_moi()["magasin_id"])}))), 200


@caisse_bp.route("/code-barre/<code>", methods=["GET"])
@jwt_required()
@role_required("caissier")
def produit_par_code_barre(code):
    """
    Produit du magasin par code-barres (même format que /produits/code-barre, sans prix d'achat).
    ---
    tags: [Caisse]
    responses:
      200: {description: Produit}
      404: {description: Produit non trouvé}
    """
    from services.produit_service import get_produit_by_barcode_service

    resultat, statut = get_produit_by_barcode_service(code, str(_moi()["magasin_id"]))
    return jsonify(_sans_couts(resultat)), statut


@caisse_bp.route("/livraisons", methods=["GET"])
@jwt_required()
@role_required("caissier")
def mes_livraisons():
    """
    Ventes du caissier (même format que /managers/livraisons).
    ---
    tags: [Caisse]
    responses:
      200: {description: Ventes}
    """
    from services.livraison_service import get_livraison_managers

    user = _moi()
    resultat, statut = get_livraison_managers(str(user["magasin_id"]), vendeur_id=str(user["_id"]))
    if statut == 200:
        # Fin du délai d'annulation par le caissier (null une fois dépassé).
        maintenant = datetime.utcnow()
        for vente in resultat:
            limite = caisse.limite_annulation(vente)
            vente["annulable_jusqua"] = caisse._iso_utc(limite) if limite and limite > maintenant else None
    return jsonify(_sans_couts(resultat)), statut


@caisse_bp.route("/livraisons/<vente_id>", methods=["GET"])
@jwt_required()
@role_required("caissier")
def ma_livraison(vente_id):
    """
    Détail d'une vente du caissier (même format que GET /livraisons/<id>).
    ---
    tags: [Caisse]
    responses:
      200: {description: Vente}
    """
    from services.livraison_service import details_livraison_service

    user = _moi()
    vente = caisse._vente_du_caissier(user, vente_id)
    resultat, statut = details_livraison_service(str(user["_id"]), str(vente["_id"]))
    return jsonify(_sans_couts(resultat)), statut


@caisse_bp.route("/livraisons/ajouter-paiement/<vente_id>", methods=["POST"])
@jwt_required()
@role_required("caissier")
def payer_ma_livraison(vente_id):
    """
    Paiement sur une vente à crédit du caissier (même format que /livraisons/ajouter-paiement).
    ---
    tags: [Caisse]
    responses:
      200: {description: Paiement enregistré}
    """
    data = request.get_json(silent=True) or {}
    vente = caisse.ajouter_paiement(_moi(), vente_id, data.get("montant"), data.get("methode") or "cash")
    return jsonify({"message": "Paiement mis à jour avec succès", "livraison": vente}), 200


@caisse_bp.route("/livraisons/<vente_id>", methods=["DELETE"])
@jwt_required()
@role_required("caissier")
@permission_requise("ventes.annuler")
def annuler_ma_livraison(vente_id):
    """
    Annulation d'une vente par le caissier dans les 15 minutes (même corps que DELETE /livraisons/<id>).
    ---
    tags: [Caisse]
    responses:
      200: {description: Vente annulée}
      403: {description: Délai dépassé}
    """
    return jsonify(caisse.annuler_vente(_moi(), vente_id, request.get_json(silent=True))), 200
