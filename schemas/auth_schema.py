# schemas/auth_schemas.py
# La robustesse des nouveaux mots de passe est contrôlée par
# utils.passwords.erreur_robustesse (message clair en français).

from marshmallow import Schema, fields, validate


class ResetPasswordSchema(Schema):
    new_password = fields.Str(required=True, validate=validate.Length(min=1, max=128))
    confirm_password = fields.Str(required=True, validate=validate.Length(min=1, max=128))

class LoginSchema(Schema):
    email = fields.Str(required=True, validate=validate.Email())
    password = fields.Str(required=True, validate=validate.Length(min=1, max=128))



class ChangePasswordSchema(Schema):
    old_password = fields.Str(required=True, validate=validate.Length(min=1, max=128))
    new_password = fields.Str(required=True, validate=validate.Length(min=1, max=128))
    confirm_password = fields.Str(required=True, validate=validate.Length(min=1, max=128))
