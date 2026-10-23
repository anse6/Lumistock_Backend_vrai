"""TVA appliquée aux ventes selon le pays du magasin.

Le taux est celui du régime général du pays (taux standard). Au Cameroun,
19,25 % = TVA de 17,5 % + centimes additionnels communaux (10 % de la TVA).
Les prix des articles sont hors taxe : la TVA s'ajoute au montant net
(après rabais, remise et ristourne), jamais à la dette ancienne.
"""
import unicodedata
from decimal import Decimal, ROUND_HALF_UP

from bson import ObjectId

# Monnaies sans centimes : les montants sont arrondis à l'unité (franc CFA…).
DEVISES_SANS_DECIMALES = {"XAF", "XOF", "FCFA", "CFA", "KMF", "GNF", "RWF", "BIF", "DJF", "UGX", "XPF", "JPY", "KRW"}


def arrondir(montant, decimales=2):
    """Arrondi commercial (0,5 vers le haut), et non l'arrondi au pair de round()."""
    pas = Decimal(1).scaleb(-int(decimales))
    return float(Decimal(str(float(montant or 0))).quantize(pas, rounding=ROUND_HALF_UP))


def decimales_devise(devise):
    """Nombre de décimales de la monnaie ; sans devise renseignée : franc CFA (0)."""
    code = str(devise or "").strip().upper()
    return 0 if not code or code in DEVISES_SANS_DECIMALES else 2

# Taux standard (en %) par code pays ISO 3166-1 alpha-2.
TAUX_TVA = {
    # CEMAC
    "CM": 19.25, "GA": 18.0, "CG": 18.0, "TD": 18.0, "CF": 19.0, "GQ": 15.0,
    # UEMOA
    "CI": 18.0, "SN": 18.0, "BJ": 18.0, "BF": 18.0, "ML": 18.0, "NE": 19.0, "TG": 18.0, "GW": 19.0,
    # Reste de l'Afrique
    "GN": 18.0, "NG": 7.5, "GH": 15.0, "KE": 16.0, "MA": 20.0, "TN": 19.0, "DZ": 19.0, "EG": 14.0,
    "CD": 16.0, "RW": 18.0, "BI": 18.0, "MG": 20.0, "ZA": 15.0, "MU": 15.0, "MR": 16.0, "DJ": 10.0,
    "KM": 10.0, "CV": 15.0, "ET": 15.0, "TZ": 18.0, "UG": 18.0, "AO": 14.0, "MZ": 16.0, "ZM": 16.0,
    "ZW": 15.0, "SL": 15.0, "GM": 15.0,
    # Europe
    "FR": 20.0, "BE": 21.0, "LU": 17.0, "CH": 8.1, "DE": 19.0, "ES": 21.0, "IT": 22.0, "PT": 23.0,
    "NL": 21.0, "GB": 20.0, "IE": 23.0, "AT": 20.0, "PL": 23.0,
    # Autres
    "CA": 5.0, "CN": 13.0, "IN": 18.0, "AE": 5.0, "SA": 15.0, "TR": 20.0,
}

# Nom du pays (sans accents, minuscules) -> code ISO, quand country_code manque.
PAYS_VERS_CODE = {
    "cameroun": "CM", "cameroon": "CM", "gabon": "GA", "congo": "CG", "republique du congo": "CG",
    "tchad": "TD", "chad": "TD", "centrafrique": "CF", "republique centrafricaine": "CF",
    "guinee equatoriale": "GQ", "cote d'ivoire": "CI", "cote divoire": "CI", "senegal": "SN",
    "benin": "BJ", "burkina faso": "BF", "mali": "ML", "niger": "NE", "togo": "TG",
    "guinee-bissau": "GW", "guinee": "GN", "nigeria": "NG", "ghana": "GH", "kenya": "KE",
    "maroc": "MA", "tunisie": "TN", "algerie": "DZ", "egypte": "EG",
    "republique democratique du congo": "CD", "rdc": "CD", "rwanda": "RW", "burundi": "BI",
    "madagascar": "MG", "afrique du sud": "ZA", "maurice": "MU", "mauritanie": "MR",
    "djibouti": "DJ", "comores": "KM", "cap-vert": "CV", "ethiopie": "ET", "tanzanie": "TZ",
    "ouganda": "UG", "angola": "AO", "mozambique": "MZ", "zambie": "ZM", "zimbabwe": "ZW",
    "sierra leone": "SL", "gambie": "GM", "france": "FR", "belgique": "BE", "luxembourg": "LU",
    "suisse": "CH", "allemagne": "DE", "espagne": "ES", "italie": "IT", "portugal": "PT",
    "pays-bas": "NL", "royaume-uni": "GB", "irlande": "IE", "autriche": "AT", "pologne": "PL",
    "canada": "CA", "chine": "CN", "inde": "IN", "emirats arabes unis": "AE",
    "arabie saoudite": "SA", "turquie": "TR",
}


def _sans_accents(texte):
    return "".join(c for c in unicodedata.normalize("NFD", texte or "") if unicodedata.category(c) != "Mn").lower().strip()


def code_pays(magasin):
    """Code ISO du pays du magasin (country_code, sinon déduit du nom du pays)."""
    code = str((magasin or {}).get("country_code") or "").strip().upper()
    if len(code) == 2:
        return code
    return PAYS_VERS_CODE.get(_sans_accents((magasin or {}).get("pays")), "")


def tva_du_magasin(magasin_id):
    """{pays, code, taux} pour le magasin ; taux None si le pays est inconnu."""
    from models.magasin_model import MagasinModel

    magasin = None
    if magasin_id and ObjectId.is_valid(str(magasin_id)):
        magasin = MagasinModel.get_collection().find_one({"_id": ObjectId(str(magasin_id))})
    code = code_pays(magasin)
    return {
        "pays": (magasin or {}).get("pays") or code or None,
        "code": code or None,
        "taux": TAUX_TVA.get(code),
        "decimales": decimales_devise((magasin or {}).get("devise")),
    }


def decimales_magasin(magasin_id):
    """Décimales des montants du magasin (0 pour le franc CFA)."""
    from models.magasin_model import MagasinModel

    if not magasin_id or not ObjectId.is_valid(str(magasin_id)):
        return 0
    magasin = MagasinModel.get_collection().find_one({"_id": ObjectId(str(magasin_id))})
    return decimales_devise((magasin or {}).get("devise"))


def calculer_tva(montant_ht, taux, decimales=2):
    """Montant de TVA sur un montant hors taxe, arrondi à l'unité de la monnaie."""
    if not taux or not montant_ht or montant_ht <= 0:
        return 0.0
    return arrondir(float(montant_ht) * float(taux) / 100.0, decimales)


def tva_vente(livraison_ou_donnees, montant_ht, magasin_id=None):
    """(appliquer_tva, taux_tva, montant_tva) pour une vente.

    - appliquer_tva vient de la vente (ou de la demande) ;
    - le taux déjà enregistré sur la vente est conservé (une vente garde le
      taux de sa date) ; sinon c'est celui du pays du magasin.
    :raises ValueError: TVA demandée mais pays du magasin sans taux connu.
    """
    appliquer = bool(livraison_ou_donnees.get("appliquer_tva"))
    if not appliquer:
        return False, 0.0, 0.0
    taux = livraison_ou_donnees.get("taux_tva")
    if not taux:
        info = tva_du_magasin(magasin_id or livraison_ou_donnees.get("magasin_id"))
        taux = info["taux"]
        if taux is None:
            raise ValueError(
                "TVA impossible : le pays du magasin n'est pas renseigné ou son taux est inconnu "
                "(Paramètres > Profil de l'entreprise)."
            )
    taux = float(taux)
    decimales = decimales_magasin(magasin_id or livraison_ou_donnees.get("magasin_id"))
    return True, taux, calculer_tva(montant_ht, taux, decimales)
