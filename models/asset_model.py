# models/asset_model.py

from bson import ObjectId
from datetime import datetime
from extensions import db

CHAMPS_MODIFIABLES = ("nom", "valeur_achat", "valeur_residuelle", "date_achat", "duree_vie_annees",
                      "categorie", "description", "mode_paiement", "montant_tva", "residuelle_auto", "fournisseur",
                      "reference")

# Valeur résiduelle (valeur de revente estimée en fin d'utilisation), en % de
# la valeur d'achat HT, selon la nature du bien. Elle n'est pas amortie.
TAUX_RESIDUEL = {"informatique": 5.0, "mobilier": 10.0, "vehicule": 20.0, "equipement": 10.0, "autre": 5.0}


def taux_residuel(categorie):
    return TAUX_RESIDUEL.get(categorie, TAUX_RESIDUEL["autre"])


def residuelle_auto(valeur_achat, categorie):
    """Valeur résiduelle calculée, arrondie à l'unité."""
    return float(round(float(valeur_achat or 0) * taux_residuel(categorie) / 100))


def _sans_fuseau(valeur):
    if isinstance(valeur, str) and valeur:
        valeur = datetime.fromisoformat(valeur.replace("Z", "+00:00"))
    if isinstance(valeur, datetime):
        # Date avec fuseau (ex. « …Z » envoyé par le navigateur) : ramenée à
        # l'heure locale du magasin, sinon une date choisie le 16 tomberait le 15.
        return valeur.astimezone().replace(tzinfo=None) if valeur.tzinfo else valeur
    return valeur


def _ajouter_annees(date, annees):
    try:
        return date.replace(year=date.year + annees)
    except ValueError:  # 29 février
        return date.replace(year=date.year + annees, day=28)


class AssetModel:
    @staticmethod
    def get_collection():
        return db.tables.assets

    @staticmethod
    def create_asset(data):
        categorie = data.get("categorie", "materiel")
        auto = data.get("residuelle_auto", data.get("valeur_residuelle") is None) is not False
        valeur_achat = float(data["valeur_achat"])
        tva = float(data.get("montant_tva") or 0)
        if tva < 0 or tva > valeur_achat:
            raise ValueError("La TVA récupérable doit être comprise entre 0 et la valeur d'achat HT.")
        asset = {
            "nom": data["nom"],
            "valeur_achat": valeur_achat,
            "valeur_residuelle": residuelle_auto(valeur_achat, categorie) if auto else float(data.get("valeur_residuelle") or 0),
            "residuelle_auto": auto,
            "montant_tva": tva,
            "fournisseur": data.get("fournisseur") or "",
            "reference": data.get("reference") or "",
            "date_achat": _sans_fuseau(data.get("date_achat")) or datetime.now(),
            "duree_vie_annees": int(data["duree_vie_annees"]), # Durée d'amortissement
            "magasin_id": ObjectId(data["magasin_id"]),
            "categorie": categorie,  # informatique, mobilier, vehicule, equipement, autre
            "description": data.get("description") or "",
            "mode_paiement": data.get("mode_paiement") or "cash",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        if asset["valeur_residuelle"] > valeur_achat:
            raise ValueError("La valeur résiduelle ne peut pas dépasser la valeur d'achat.")
        result = AssetModel.get_collection().insert_one(asset)
        return str(result.inserted_id)

    @staticmethod
    def get_all_by_magasin(magasin_id):
        return list(AssetModel.get_collection().find({"magasin_id": ObjectId(magasin_id)}))

    @staticmethod
    def get_for_magasin(asset_id, magasin_id):
        if not ObjectId.is_valid(str(asset_id)):
            return None
        return AssetModel.get_collection().find_one({"_id": ObjectId(str(asset_id)), "magasin_id": ObjectId(str(magasin_id))})

    @staticmethod
    def delete_asset(asset_id, magasin_id):
        actuel = AssetModel.get_for_magasin(asset_id, magasin_id)
        if not actuel:
            return None
        return AssetModel.get_collection().find_one_and_delete({"_id": actuel["_id"]})

    @staticmethod
    def update_asset(asset_id, magasin_id, data):
        actuel = AssetModel.get_for_magasin(asset_id, magasin_id)
        if not actuel:
            return None
        update_data = {k: data[k] for k in data if k in CHAMPS_MODIFIABLES}
        if "valeur_residuelle" in update_data and "residuelle_auto" not in update_data:
            update_data["residuelle_auto"] = False  # valeur saisie à la main
        auto = update_data.get("residuelle_auto", actuel.get("residuelle_auto", False))
        if auto:
            update_data["residuelle_auto"] = True
            update_data["valeur_residuelle"] = residuelle_auto(update_data.get("valeur_achat", actuel.get("valeur_achat")),
                                                               update_data.get("categorie", actuel.get("categorie")))
        for champ in ("valeur_achat", "valeur_residuelle", "montant_tva"):
            if champ in update_data:
                update_data[champ] = float(update_data[champ] or 0)
        if "duree_vie_annees" in update_data:
            update_data["duree_vie_annees"] = int(update_data["duree_vie_annees"])
        if "date_achat" in update_data:
            update_data["date_achat"] = _sans_fuseau(update_data["date_achat"])
        if update_data.get("valeur_residuelle", actuel.get("valeur_residuelle") or 0) > \
                update_data.get("valeur_achat", actuel.get("valeur_achat") or 0):
            raise ValueError("La valeur résiduelle ne peut pas dépasser la valeur d'achat.")
        if not 0 <= update_data.get("montant_tva", actuel.get("montant_tva") or 0) <= \
                update_data.get("valeur_achat", actuel.get("valeur_achat") or 0):
            raise ValueError("La TVA récupérable doit être comprise entre 0 et la valeur d'achat HT.")

        update_data["updated_at"] = datetime.utcnow()
        return AssetModel.get_collection().find_one_and_update(
            {"_id": actuel["_id"]},
            {"$set": update_data},
            return_document=True
        )

    # ------------------------------------------------------------ cession
    @staticmethod
    def ceder(asset_id, magasin_id, date_cession, prix_cession, mode_paiement=None, acheteur=None):
        """Vente (ou mise au rebut, prix 0) : l'amortissement s'arrête à la date de cession."""
        actuel = AssetModel.get_for_magasin(asset_id, magasin_id)
        if not actuel:
            return None
        date_cession = _sans_fuseau(date_cession) or datetime.now()
        if date_cession < _sans_fuseau(actuel.get("date_achat")):
            raise ValueError("La date de cession est antérieure à la date d'achat.")
        if float(prix_cession or 0) < 0:
            raise ValueError("Le prix de cession ne peut pas être négatif.")
        return AssetModel.get_collection().find_one_and_update({"_id": actuel["_id"]}, {"$set": {
            "date_cession": date_cession, "prix_cession": float(prix_cession or 0),
            "mode_paiement_cession": mode_paiement or "cash", "acheteur": acheteur or "",
            "updated_at": datetime.utcnow(),
        }}, return_document=True)

    @staticmethod
    def annuler_cession(asset_id, magasin_id):
        actuel = AssetModel.get_for_magasin(asset_id, magasin_id)
        if not actuel:
            return None
        return AssetModel.get_collection().find_one_and_update({"_id": actuel["_id"]}, {
            "$unset": {"date_cession": "", "prix_cession": "", "mode_paiement_cession": "", "acheteur": ""},
            "$set": {"updated_at": datetime.utcnow()}}, return_document=True)

    @staticmethod
    def date_cession(asset):
        return _sans_fuseau(asset.get("date_cession")) if asset.get("date_cession") else None

    @staticmethod
    def cede_avant(asset, date):
        cession = AssetModel.date_cession(asset)
        return bool(cession and cession <= date)

    # ------------------------------------------------------------ amortissement
    @staticmethod
    def base_amortissable(asset):
        return max(float(asset.get("valeur_achat") or 0) - float(asset.get("valeur_residuelle") or 0), 0.0)

    @staticmethod
    def amortissement_entre(asset, debut, fin):
        """Dotation linéaire, au prorata des jours, de la période [debut, fin]."""
        date_achat = _sans_fuseau(asset.get("date_achat"))
        duree = int(asset.get("duree_vie_annees") or 0)
        if not date_achat or duree <= 0:
            return 0.0
        fin_amortissement = _ajouter_annees(date_achat, duree)
        cession = AssetModel.date_cession(asset)
        d, f = max(debut, date_achat), min(fin, fin_amortissement, cession or fin)
        if f <= d:
            return 0.0
        jours_total = (fin_amortissement - date_achat).total_seconds() / 86400
        jours = (f - d).total_seconds() / 86400
        return AssetModel.base_amortissable(asset) * jours / jours_total

    @staticmethod
    def amortissements_cumules(asset, date=None):
        date = date or datetime.now()
        date_achat = _sans_fuseau(asset.get("date_achat"))
        return AssetModel.amortissement_entre(asset, date_achat, date) if date_achat else 0.0

    @staticmethod
    def valeur_nette(asset, date=None):
        """Valeur nette comptable (VNC) à la date."""
        date = date or datetime.now()
        date_achat = _sans_fuseau(asset.get("date_achat"))
        if (date_achat and date_achat > date) or AssetModel.cede_avant(asset, date):
            return 0.0
        return float(asset.get("valeur_achat") or 0) - AssetModel.amortissements_cumules(asset, date)

    @staticmethod
    def calculate_monthly_depreciation(asset, target_date=None):
        """Dotation mensuelle linéaire (0 avant l'achat et une fois l'actif amorti)."""
        target_date = target_date or datetime.now()
        date_achat = _sans_fuseau(asset.get("date_achat"))
        duree = int(asset.get("duree_vie_annees") or 0)
        if not date_achat or duree <= 0 or target_date < date_achat or target_date >= _ajouter_annees(date_achat, duree) \
                or AssetModel.cede_avant(asset, target_date):
            return 0.0
        return round(AssetModel.base_amortissable(asset) / (duree * 12), 2)
