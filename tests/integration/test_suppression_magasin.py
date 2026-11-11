"""La suppression définitive d'un magasin archivé emporte toutes ses données."""
from datetime import datetime

from bson import ObjectId

from extensions import db
from utils.jwt_token import generate_token


def _magasin(nom, archive=False):
    mid = ObjectId()
    doc = {"_id": mid, "denomination": nom, "is_active": not archive, "created_at": datetime.utcnow()}
    if archive:
        doc["deleted_at"] = datetime.utcnow()
    db.tables.magasin.insert_one(doc)
    uid = db.tables.users.insert_one({"email": f"admin@{nom}.test", "role": "admin", "magasin_id": mid,
                                      "is_active": True}).inserted_id
    db.tables.produits.insert_one({"nom": "Riz", "quantite": 1, "magasin_id": mid})
    db.tables.categories.insert_one({"nom": "Céréales", "magasin_id": mid})
    db.tables.livraisons.insert_one({"magasin_id": mid, "montant_total": 100})
    db.tables.deux_facteurs.insert_one({"user_id": uid, "actif": True})
    return mid


def test_suppression_definitive_complete(client, app):
    with app.app_context():
        for t in ("magasin", "users", "produits", "categories", "livraisons", "deux_facteurs"):
            db.tables[t].delete_many({})
        archive, garde = _magasin("archive", archive=True), _magasin("garde")
        staff = str(db.tables.users.insert_one({"email": "staff@x.test", "role": "staff", "is_active": True}).inserted_id)
    headers = {"Authorization": f"Bearer {generate_token(user_id=staff, role='staff')}"}

    assert client.delete(f"/magasin/delete/{garde}/permanent", headers=headers).status_code == 404  # pas archivé
    assert client.delete(f"/magasin/delete/{archive}/permanent", headers=headers).status_code == 200

    with app.app_context():
        for t in ("users", "produits", "categories", "livraisons"):
            assert db.tables[t].count_documents({"magasin_id": archive}) == 0, t
            assert db.tables[t].count_documents({"magasin_id": garde}) == 1, t
        assert db.tables.magasin.find_one({"_id": archive}) is None
        assert db.tables.deux_facteurs.count_documents({}) == 1  # seul celui du magasin gardé reste
