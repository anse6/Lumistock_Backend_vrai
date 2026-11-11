# routes/numeros_serie_routes.py
"""Numéros de série : unités disponibles (vente), liste, traçabilité et
rapport des sorties."""
import io
import time
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request, send_file
from flask_jwt_extended import jwt_required
from openpyxl import Workbook

from models.produit_model import ProduitModel
from services import numeros_serie_service as ns
from utils.decorators import role_required
from utils.inject_magasin_id import inject_magasin_id

numeros_serie_bp = Blueprint("numeros_serie", __name__, url_prefix="/numeros-serie")


def _magasin():
    try:
        return inject_magasin_id({})["magasin_id"], None
    except Exception:
        return None, (jsonify({"error": "Aucun magasin n'est associé à cet utilisateur."}), 403)


def _jour_utc(texte, defaut):
    """Début de journée locale (fuseau du magasin) exprimé en UTC."""
    try:
        jour = datetime.strptime(texte, "%Y-%m-%d") if texte else defaut
    except ValueError:
        jour = defaut
    return datetime.utcfromtimestamp(time.mktime(jour.timetuple()))


def _periode():
    aujourd_hui = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    debut = _jour_utc(request.args.get("debut"), aujourd_hui.replace(day=1))
    fin = _jour_utc(request.args.get("fin"), aujourd_hui) + timedelta(days=1)
    return debut, fin


@numeros_serie_bp.route("/disponibles/<produit_id>", methods=["GET"])
@jwt_required()
@role_required("admin", "manager", "caissier")
def disponibles(produit_id):
    """
    Numéros en stock d'un article (choix des unités vendues). Sans coût.
    ---
    tags: [Numéros de série]
    """
    magasin_id, erreur = _magasin()
    if erreur:
        return erreur
    produit = ProduitModel.get_for_magasin(produit_id, magasin_id)
    if not produit:
        return jsonify({"error": "Article non trouvé"}), 404
    return jsonify({"suivi_serie": ns.est_suivi(produit),
                    "numeros": [n["numero_serie"] for n in ns.numeros_de(produit_id, ns.EN_STOCK)]}), 200


@numeros_serie_bp.route("", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def lister():
    """
    Numéros de série du magasin (filtres : produit_id, statut, q).
    ---
    tags: [Numéros de série]
    """
    magasin_id, erreur = _magasin()
    if erreur:
        return erreur
    return jsonify(ns.lister(magasin_id, request.args.get("produit_id"), request.args.get("statut"),
                             request.args.get("q"))), 200


@numeros_serie_bp.route("/sorties", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def sorties():
    """
    Unités sorties (ventes, avaries) sur la période debut..fin (AAAA-MM-JJ).
    ---
    tags: [Numéros de série]
    """
    magasin_id, erreur = _magasin()
    if erreur:
        return erreur
    debut, fin = _periode()
    return jsonify(ns.sorties(magasin_id, debut, fin, request.args.get("produit_id"))), 200


@numeros_serie_bp.route("/sorties/export", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def exporter_sorties():
    """
    Export Excel des unités sorties sur la période.
    ---
    tags: [Numéros de série]
    """
    magasin_id, erreur = _magasin()
    if erreur:
        return erreur
    debut, fin = _periode()
    lignes = ns.sorties(magasin_id, debut, fin, request.args.get("produit_id"))
    wb = Workbook()
    ws = wb.active
    ws.title = "Numéros sortis"
    ws.append(["Date", "Numéro de série", "Article", "Sortie", "Client", "Par", "Prix", "Revenu ensuite", "État actuel"])
    for l in lignes:
        quand = datetime.fromisoformat(l["date"]) + (datetime.now() - datetime.utcnow()) if l["date"] else None
        ws.append([quand.strftime("%d/%m/%Y %H:%M") if quand else "", l["numero_serie"], l["produit"], l["libelle"],
                   l.get("client_nom") or "", l.get("par_nom") or "", l.get("prix"),
                   l.get("revenu_libelle") or "", l.get("statut_actuel")])
    flux = io.BytesIO()
    wb.save(flux)
    flux.seek(0)
    return send_file(flux, as_attachment=True, download_name="numeros_serie_sortis.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@numeros_serie_bp.route("/generer", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def generer():
    """
    Numéros internes (« LS » + 10 chiffres) pour des unités sans numéro du fabricant.
    ---
    tags: [Numéros de série]
    """
    magasin_id, erreur = _magasin()
    if erreur:
        return erreur
    try:
        nombre = int(request.args.get("nombre") or 1)
    except ValueError:
        nombre = 1
    return jsonify({"numeros": ns.generer(magasin_id, nombre)}), 200
