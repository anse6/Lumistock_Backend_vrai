# schemas/magasin_schema.py

from marshmallow import Schema, fields, validate


def blank_to_none(data, schema):
    """Les formulaires envoient "" pour un champ optionnel laissé vide.

    Pour les champs qui acceptent ``None``, la chaîne vide devient ``None`` :
    le champ est ignoré à la création et effacé lors d'une mise à jour, au lieu
    d'échouer sur la validation de format (devise, code pays, URL...).
    """
    cleaned = dict(data)
    for name, field in schema.fields.items():
        if field.allow_none and isinstance(cleaned.get(name), str) and not cleaned[name].strip():
            cleaned[name] = None
    return cleaned


class LicenceSchema(Schema):
    max_managers = fields.Int(required=True, validate=validate.Range(min=1))
    max_livreurs = fields.Int(required=True, validate=validate.Range(min=1))
    max_caissiers = fields.Int(required=False, validate=validate.Range(min=0))


class StoreProfileSchema(Schema):
    """Champs facultatifs du profil entreprise, communs à toutes les opérations."""
    raison_sociale = fields.String(required=False, allow_none=True, validate=validate.Length(max=255))
    forme_juridique = fields.String(required=False, allow_none=True, validate=validate.Length(max=100))
    country_code = fields.String(required=False, allow_none=True, validate=validate.Regexp(r"^[A-Z]{2}$"))
    region = fields.String(required=False, allow_none=True, validate=validate.Length(max=150))
    adresse_ligne_1 = fields.String(required=False, allow_none=True, validate=validate.Length(max=255))
    adresse_ligne_2 = fields.String(required=False, allow_none=True, validate=validate.Length(max=255))
    code_postal = fields.String(required=False, allow_none=True, validate=validate.Length(max=30))
    site_web = fields.Url(required=False, allow_none=True)
    nom_contact = fields.String(required=False, allow_none=True, validate=validate.Length(max=120))
    prenom_contact = fields.String(required=False, allow_none=True, validate=validate.Length(max=120))
    fonction_contact = fields.String(required=False, allow_none=True, validate=validate.Length(max=120))
    numero_immatriculation = fields.String(required=False, allow_none=True, validate=validate.Length(max=120))
    numero_fiscal = fields.String(required=False, allow_none=True, validate=validate.Length(max=120))
    devise = fields.String(required=False, allow_none=True, validate=validate.Regexp(r"^[A-Z]{3}$"))
    fuseau_horaire = fields.String(required=False, allow_none=True, validate=validate.Length(max=80))


class NouveauMagasinSchema(StoreProfileSchema):
    """Magasin et administrateur : champs communs à la création par le staff et
    à l'inscription publique (mêmes règles des deux côtés)."""
    denomination = fields.String(required=True, validate=validate.Length(min=3, max=120))
    pays = fields.String(required=True, validate=validate.Length(min=1))
    ville = fields.String(required=True, validate=validate.Length(min=1))
    quartier = fields.String(required=True, validate=validate.Length(min=1, max=120))
    email = fields.Email(required=True)
    telephone = fields.String(required=True, validate=validate.Regexp(r"^\+[1-9]\d{6,14}$"))
    activité = fields.String(required=True, validate=validate.Length(min=1, max=255))

    # Administrateur du magasin, à plat. Pas de mot de passe : il reçoit un
    # lien pour choisir le sien.
    admin_name = fields.String(required=True, validate=validate.Length(min=2, max=80))
    admin_first_name = fields.String(required=True, validate=validate.Length(min=2, max=80))
    admin_email = fields.Email(required=True)
    # Facultatifs, validés ensuite par services.profil_utilisateur.
    admin_telephone = fields.String(required=False, allow_none=True)
    admin_sexe = fields.String(required=False, allow_none=True)
    admin_date_naissance = fields.String(required=False, allow_none=True)
    admin_fonction = fields.String(required=False, allow_none=True, validate=validate.Length(max=100))


class CreateMagasinSchema(NouveauMagasinSchema):
    """Création par le staff : le magasin est actif et sa licence est choisie."""
    logo = fields.String(required=False)
    nombre_manager = fields.Int(required=True, validate=validate.Range(min=1))
    nombre_livreur = fields.Int(required=True, validate=validate.Range(min=1))
    nombre_caissier = fields.Int(required=False, validate=validate.Range(min=0))


class MagasinUpdateSchema(StoreProfileSchema):
    # Champs obligatoires du magasin : modifiables, mais jamais vidés.
    denomination = fields.String(required=False, validate=validate.Length(min=3))
    pays = fields.String(required=False, validate=validate.Length(min=1))
    ville = fields.String(required=False, validate=validate.Length(min=1))
    quartier = fields.String(required=False, validate=validate.Length(min=1))
    email = fields.Email(required=False)
    telephone = fields.String(required=False, validate=validate.Regexp(r"^\+[1-9]\d{6,14}$"))
    activité = fields.String(required=False, validate=validate.Length(min=1))
    licence = fields.Nested(LicenceSchema, required=False)
