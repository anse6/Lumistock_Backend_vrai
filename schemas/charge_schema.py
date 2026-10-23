# schemas/charge_schema.py

from marshmallow import Schema, ValidationError, fields, validate, validates_schema

STATUTS_CHARGE = ("payee", "a_payer")
RECURRENCES = ("aucune", "mensuelle", "trimestrielle", "annuelle")


class ChargeSchema(Schema):
    id = fields.String(dump_only=True, attribute="_id")
    nom = fields.String(required=True, validate=validate.Length(min=1))
    # Montant TTC payé ou à payer au fournisseur.
    montant = fields.Float(required=True, validate=validate.Range(min=0))
    # Part de TVA récupérable comprise dans le montant.
    montant_tva = fields.Float(required=False, load_default=0, validate=validate.Range(min=0))
    categorie = fields.String(required=True)
    magasin_id = fields.String(required=False)
    date_charge = fields.DateTime(required=False)
    description = fields.String(required=False, allow_none=True)
    fournisseur = fields.String(required=False, allow_none=True)
    reference = fields.String(required=False, allow_none=True)
    statut = fields.String(required=False, load_default="payee", validate=validate.OneOf(STATUTS_CHARGE))
    date_paiement = fields.DateTime(required=False, allow_none=True)
    date_echeance = fields.DateTime(required=False, allow_none=True)
    mode_paiement = fields.String(required=False, allow_none=True)
    recurrence = fields.String(required=False, load_default="aucune", validate=validate.OneOf(RECURRENCES))
    created_at = fields.DateTime(dump_only=True)
    updated_at = fields.DateTime(dump_only=True)

    @validates_schema
    def _tva_dans_le_montant(self, data, **kwargs):
        if "montant" in data and (data.get("montant_tva") or 0) > data["montant"]:
            raise ValidationError("La TVA ne peut pas dépasser le montant de la charge.", "montant_tva")


class CreateChargeSchema(ChargeSchema):
    pass


class UpdateChargeSchema(ChargeSchema):
    """Tous les champs sont facultatifs ; aucune valeur par défaut n'écrase l'existant."""

    nom = fields.String(validate=validate.Length(min=1))
    montant = fields.Float(validate=validate.Range(min=0))
    montant_tva = fields.Float(validate=validate.Range(min=0))
    categorie = fields.String()
    statut = fields.String(validate=validate.OneOf(STATUTS_CHARGE))
    recurrence = fields.String(validate=validate.OneOf(RECURRENCES))


class PayerChargeSchema(Schema):
    date_paiement = fields.DateTime(required=False, allow_none=True)
    mode_paiement = fields.String(required=False, load_default="cash")
