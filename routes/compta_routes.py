# routes/compta_routes.py
"""Comptabilité du magasin connecté : synthèse financière, journal et réglages."""

from flask import Blueprint, request, jsonify
from flask_jwt_extended import get_jwt_identity, jwt_required

from services import cloture_service, compta_service, financement_service, fiscal_service, rapprochement_service
from services.cloture_service import PeriodeVerrouillee
from services.rapport_service import periode
from utils.decorators import role_required
from utils.decorators import permission_requise
from utils.inject_magasin_id import inject_magasin_id

compta_bp = Blueprint("compta_bp", __name__)


def _magasin():
    return inject_magasin_id({})["magasin_id"]


def _executer(fonction):
    try:
        return jsonify(fonction()), 200
    except PeriodeVerrouillee as erreur:
        return jsonify({"error": str(erreur)}), 409
    except LookupError as erreur:
        return jsonify({"error": str(erreur)}), 404
    except ValueError as erreur:
        return jsonify({"error": str(erreur)}), 400
    except Exception as erreur:  # magasin introuvable…
        return jsonify({"error": str(erreur)}), 400


@compta_bp.route("/synthese", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def get_synthese():
    """
    Synthèse financière de la période
    ---
    tags: [Comptabilité]
    summary: Compte de résultat comparé, trésorerie, TVA, bilan simplifié et évolution mensuelle
    parameters:
      - {in: query, name: debut, type: string, format: date}
      - {in: query, name: fin, type: string, format: date}
    responses:
      200: {description: Synthèse de la période (par défaut le mois en cours)}
    """
    def calcul():
        debut, fin = periode(request.args.get("debut"), request.args.get("fin"))
        return compta_service.synthese(_magasin(), debut, fin)
    return _executer(calcul)


@compta_bp.route("/journal", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def get_journal():
    """
    Journal comptable (partie double, plan SYSCOHADA) et balance de la période
    ---
    tags: [Comptabilité]
    parameters:
      - {in: query, name: debut, type: string, format: date}
      - {in: query, name: fin, type: string, format: date}
    responses:
      200: {description: Écritures et balance des comptes}
    """
    def calcul():
        debut, fin = periode(request.args.get("debut"), request.args.get("fin"))
        return compta_service.journal(_magasin(), debut, fin)
    return _executer(calcul)


@compta_bp.route("/parametres", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def get_parametres():
    """
    Solde de trésorerie d'ouverture
    ---
    tags: [Comptabilité]
    """
    def lire():
        p = compta_service.parametres(_magasin())
        return {**p, "date_ouverture": p["date_ouverture"].isoformat() if p["date_ouverture"] else None}
    return _executer(lire)


@compta_bp.route("/parametres", methods=["PUT"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("finances.parametres")
def put_parametres():
    """
    Enregistrer le solde de trésorerie d'ouverture
    ---
    tags: [Comptabilité]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            solde_ouverture: {type: number, example: 250000}
            date_ouverture: {type: string, format: date, example: "2026-01-01"}
    """
    def enregistrer():
        data = request.get_json(silent=True) or {}
        try:
            solde = float(data.get("solde_ouverture") or 0)
        except (TypeError, ValueError):
            raise ValueError("Solde d'ouverture invalide")
        soldes = data.get("soldes_ouverture")
        if soldes is not None:
            if not isinstance(soldes, dict):
                raise ValueError("Soldes d'ouverture invalides")
            try:
                soldes = {c: float(soldes.get(c) or 0) for c in compta_service.COMPTES_OUVERTURE}
            except (TypeError, ValueError):
                raise ValueError("Soldes d'ouverture invalides")
        p = compta_service.enregistrer_parametres(_magasin(), solde, data.get("date_ouverture"), soldes)
        return {**p, "date_ouverture": p["date_ouverture"].isoformat() if p["date_ouverture"] else None}
    return _executer(enregistrer)


@compta_bp.route("/categories", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def get_categories():
    """
    Catégories de charges et leur compte SYSCOHADA
    ---
    tags: [Comptabilité]
    """
    return jsonify([
        {"value": cle, "label": libelle, "compte": compte, "rubrique": rubrique,
         "tva": cle in compta_service.CATEGORIES_AVEC_TVA}
        for cle, (libelle, compte, rubrique) in compta_service.CATEGORIES_CHARGES.items()
    ]), 200


# --------------------------------------------------------------------------- impôts et taxes
@compta_bp.route("/fiscalite", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def get_fiscalite():
    """
    Réglages fiscaux : assujettissement à la TVA, régime, taux de l'impôt sur le résultat et du minimum fiscal
    ---
    tags: [Comptabilité]
    """
    return _executer(lambda: fiscal_service.parametres_fiscaux(_magasin()))


@compta_bp.route("/fiscalite", methods=["PUT"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("finances.parametres")
def put_fiscalite():
    """
    Modifier les réglages fiscaux du magasin
    ---
    tags: [Comptabilité]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            assujetti_tva: {type: boolean}
            regime: {type: string, enum: [reel, simplifie, forfait]}
            taux_is: {type: number, example: 33}
            taux_minimum: {type: number, example: 2.2}
            capital_social: {type: number, example: 1000000}
    """
    return _executer(lambda: fiscal_service.enregistrer_parametres_fiscaux(_magasin(), request.get_json(silent=True) or {}))


@compta_bp.route("/impots", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def get_impots():
    """
    Déclarations de TVA mois par mois, acomptes et impôt sur le résultat de l'année
    ---
    tags: [Comptabilité]
    parameters:
      - {in: query, name: annee, type: integer, example: 2026}
    """
    def calcul():
        from datetime import datetime
        try:
            annee = int(request.args.get("annee") or datetime.now().year)
        except ValueError:
            raise ValueError("Année invalide.")
        if not 2000 <= annee <= 2100:
            raise ValueError("Année invalide.")
        return fiscal_service.situation(_magasin(), annee)
    return _executer(calcul)


@compta_bp.route("/impots/paiements", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("finances.parametres")
def post_paiement_impot():
    """
    Enregistrer un reversement de TVA, un remboursement de crédit de TVA ou un paiement d'impôt sur le résultat
    ---
    tags: [Comptabilité]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [type, periode, montant]
          properties:
            type: {type: string, enum: [tva, remboursement_tva, is]}
            periode: {type: string, example: "2026-09", description: "AAAA-MM pour la TVA, AAAA pour l'impôt sur le résultat"}
            montant: {type: number}
            date: {type: string, format: date}
            mode_paiement: {type: string, example: banque}
            reference: {type: string}
    """
    return _executer(lambda: fiscal_service.creer_operation(_magasin(), request.get_json(silent=True) or {}, get_jwt_identity()))


@compta_bp.route("/impots/paiements/<operation_id>", methods=["DELETE"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("finances.parametres")
def delete_paiement_impot(operation_id):
    """
    Supprimer un paiement d'impôt enregistré par erreur
    ---
    tags: [Comptabilité]
    """
    return _executer(lambda: fiscal_service.supprimer_operation(_magasin(), operation_id))


# --------------------------------------------------------------------------- capital et emprunts
@compta_bp.route("/financements", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def get_financements():
    """
    Apports, retraits de l'exploitant, emprunts et remboursements
    ---
    tags: [Comptabilité]
    """
    return _executer(lambda: financement_service.liste(_magasin()))


@compta_bp.route("/financements", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("finances.parametres")
def post_financement():
    """
    Enregistrer un apport, un retrait, un emprunt reçu ou un remboursement d'emprunt
    ---
    tags: [Comptabilité]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [type, montant]
          properties:
            type: {type: string, enum: [apport_capital, apport, retrait, emprunt, remboursement]}
            montant: {type: number, description: "Capital (hors intérêts pour un remboursement)"}
            interets: {type: number}
            emprunt_id: {type: string}
            date: {type: string, format: date}
            mode_paiement: {type: string}
            tiers: {type: string}
            libelle: {type: string}
    """
    return _executer(lambda: financement_service.creer(_magasin(), request.get_json(silent=True) or {}, get_jwt_identity()))


@compta_bp.route("/financements/<operation_id>", methods=["DELETE"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("finances.parametres")
def delete_financement(operation_id):
    """
    Supprimer une opération de financement
    ---
    tags: [Comptabilité]
    """
    return _executer(lambda: financement_service.supprimer(_magasin(), operation_id))


# --------------------------------------------------------------------------- clôture et verrouillage
@compta_bp.route("/clotures", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def get_clotures():
    """
    Date de verrouillage et exercices clôturés
    ---
    tags: [Comptabilité]
    """
    return _executer(lambda: cloture_service.etat(_magasin()))


@compta_bp.route("/verrouillage", methods=["PUT"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("finances.parametres")
def put_verrouillage():
    """
    Verrouiller les opérations jusqu'à une date (incluse), ou déverrouiller (date vide)
    ---
    tags: [Comptabilité]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            date: {type: string, format: date, example: "2026-09-30"}
    """
    def verrouiller():
        cloture_service.verrouiller(_magasin(), (request.get_json(silent=True) or {}).get("date"))
        return cloture_service.etat(_magasin())
    return _executer(verrouiller)


@compta_bp.route("/clotures", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("finances.parametres")
def post_cloture():
    """
    Clôturer un exercice (année civile terminée) : résultat et bilan figés, période verrouillée
    ---
    tags: [Comptabilité]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [exercice]
          properties:
            exercice: {type: integer, example: 2025}
    """
    def cloturer():
        exercice = (request.get_json(silent=True) or {}).get("exercice")
        try:
            exercice = int(exercice)
        except (TypeError, ValueError):
            raise ValueError("Exercice invalide (année attendue).")
        return cloture_service.cloturer(_magasin(), exercice, get_jwt_identity())
    return _executer(cloturer)


@compta_bp.route("/clotures/<int:exercice>", methods=["DELETE"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("finances.parametres")
def delete_cloture(exercice):
    """
    Rouvrir le dernier exercice clôturé
    ---
    tags: [Comptabilité]
    """
    return _executer(lambda: cloture_service.rouvrir(_magasin(), exercice))


# --------------------------------------------------------------------------- rapprochement bancaire
def _compte():
    return (request.args.get("compte") or (request.get_json(silent=True) or {}).get("compte")
            or request.form.get("compte") or "521")


@compta_bp.route("/rapprochement", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def get_rapprochement():
    """
    Relevé importé et opérations de l'application à rapprocher
    ---
    tags: [Rapprochement bancaire]
    parameters:
      - {in: query, name: compte, type: string, enum: ["521", "552"], description: "521 banque, 552 mobile money"}
      - {in: query, name: debut, type: string, format: date}
      - {in: query, name: fin, type: string, format: date}
    """
    def lire():
        debut, fin = periode(request.args.get("debut"), request.args.get("fin"))
        return rapprochement_service.etat(_magasin(), _compte(), debut, fin)
    return _executer(lire)


@compta_bp.route("/rapprochement/releve", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
def post_releve():
    """
    Importer un relevé (fichier CSV) ou ajouter des lignes saisies à la main
    ---
    tags: [Rapprochement bancaire]
    consumes: [multipart/form-data, application/json]
    parameters:
      - {in: formData, name: fichier, type: file, description: "CSV : Date ; Libellé ; Montant (ou Débit / Crédit) ; Référence"}
      - {in: formData, name: compte, type: string}
    """
    def importer():
        fichier = request.files.get("fichier")
        erreurs = []
        if fichier:
            operations, erreurs = rapprochement_service.lire_csv(fichier.read())
        else:
            operations = []
            for l in (request.get_json(silent=True) or {}).get("lignes") or []:
                from services.rapport_service import _date as lire_date
                quand, montant = lire_date(l.get("date")), l.get("montant")
                if not quand or not montant:
                    raise ValueError("Chaque ligne demande une date et un montant non nul.")
                operations.append({"date": quand, "libelle": (l.get("libelle") or "Opération").strip(),
                                   "montant": round(float(montant), 2), "reference": l.get("reference") or ""})
        if not operations:
            raise ValueError("Aucune opération lisible dans le relevé.")
        return {**rapprochement_service.importer(_magasin(), _compte(), operations), "erreurs": erreurs}
    return _executer(importer)


@compta_bp.route("/rapprochement/auto", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
def post_rapprochement_auto():
    """
    Rapprocher automatiquement (même montant, à 5 jours près)
    ---
    tags: [Rapprochement bancaire]
    """
    def auto():
        data = request.get_json(silent=True) or {}
        debut, fin = periode(data.get("debut"), data.get("fin"))
        return {"rapprochees": rapprochement_service.automatique(_magasin(), _compte(), debut, fin)}
    return _executer(auto)


@compta_bp.route("/rapprochement/lignes/<ligne_id>", methods=["PUT"])
@jwt_required()
@role_required("admin", "manager")
def put_rapprochement(ligne_id):
    """
    Rapprocher une ligne du relevé d'une ou plusieurs opérations (refs), ou l'annuler (refs vide)
    ---
    tags: [Rapprochement bancaire]
    """
    def rapprocher():
        refs = (request.get_json(silent=True) or {}).get("refs") or []
        fait = (rapprochement_service.rapprocher(_magasin(), ligne_id, refs) if refs
                else rapprochement_service.annuler(_magasin(), ligne_id))
        if not fait:
            raise LookupError("Ligne du relevé introuvable.")
        return {"message": "Rapprochement enregistré" if refs else "Rapprochement annulé"}
    return _executer(rapprocher)


@compta_bp.route("/rapprochement/lignes/<ligne_id>", methods=["DELETE"])
@jwt_required()
@role_required("admin", "manager")
def delete_ligne_releve(ligne_id):
    """
    Supprimer une ligne du relevé importée par erreur
    ---
    tags: [Rapprochement bancaire]
    """
    def supprimer():
        if not rapprochement_service.supprimer_ligne(_magasin(), ligne_id):
            raise LookupError("Ligne du relevé introuvable.")
        return {"message": "Ligne supprimée"}
    return _executer(supprimer)


@compta_bp.route("/rapprochement/lignes/<ligne_id>/charge", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
def post_charge_depuis_releve(ligne_id):
    """
    Créer la charge manquante (frais bancaires, agios…) à partir d'une sortie du relevé
    ---
    tags: [Rapprochement bancaire]
    """
    def creer():
        data = request.get_json(silent=True) or {}
        charge_id = rapprochement_service.creer_charge_depuis_ligne(
            _magasin(), ligne_id, data.get("categorie") or "frais_bancaires", data.get("nom"))
        if not charge_id:
            raise LookupError("Ligne du relevé introuvable.")
        return {"message": "Charge créée et rapprochée", "charge_id": charge_id}
    return _executer(creer)


# --------------------------------------------------------------------------- politique de retour
@compta_bp.route("/politique-retour", methods=["GET"])
@jwt_required()
@role_required("admin", "manager", "livreur", "gerant")
def get_politique_retour():
    """
    Frais de retour appliqués par défaut (pourcentage de la valeur retournée gardé par le magasin)
    ---
    tags: [Comptabilité]
    """
    return _executer(lambda: {"retenue_pourcentage": cloture_service.politique_retour(_magasin())})


@compta_bp.route("/politique-retour", methods=["PUT"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("finances.parametres")
def put_politique_retour():
    """
    Fixer les frais de retour par défaut (0 % : remboursement intégral)
    ---
    tags: [Comptabilité]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            retenue_pourcentage: {type: number, example: 1}
    """
    return _executer(lambda: cloture_service.enregistrer_politique_retour(
        _magasin(), (request.get_json(silent=True) or {}).get("retenue_pourcentage")))
