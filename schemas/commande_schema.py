# schemas/commande_schema.py

from marshmallow import Schema, fields
from marshmallow.validate import OneOf

class ProduitCommandeSchema(Schema):
    produit_id = fields.String(required=True, metadata={"description": "ID du produit commandé"})
    nom = fields.String(required=True, metadata={"description": "Nom du produit"})
    quantite = fields.Integer(required=True, metadata={"description": "Quantité commandée"})
    prix = fields.Float(required=True, metadata={"description": "Prix unitaire"})

class CreateCommandeSchema(Schema):
    client_id = fields.String(required=True, metadata={"description": "ID du client"})
    magasin_id = fields.String(required=True, metadata={"description": "ID du magasin"})
    produits = fields.List(fields.Nested(ProduitCommandeSchema), required=True, metadata={"description": "Liste des produits commandés"})
    montant_total = fields.Float(required=True, metadata={"description": "Montant total de la commande"})
    statut = fields.String(
        required=False,
        validate=OneOf(["en_attente", "validée", "livrée", "annulée"]),
        load_default="en_attente",
        metadata={"description": "Statut de la commande"}
    )
    adresse_livraison = fields.String(required=True, metadata={"description": "Adresse de livraison"})
    date_commande = fields.DateTime(dump_only=True, metadata={"description": "Date de création de la commande"})
    created_at = fields.DateTime(dump_only=True, metadata={"description": "Date d'enregistrement"})
    updated_at = fields.DateTime(dump_only=True, metadata={"description": "Dernière mise à jour"})


class UpdateCommandeSchema(Schema):
    client_id = fields.String(metadata={"description": "ID du client"})
    magasin_id = fields.String(metadata={"description": "ID du magasin"})
    produits = fields.List(fields.Nested(ProduitCommandeSchema), metadata={"description": "Liste des produits commandés"})
    montant_total = fields.Float(metadata={"description": "Montant total de la commande"})
    statut = fields.String(
        validate=OneOf(["en_attente", "validée", "livrée", "annulée"]),
        metadata={"description": "Statut de la commande"}
    )
    adresse_livraison = fields.String(metadata={"description": "Adresse de livraison"})
    date_commande = fields.DateTime(dump_only=True, metadata={"description": "Date de création de la commande"})
    created_at = fields.DateTime(dump_only=True, metadata={"description": "Date d'enregistrement"})
    updated_at = fields.DateTime(dump_only=True, metadata={"description": "Dernière mise à jour"})