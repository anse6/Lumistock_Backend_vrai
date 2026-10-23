from marshmallow import Schema, fields, validate, pre_load

class CreateClientSchema(Schema):
    nom = fields.String(required=True, validate=validate.Length(min=1))
    telephone = fields.String(required=False, allow_none=True)
    email = fields.Email(required=False, allow_none=True)
    ville = fields.String(required=False, allow_none=True)
    quartier = fields.String(required=False, allow_none=True)
    pays = fields.String(required=False, allow_none=True, validate=validate.Length(equal=2))  # code ISO (CM, CI…)
    magasin_id = fields.String(required=True)
    photo_profil = fields.Raw(required=False, metadata={"type": "file"})
    montant_en_caisse = fields.Float(required=False, allow_none=True)
    created_by = fields.String(required=True, load_only=True)  
    
    @pre_load
    def process_fields(self, data, **kwargs):
        for field in ['email', 'telephone', 'ville', 'quartier', 'pays']:
            if field in data and data[field] == '':
                data[field] = None
        return data

class UpdateClientSchema(Schema):
    nom = fields.String(validate=validate.Length(min=1))
    telephone = fields.String(allow_none=True)
    email = fields.Email(allow_none=True)
    ville = fields.String(allow_none=True)
    quartier = fields.String(allow_none=True)
    pays = fields.String(allow_none=True, validate=validate.Length(equal=2))  # code ISO (CM, CI…)
    photo_profil = fields.Raw(required=False, metadata={"type": "file"})
    montant_en_caisse = fields.Float(required=False, allow_none=True)

    @pre_load
    def process_fields(self, data, **kwargs):
        for field in ['email', 'telephone', 'ville', 'quartier', 'pays']:
            if field in data and data[field] == '':
                data[field] = None
        return data
