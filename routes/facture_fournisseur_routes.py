# routes/facture_fournisseur_routes.py
"""Factures fournisseurs (achats de marchandises à crédit) du magasin connecté."""

from flask import Blueprint, jsonify, request, send_file
from flask_jwt_extended import get_jwt_identity, jwt_required

from services import facture_fournisseur_service as factures
from services.cloture_service import PeriodeVerrouillee
from utils import pieces_jointes
from utils.decorators import role_required
from utils.decorators import permission_requise
from utils.inject_magasin_id import inject_magasin_id

facture_fournisseur_bp = Blueprint("facture_fournisseur_bp", __name__, url_prefix="/factures-fournisseurs")


def _magasin():
    return inject_magasin_id({})["magasin_id"]


def _executer(fonction, statut=200):
    try:
        resultat = fonction()
    except PeriodeVerrouillee as e:
        return jsonify({"error": str(e)}), 409
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if resultat is None:
        return jsonify({"error": "Facture introuvable"}), 404
    return jsonify(resultat), statut


@facture_fournisseur_bp.route("", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def lister():
    """
    Factures fournisseurs du magasin
    ---
    tags: [Factures fournisseurs]
    responses:
      200: {description: Factures, de la plus récente à la plus ancienne}
    """
    return jsonify([factures.serialiser(f) for f in factures.lister(_magasin())]), 200


@facture_fournisseur_bp.route("", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
def creer():
    """
    Enregistrer une facture fournisseur : les articles entrent en stock
    ---
    tags: [Factures fournisseurs]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [lignes]
          properties:
            fournisseur_id: {type: string}
            fournisseur_nom: {type: string, description: Si le fournisseur n'est pas dans la liste}
            numero: {type: string, example: "FA-2026-118"}
            date_facture: {type: string, format: date}
            date_echeance: {type: string, format: date}
            montant_tva: {type: number, example: 0}
            note: {type: string}
            lignes:
              type: array
              items:
                type: object
                properties:
                  produit_id: {type: string}
                  quantite: {type: integer, example: 10}
                  prix_achat: {type: number, example: 2500, description: Prix unitaire HT}
            paiement:
              type: object
              description: Acompte versé à la réception (facultatif)
              properties:
                montant: {type: number}
                mode: {type: string, example: "cash"}
    responses:
      201: {description: Facture créée, stock mis à jour}
      409: {description: Période clôturée}
    """
    data = request.get_json(silent=True) or {}
    return _executer(lambda: factures.serialiser(factures.creer(_magasin(), data, get_jwt_identity())), 201)


@facture_fournisseur_bp.route("/<facture_id>", methods=["PUT"])
@jwt_required()
@role_required("admin", "manager")
def modifier(facture_id):
    """
    Modifier le numéro, l'échéance, la TVA ou la note d'une facture
    ---
    tags: [Factures fournisseurs]
    """
    data = request.get_json(silent=True) or {}
    return _executer(lambda: (lambda f: f and factures.serialiser(f))(factures.modifier(facture_id, _magasin(), data)))


@facture_fournisseur_bp.route("/<facture_id>/paiements", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
def payer(facture_id):
    """
    Enregistrer un règlement au fournisseur
    ---
    tags: [Factures fournisseurs]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            montant: {type: number}
            mode: {type: string, example: "om"}
            type: {type: string, enum: [reglement, remboursement], description: "remboursement : argent rendu par le fournisseur après un avoir"}
            date: {type: string, format: date}
            reference: {type: string}
    """
    data = request.get_json(silent=True) or {}
    return _executer(lambda: (lambda f: f and factures.serialiser(f))(
        factures.payer(facture_id, _magasin(), data, get_jwt_identity())))


@facture_fournisseur_bp.route("/<facture_id>/paiements/<paiement_id>", methods=["DELETE"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("finances.parametres")
def annuler_paiement(facture_id, paiement_id):
    """
    Annuler un règlement saisi par erreur
    ---
    tags: [Factures fournisseurs]
    """
    return _executer(lambda: (lambda f: f and factures.serialiser(f))(
        factures.annuler_paiement(facture_id, _magasin(), paiement_id)))


@facture_fournisseur_bp.route("/<facture_id>/pieces", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
def ajouter_piece(facture_id):
    """
    Joindre le scan de la facture (PDF, PNG, JPG, WEBP ; 10 Mo maximum)
    ---
    tags: [Factures fournisseurs]
    consumes: [multipart/form-data]
    parameters:
      - {in: formData, name: fichier, type: file, required: true}
    """
    magasin_id = _magasin()
    facture = factures.get_for_magasin(facture_id, magasin_id)
    if not facture:
        return jsonify({"error": "Facture introuvable"}), 404
    if len(facture.get("pieces_jointes") or []) >= pieces_jointes.NOMBRE_MAX:
        return jsonify({"error": f"{pieces_jointes.NOMBRE_MAX} pièces au maximum par facture."}), 400
    try:
        piece = pieces_jointes.enregistrer(request.files.get("fichier"), get_jwt_identity())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    factures.collection().update_one({"_id": facture["_id"]}, {"$push": {"pieces_jointes": piece}})
    return jsonify(factures.serialiser(factures.get_for_magasin(facture_id, magasin_id))), 201


@facture_fournisseur_bp.route("/<facture_id>/pieces/<piece_id>", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def lire_piece(facture_id, piece_id):
    """
    Télécharger une pièce jointe de la facture
    ---
    tags: [Factures fournisseurs]
    """
    facture = factures.get_for_magasin(facture_id, _magasin())
    piece = next((p for p in (facture or {}).get("pieces_jointes") or [] if p.get("id") == piece_id), None)
    if not piece:
        return jsonify({"error": "Pièce jointe introuvable"}), 404
    return send_file(pieces_jointes.chemin(piece), mimetype=piece.get("type"), download_name=piece.get("nom"),
                     as_attachment=request.args.get("telecharger") == "1")


@facture_fournisseur_bp.route("/<facture_id>/pieces/<piece_id>", methods=["DELETE"])
@jwt_required()
@role_required("admin", "manager")
def supprimer_piece(facture_id, piece_id):
    """
    Retirer une pièce jointe de la facture
    ---
    tags: [Factures fournisseurs]
    """
    magasin_id = _magasin()
    facture = factures.get_for_magasin(facture_id, magasin_id)
    pieces = (facture or {}).get("pieces_jointes") or []
    piece = next((p for p in pieces if p.get("id") == piece_id), None)
    if not piece:
        return jsonify({"error": "Pièce jointe introuvable"}), 404
    factures.collection().update_one({"_id": facture["_id"]},
                                     {"$set": {"pieces_jointes": [p for p in pieces if p.get("id") != piece_id]}})
    pieces_jointes.supprimer(piece)
    return jsonify(factures.serialiser(factures.get_for_magasin(facture_id, magasin_id))), 200


@facture_fournisseur_bp.route("/<facture_id>/avoirs", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
def creer_avoir(facture_id):
    """
    Avoir fournisseur : articles rendus au fournisseur, ou annulation de la facture (sans « lignes »)
    ---
    tags: [Factures fournisseurs]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            motif: {type: string, example: "Articles défectueux"}
            lignes:
              type: array
              description: Absent pour annuler toute la facture
              items:
                type: object
                properties:
                  produit_id: {type: string}
                  quantite: {type: integer}
    responses:
      200: {description: Avoir enregistré ; stock et dette fournisseur diminués}
    """
    data = request.get_json(silent=True) or {}
    return _executer(lambda: (lambda f: f and factures.serialiser(f))(
        factures.avoir(facture_id, _magasin(), data, get_jwt_identity())))
