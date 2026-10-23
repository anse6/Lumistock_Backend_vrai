# schemas/asset_schema.py

from marshmallow import Schema, ValidationError, fields, validate, validates_schema


class AssetSchema(Schema):
    id = fields.String(dump_only=True, attribute="_id")
    nom = fields.String(required=True, validate=validate.Length(min=1))
    valeur_achat = fields.Float(required=True, validate=validate.Range(min=0))
    # Valeur estimée en fin d'utilisation (non amortie).
    valeur_residuelle = fields.Float(required=False, validate=validate.Range(min=0))
    # Vrai : valeur résiduelle calculée d'après la catégorie (% de la valeur d'achat).
    residuelle_auto = fields.Boolean(required=False)
    # TVA récupérable payée sur l'achat (la valeur d'achat est hors taxe).
    montant_tva = fields.Float(required=False, validate=validate.Range(min=0))
    fournisseur = fields.String(required=False, allow_none=True)
    reference = fields.String(required=False, allow_none=True)
    date_achat = fields.DateTime(required=False)
    duree_vie_annees = fields.Integer(required=True, validate=validate.Range(min=1))
    categorie = fields.String(required=False)
    description = fields.String(required=False, allow_none=True)
    mode_paiement = fields.String(required=False, allow_none=True)
    magasin_id = fields.String(required=False)
    created_at = fields.DateTime(dump_only=True)
    updated_at = fields.DateTime(dump_only=True)

    @validates_schema
    def _residuelle(self, data, **kwargs):
        if "valeur_achat" in data and (data.get("valeur_residuelle") or 0) > data["valeur_achat"]:
            raise ValidationError("La valeur résiduelle ne peut pas dépasser la valeur d'achat.", "valeur_residuelle")
        if "valeur_achat" in data and (data.get("montant_tva") or 0) > data["valeur_achat"]:
            raise ValidationError("La TVA ne peut pas dépasser la valeur d'achat.", "montant_tva")


class CreateAssetSchema(AssetSchema):
    pass


class UpdateAssetSchema(AssetSchema):
    nom = fields.String(validate=validate.Length(min=1))
    valeur_achat = fields.Float(validate=validate.Range(min=0))
    valeur_residuelle = fields.Float(validate=validate.Range(min=0))
    duree_vie_annees = fields.Integer(validate=validate.Range(min=1))


class CessionAssetSchema(Schema):
    date_cession = fields.DateTime(required=True)
    prix_cession = fields.Float(required=True, validate=validate.Range(min=0))
    mode_paiement = fields.String(required=False, allow_none=True)
    acheteur = fields.String(required=False, allow_none=True)
