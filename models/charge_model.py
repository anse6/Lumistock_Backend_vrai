# models/charge_model.py

from bson import ObjectId
from datetime import datetime
from extensions import db

CHAMPS_MODIFIABLES = (
    "nom", "montant", "montant_tva", "categorie", "date_charge", "description", "fournisseur",
    "reference", "statut", "date_paiement", "date_echeance", "mode_paiement", "recurrence",
)


def _sans_fuseau(valeur):
    if isinstance(valeur, str) and valeur:
        valeur = datetime.fromisoformat(valeur.replace("Z", "+00:00"))
    if isinstance(valeur, datetime):
        # Date avec fuseau (ex. « …Z » envoyé par le navigateur) : ramenée à
        # l'heure locale du magasin, sinon une date choisie le 16 tomberait le 15.
        return valeur.astimezone().replace(tzinfo=None) if valeur.tzinfo else valeur
    return valeur


class ChargeModel:
    @staticmethod
    def get_collection():
        return db.tables.charges

    @staticmethod
    def create_charge(data):
        # Dates métier à l'heure locale (comme les périodes et le verrouillage).
        date_charge = _sans_fuseau(data.get("date_charge")) or datetime.now()
        statut = data.get("statut") or "payee"
        charge = {
            "nom": data["nom"],
            "montant": float(data["montant"]),
            "montant_tva": float(data.get("montant_tva") or 0),
            "categorie": data.get("categorie", "autre"),
            "magasin_id": ObjectId(data["magasin_id"]),
            "date_charge": date_charge,
            "description": data.get("description") or "",
            "fournisseur": data.get("fournisseur") or "",
            "reference": data.get("reference") or "",
            "statut": statut,
            # Une charge payée sans date de paiement est réglée le jour de la charge.
            "date_paiement": (_sans_fuseau(data.get("date_paiement")) or date_charge) if statut == "payee" else None,
            "date_echeance": _sans_fuseau(data.get("date_echeance")),
            "mode_paiement": (data.get("mode_paiement") or "cash") if statut == "payee" else data.get("mode_paiement"),
            "recurrence": data.get("recurrence") or "aucune",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        if data.get("recurrence_source"):
            charge["recurrence_source"] = data["recurrence_source"]
        result = ChargeModel.get_collection().insert_one(charge)
        return str(result.inserted_id)

    @staticmethod
    def get_for_magasin(charge_id, magasin_id):
        """La charge seulement si elle appartient au magasin indiqué."""
        if not ObjectId.is_valid(str(charge_id)):
            return None
        return ChargeModel.get_collection().find_one({"_id": ObjectId(str(charge_id)), "magasin_id": ObjectId(str(magasin_id))})

    @staticmethod
    def get_all_by_magasin(magasin_id):
        return list(ChargeModel.get_collection().find({"magasin_id": ObjectId(magasin_id)}).sort("date_charge", -1))

    @staticmethod
    def update_charge(charge_id, magasin_id, data):
        actuelle = ChargeModel.get_for_magasin(charge_id, magasin_id)
        if not actuelle:
            return None
        update_data = {k: data[k] for k in data if k in CHAMPS_MODIFIABLES}
        for champ in ("montant", "montant_tva"):
            if champ in update_data:
                update_data[champ] = float(update_data[champ] or 0)
        for champ in ("date_charge", "date_paiement", "date_echeance"):
            if champ in update_data:
                update_data[champ] = _sans_fuseau(update_data[champ])
        montant = update_data.get("montant", actuelle.get("montant", 0))
        if update_data.get("montant_tva", actuelle.get("montant_tva") or 0) > montant:
            raise ValueError("La TVA ne peut pas dépasser le montant de la charge.")
        statut = update_data.get("statut", actuelle.get("statut") or "payee")
        if statut == "payee" and not (update_data.get("date_paiement") or actuelle.get("date_paiement")):
            update_data["date_paiement"] = update_data.get("date_charge") or actuelle.get("date_charge")
        if statut == "a_payer":
            update_data["date_paiement"] = None

        update_data["updated_at"] = datetime.utcnow()
        return ChargeModel.get_collection().find_one_and_update(
            {"_id": actuelle["_id"]},
            {"$set": update_data},
            return_document=True
        )

    @staticmethod
    def payer(charge_id, magasin_id, date_paiement=None, mode_paiement="cash"):
        actuelle = ChargeModel.get_for_magasin(charge_id, magasin_id)
        if not actuelle:
            return None
        return ChargeModel.get_collection().find_one_and_update(
            {"_id": actuelle["_id"]},
            {"$set": {
                "statut": "payee",
                "date_paiement": _sans_fuseau(date_paiement) or datetime.now(),
                "mode_paiement": mode_paiement or "cash",
                "updated_at": datetime.utcnow(),
            }},
            return_document=True
        )

    @staticmethod
    def delete_charge(charge_id, magasin_id):
        actuelle = ChargeModel.get_for_magasin(charge_id, magasin_id)
        if not actuelle:
            return None
        return ChargeModel.get_collection().find_one_and_delete({"_id": actuelle["_id"]})

    @staticmethod
    def get_total_charges_by_magasin(magasin_id, start_date=None, end_date=None):
        query = {"magasin_id": ObjectId(magasin_id)}
        if start_date and end_date:
            query["date_charge"] = {"$gte": start_date, "$lte": end_date}
        return float(sum(float(c.get("montant", 0) or 0) for c in ChargeModel.get_collection().find(query)))
