"""Version 1 SQL schema. Existing public 24-character identifiers are retained.

Scalar business fields are SQL columns. Nested records (sale lines, payments,
packaging, stock history) remain JSON so existing API payloads stay compatible.
The _extra and _meta columns preserve legacy optional fields and their types.
References are indexed, without cascading deletion of historical sale records.
"""

from sqlalchemy import (
    MetaData, Table, Column, String, Text, Integer, Boolean, DateTime,
    Numeric, JSON, Index, UniqueConstraint,
)
from sqlalchemy.dialects.mysql import DATETIME, LONGTEXT

metadata = MetaData()
DATE = DateTime().with_variant(DATETIME(fsp=6), "mysql", "mariadb")
TEXT = Text().with_variant(LONGTEXT(), "mysql", "mariadb")
MONEY = Numeric(24, 8)

# Fields common to every entity; absence is distinguished from explicit null.
COMMON = "created_at updated_at deleted_at"
FIELDS = {
    "magasin": (
        "denomination raison_sociale forme_juridique pays country_code region ville quartier "
        "adresse_ligne_1 adresse_ligne_2 code_postal email telephone site_web nom_contact "
        "prenom_contact fonction_contact numero_immatriculation numero_fiscal devise fuseau_horaire "
        "activité logo licence created_by_staff is_active active"
    ),
    "users": "name first_name email password role magasin_id is_active nom_boutique barcode_mode avatar",
    "produits": "nom description image prix prix_achat unite quantite quantite_initiale etat categorie_id fournisseur_id magasin_id mouvements_stock conditionnements code_barre",
    "categories": "nom description magasin_id",
    "unites": "unite magasin_id",
    "fournisseurs": "nom telephone email adresse ville magasin_id",
    "clients": "nom created_by telephone email ville quartier adresse photo_profil magasin_id montant_en_caisse",
    "approvisionnements": "magasin_id livreur_id admin_id produits",
    "stocks_livreur": "livreur_id produit_id quantite",
    "livraisons": "livreur_id manager_id magasin_id client_id produits montant_total montant_produits dette_ancienne montant_paye montant_deduit_caisse quantite statut_paiement statut_livraison date_commande date_livraison relicat ristourne retour have_ristourne have_retour action_manager methode_paiement paiements",
    "retours_livraison": "livraison_id livreur_id client_id manager_id magasin_id produits montant_retour motif",
    "ristournes": "livraison_id livreur_id client_id magasin_id montant_initial montant_paye ristourne motif",
    "notifications": "title message type lien timestamp vu archivee magasin_id",
    "connexions": "user_id email role magasin_id timestamp ip_address",
    "charges": "nom montant categorie magasin_id date_charge description",
    "assets": "nom valeur_achat date_achat duree_vie_annees magasin_id categorie description",
    "chat_conversations": "user_id magasin_id title",
    "chat_messages": "conversation_id role content timestamp",
    "images_staff": "nom categorie description tags image url filename",
    # Comptabilité : achats à crédit, relevés bancaires, exercices clôturés.
    "factures_fournisseurs": (
        "magasin_id fournisseur_id fournisseur_nom numero date_facture date_echeance lignes montant_ht "
        "montant_tva montant_ttc montant_paye statut paiements avoirs pieces_jointes note cree_par"
    ),
    "releves_bancaires": "magasin_id compte date_operation libelle montant reference statut rapproche_avec import_id",
    "clotures": "magasin_id exercice date_debut date_fin resultat bilan compte_resultat cloture_par",
    # Sécurité : tokens de connexion révoqués (déconnexion), conservés jusqu'à expiration.
    "revoked_tokens": "jti user_id type expires_at",
    # Messagerie clients (SMS / email) : réglages et modèles du magasin,
    # campagnes envoyées et journal de chaque message.
    "parametres_messages": "magasin_id canaux modeles options",
    "campagnes_messages": (
        "magasin_id cree_par segment canaux_envoi statut total envoyes echecs ignores termine_at"
    ),
    "messages_clients": (
        "campagne_id magasin_id client_id canal segment destinataire contenu statut erreur reference envoye_at"
    ),
    # Envois programmés à une date (un client ou une catégorie) et relances
    # automatiques récurrentes, exécutés par le planificateur.
    "envois_programmes": "magasin_id cree_par segment canaux_envoi client_ids date_prevue statut campagne_id",
    "regles_messages": (
        "magasin_id cree_par nom segment canaux_envoi frequence jours heure actif "
        "prochaine_execution derniere_execution"
    ),
    # Caisse : ouverture (fond de caisse) et fermeture (comptage, écart) par caissier.
    "sessions_caisse": (
        "magasin_id caissier_id statut fond_initial note_ouverture ouverte_le fermee_le fermee_par_id "
        "montant_compte note_fermeture resume ecart"
    ),
    # Impôts : reversements de TVA et paiements de l'impôt sur le résultat.
    "operations_fiscales": "magasin_id type periode montant date_operation mode_paiement reference note cree_par",
    # Dette d'ouverture : ce qu'un client devait avant l'installation de
    # Lumistock (cahier de crédit), soldée par ses règlements.
    "dettes_ouverture": "magasin_id client_id montant montant_paye date_origine note cree_par paiements",
    # Messages du formulaire de contact du site public, traités par le staff.
    "messages_contact": "nom email telephone entreprise sujet message statut lu note reponses ip traite_par traite_le",
    # Double authentification (facultative) : secret TOTP et codes de secours
    # d'un compte. Table à part : le secret ne sort jamais avec le profil.
    "deux_facteurs": "user_id actif secret secret_en_attente codes_secours dernier_pas active_le",
    # Assistant Nansé du site public : conversations des visiteurs (nom, email,
    # jeton d'accès haché) et leurs messages (visiteur, IA, conseiller, système).
    "assistant_conversations": (
        "nom email langue statut jeton questions_pertinentes notifie_le agent_id agent_nom "
        "non_lus_staff non_lus_visiteur dernier_message derniere_activite vu_par_visiteur_le ip page"
    ),
    "assistant_messages": "conversation_id auteur auteur_nom staff_id texte pertinente sources",
    # Support interne : conversations entre l'administrateur d'un magasin et le
    # staff Lumistock (super-admin) ; chacun peut ouvrir une conversation.
    "support_conversations": (
        "magasin_id admin_id admin_nom magasin_nom sujet statut initie_par staff_id staff_nom "
        "non_lus_admin non_lus_staff dernier_message vu_admin_le vu_staff_le notifie_admin_le notifie_staff_le"
    ),
    "support_messages": "conversation_id magasin_id auteur auteur_id auteur_nom texte",
    # Financement : apports, retraits de l'exploitant, emprunts et remboursements.
    "financements": (
        "magasin_id type libelle tiers montant interets date_operation mode_paiement emprunt_id note cree_par"
    ),
}
JSON_FIELDS = set("licence mouvements_stock conditionnements produits paiements avoirs tags lignes pieces_jointes "
                  "rapproche_avec bilan compte_resultat canaux modeles options canaux_envoi client_ids jours resume codes_secours reponses "
                  "sources".split())
BOOL_FIELDS = set("created_by_staff is_active active barcode_mode have_ristourne have_retour action_manager vu archivee "
                  "actif lu pertinente".split())
INT_FIELDS = set(("quantite quantite_initiale duree_vie_annees exercice total envoyes echecs ignores dernier_pas "
                  "questions_pertinentes non_lus_staff non_lus_visiteur non_lus_admin").split())
MONEY_FIELDS = set("prix prix_achat montant_en_caisse montant_total montant_produits dette_ancienne montant_paye montant_deduit_caisse relicat ristourne retour montant_retour montant_initial montant valeur_achat montant_ht montant_tva montant_ttc resultat fond_initial montant_compte ecart interets".split())
DATE_FIELDS = set((COMMON + " timestamp date_commande date_livraison date_charge date_achat date_facture date_echeance "
                    "date_operation date_debut date_fin expires_at termine_at envoye_at date_prevue prochaine_execution "
                    "derniere_execution ouverte_le fermee_le date_origine active_le traite_le notifie_le derniere_activite "
                    "vu_par_visiteur_le vu_admin_le vu_staff_le notifie_admin_le notifie_staff_le").split())
INDEX_FIELDS = {"email", "role", "country_code", "code_barre", "deleted_at", "created_at", "date_livraison", "date_charge"}


def field_type(name):
    if name.endswith("_id") or name == "created_by":
        return String(24)
    if name in JSON_FIELDS:
        return JSON()
    if name in BOOL_FIELDS:
        return Boolean()
    if name in INT_FIELDS:
        return Integer()
    if name in MONEY_FIELDS:
        return MONEY
    if name in DATE_FIELDS:
        return DATE
    if name in INDEX_FIELDS:
        return String(255)
    return TEXT


tables = {}
for name, fields in FIELDS.items():
    columns = [Column("_id", String(24), primary_key=True)]
    for field in (fields + " " + COMMON).split():
        columns.append(Column(field, field_type(field), nullable=True))
    columns.extend([Column("_extra", JSON, nullable=False), Column("_meta", JSON, nullable=False)])
    table = Table(name, metadata, *columns, mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_bin")
    for field in (fields + " " + COMMON).split():
        if field.endswith("_id") or field in INDEX_FIELDS:
            Index(f"ix_{name}_{field}", table.c[field])
    if "magasin_id" in table.c:
        Index(f"ix_{name}_magasin_created", table.c.magasin_id, table.c.created_at)
    tables[name] = table

UniqueConstraint(tables["stocks_livreur"].c.livreur_id, tables["stocks_livreur"].c.produit_id, name="uq_stock_livreur_produit")

migrations = Table(
    "schema_migrations", metadata,
    Column("version", String(100), primary_key=True),
    Column("applied_at", DATE, nullable=False),
    Column("details", JSON, nullable=False),
    mysql_engine="InnoDB", mysql_charset="utf8mb4",
)
