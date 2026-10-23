from marshmallow import Schema, fields, validate

class FournisseurSchema(Schema):
    nom = fields.String(required=True)
    pays = fields.String(required=False)
    ville = fields.String(required=False)
    telephone = fields.String(required=False)
    email = fields.Email(required=False)
    adresse = fields.String(required=False)
    magasin_id = fields.String(required=False)

class UpdateFournisseurSchema(Schema):
    nom = fields.String(required=False)
    pays = fields.String(required=False)
    ville = fields.String(required=False)
    telephone = fields.String(required=False)
    email = fields.Email(required=False)
    adresse = fields.String(required=False)
