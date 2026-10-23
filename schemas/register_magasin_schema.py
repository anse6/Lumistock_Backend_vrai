# schemas/register_magasin_schema.py

from marshmallow import fields, validate

from schemas.magasin_schema import StoreProfileSchema


class RegisterMagasinSchema(StoreProfileSchema):
    denomination = fields.String(required=True, validate=validate.Length(min=3))
    pays = fields.String(required=True)
    ville = fields.String(required=True)
    quartier = fields.String(required=True)
    email = fields.Email(required=True)
    telephone = fields.String(required=True, validate=validate.Regexp(r"^\+[1-9]\d{6,14}$"))
    activité = fields.String(required=True)

    # Informations de l'admin (fournies à plat pour simplifier le formulaire public)
    admin_name = fields.String(required=True, validate=validate.Length(min=2))
    admin_first_name = fields.String(required=True, validate=validate.Length(min=2))
    admin_email = fields.Email(required=True)
    # Facultatifs, comme à la création par le staff (validés par profil_utilisateur).
    admin_telephone = fields.String(required=False, allow_none=True)
    admin_sexe = fields.String(required=False, allow_none=True)
    admin_date_naissance = fields.String(required=False, allow_none=True)
    admin_fonction = fields.String(required=False, allow_none=True, validate=validate.Length(max=100))
    # Conditions d'utilisation : doivent être acceptées explicitement.
    accepte_conditions = fields.Boolean(required=True, validate=validate.Equal(
        True, error="Vous devez accepter les conditions d'utilisation."))
