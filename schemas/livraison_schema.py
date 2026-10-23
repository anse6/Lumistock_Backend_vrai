# schemas/livraison_schema.py

from marshmallow import Schema, fields, validate
from datetime import datetime

class ConditionnementSchema(Schema):
    nom = fields.Str(required=True)
    quantite = fields.Int(required=True, validate=validate.Range(min=1))
    prix_conditionnement = fields.Float(required=False, validate=validate.Range(min=0))



class LivraisonSchema(Schema):
    manager_id = fields.Str(required=False, validate=validate.Length(equal=24))
    client_id = fields.Str(required=False, validate=validate.Length(equal=24))
    livreur_id = fields.Str(required=True, validate=validate.Length(equal=24))
    produits = fields.List(fields.Dict(), required=True)
    conditionnements = fields.List(fields.Nested(ConditionnementSchema), required=False, load_default=[])
    statut_paiement = fields.Str(required=True, validate=validate.OneOf(["non payé", "payé", "partiellement payé"]))
    statut_livraison = fields.Str(required=False, load_default="en cours", validate=validate.OneOf(["en cours", "livrée", "annulée"]))
    date_commande = fields.DateTime(required=False ,load_default=datetime.utcnow)
    date_livraison = fields.DateTime(required=False)
    magasin_id = fields.Str(required=True, validate=validate.Length(equal=24))
    montant_paye = fields.Float(required=False, validate=validate.Range(min=0))
    relicat = fields.Float(required=False, validate=validate.Range(min=0))
    conditionnement = fields.Dict(required=False)
    retour = fields.Float(required=False, validate=validate.Range(min=0))
    ristourne = fields.Float(required=False, validate=validate.Range(min=0))
    # Réductions commerciales (rabais, remise, ristourne) : voir reductions_service
    reductions = fields.List(fields.Dict(), required=False)    
    action_manager = fields.Boolean(required=False, load_default=False)
    methode_paiement = fields.Str(required=False, load_default="cash", validate=validate.OneOf(["cash", "om", "momo", "bancaire"]))
    paiements = fields.List(fields.Dict(), required=False)
    dette_ancienne = fields.Float(required=False, load_default=0, allow_none=True, validate=validate.Range(min=0))  # Optionnel : dette existante avant informatisation
    appliquer_tva = fields.Boolean(required=False, load_default=False)  # TVA du pays du magasin
    class Meta:
        ordered = True


class LivraisonManagerSchema(Schema):
    manager_id = fields.Str(required=False, validate=validate.Length(equal=24))
    client_id = fields.Str(required=False, validate=validate.Length(equal=24))
    #livreur_id = fields.Str(required=True, validate=validate.Length(equal=24))
    produits = fields.List(fields.Dict(), required=True)
    conditionnements = fields.List(fields.Nested(ConditionnementSchema), required=False, load_default=[])
    statut_paiement = fields.Str(required=True, validate=validate.OneOf(["non payé", "payé", "partiellement payé"]))
    statut_livraison = fields.Str(required=False, load_default="en cours", validate=validate.OneOf(["en cours", "livrée", "annulée"]))
    date_commande = fields.DateTime(required=False ,load_default=datetime.utcnow)
    date_livraison = fields.DateTime(required=False)
    magasin_id = fields.Str(required=True, validate=validate.Length(equal=24))
    montant_paye = fields.Float(required=False, validate=validate.Range(min=0))
    relicat = fields.Float(required=False, validate=validate.Range(min=0))
    retour = fields.Float(required=False, validate=validate.Range(min=0))
    ristourne = fields.Float(required=False, validate=validate.Range(min=0))
    # Réductions commerciales (rabais, remise, ristourne) : voir reductions_service
    reductions = fields.List(fields.Dict(), required=False)    
    action_manager = fields.Boolean(required=False, load_default=False)
    methode_paiement = fields.Str(required=False, load_default="cash", validate=validate.OneOf(["cash", "om", "momo", "bancaire"]))
    paiements = fields.List(fields.Dict(), required=False)
    dette_ancienne = fields.Float(required=False, load_default=0, allow_none=True, validate=validate.Range(min=0))  # Optionnel : dette existante avant informatisation
    appliquer_tva = fields.Boolean(required=False, load_default=False)  # TVA du pays du magasin
    class Meta:
        ordered = True
        


class RetourLivraisonSchema(Schema):
    livraison_id = fields.Str(required=True, validate=validate.Length(equal=24))
    produits = fields.List(fields.Dict(), required=True)  # liste de produits retournés
    motif = fields.Str(required=False)
    manager_id = fields.Str(validate=validate.Length(equal=24))
    livreur_id = fields.Str(validate=validate.Length(equal=24))
    # Part de la valeur retournée gardée par le magasin (frais de retour), en %.
    # Absente : politique du magasin (0 % par défaut).
    retenue_pourcentage = fields.Float(required=False, allow_none=True, validate=validate.Range(min=0, max=100))
    created_at = fields.DateTime(dump_only=True, load_default=datetime.utcnow)

    class Meta:
        ordered = True

class UpdateLivraisonManagerSchema(Schema):
    produits = fields.List(fields.Dict(), required=False)
    statut_paiement = fields.Str(required=False, validate=validate.OneOf(["non payé", "payé", "partiellement payé"]))
    statut_livraison = fields.Str(required=False, validate=validate.OneOf(["en cours", "livrée", "annulée"]))
    date_livraison = fields.DateTime(required=False)
    montant_paye = fields.Float(required=False, validate=validate.Range(min=0))
    relicat = fields.Float(required=False, validate=validate.Range(min=0))
    retour = fields.Float(required=False, validate=validate.Range(min=0))
    ristourne = fields.Float(required=False, validate=validate.Range(min=0))
    # Réductions commerciales (rabais, remise, ristourne) : voir reductions_service
    reductions = fields.List(fields.Dict(), required=False)
    action_manager = fields.Boolean(required=False)
    methode_paiement = fields.Str(required=False, validate=validate.OneOf(["cash", "om", "momo", "bancaire"]))
    paiements = fields.List(fields.Dict(), required=False)
    client_id = fields.Str(required=False, validate=validate.Length(equal=24))
    dette_ancienne = fields.Float(required=False, allow_none=True, validate=validate.Range(min=0))
    appliquer_tva = fields.Boolean(required=False)


    class Meta:
        ordered = True