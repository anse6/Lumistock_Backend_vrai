"""Sessions de caisse : ouverture (fond de caisse) et fermeture (comptage).

Un caissier ouvre sa caisse avec un fond (la monnaie de départ), vend, puis
la ferme en déclarant les espèces comptées. L'écart entre ce qui est compté
et ce qui est attendu est enregistré : le manager voit les écarts de chaque
caissier. Les montants attendus sont calculés par services.caisse_service.
"""
from datetime import datetime

from bson import ObjectId

from extensions import db

OUVERTE = "ouverte"
FERMEE = "fermee"


class CaisseSessionModel:

    @staticmethod
    def get_collection():
        return db.tables.sessions_caisse

    @staticmethod
    def ouvrir(magasin_id, caissier_id, fond_initial, note=None):
        session = {
            "magasin_id": ObjectId(magasin_id),
            "caissier_id": ObjectId(caissier_id),
            "statut": OUVERTE,
            "fond_initial": round(float(fond_initial), 2),
            "note_ouverture": (note or "").strip()[:300] or None,
            "ouverte_le": datetime.utcnow(),
            "fermee_le": None,
        }
        session["_id"] = CaisseSessionModel.get_collection().insert_one(session).inserted_id
        return session

    @staticmethod
    def session_ouverte(caissier_id):
        if not ObjectId.is_valid(str(caissier_id)):
            return None
        return CaisseSessionModel.get_collection().find_one(
            {"caissier_id": ObjectId(str(caissier_id)), "statut": OUVERTE})

    @staticmethod
    def get(session_id, magasin_id=None):
        if not ObjectId.is_valid(str(session_id)):
            return None
        query = {"_id": ObjectId(str(session_id))}
        if magasin_id:
            query["magasin_id"] = ObjectId(str(magasin_id))
        return CaisseSessionModel.get_collection().find_one(query)

    @staticmethod
    def fermer(session_id, fermee_par, montant_compte, resume, ecart, note=None):
        """Ferme la session si elle est encore ouverte (une double fermeture est sans effet)."""
        return CaisseSessionModel.get_collection().find_one_and_update(
            {"_id": ObjectId(str(session_id)), "statut": OUVERTE},
            {"$set": {
                "statut": FERMEE,
                "fermee_le": datetime.utcnow(),
                "fermee_par_id": ObjectId(str(fermee_par)),
                "montant_compte": round(float(montant_compte), 2),
                "note_fermeture": (note or "").strip()[:300] or None,
                "resume": resume,
                "ecart": round(float(ecart), 2),
            }},
            return_document=True,
        )

    @staticmethod
    def lister(magasin_id, caissier_id=None, statut=None, debut=None, fin=None, limite=200):
        query = {"magasin_id": ObjectId(str(magasin_id))}
        if caissier_id:
            query["caissier_id"] = ObjectId(str(caissier_id))
        if statut in (OUVERTE, FERMEE):
            query["statut"] = statut
        if debut or fin:
            query["ouverte_le"] = {}
            if debut:
                query["ouverte_le"]["$gte"] = debut
            if fin:
                query["ouverte_le"]["$lte"] = fin
        return list(CaisseSessionModel.get_collection().find(query).sort("ouverte_le", -1).limit(limite))
