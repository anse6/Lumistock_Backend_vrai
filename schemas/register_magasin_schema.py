# schemas/register_magasin_schema.py

from marshmallow import fields, validate

from schemas.magasin_schema import NouveauMagasinSchema


class RegisterMagasinSchema(NouveauMagasinSchema):
    """Inscription publique : mêmes champs que la création par le staff, plus
    l'acceptation des conditions (le magasin attend la validation du staff)."""
    accepte_conditions = fields.Boolean(required=True, validate=validate.Equal(
        True, error="Vous devez accepter les conditions d'utilisation."))
