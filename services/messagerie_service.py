# services/messagerie_service.py
"""Messagerie clients : relances et informations par SMS et par email.

Chaque magasin règle dans ses paramètres :
- les canaux qu'il utilise (SMS, email) ;
- les types de message qu'il envoie, un par catégorie de clients, avec un
  texte modifiable (variables {nom}, {dette}, {avance}, {magasin}…) ;
- le délai minimal entre deux messages à un même client.

Catégories de clients, calculées à partir des ventes et du compte client :
- ``debiteurs`` : reste à payer > 0 sur leurs ventes (même calcul que la fiche client) ;
- ``avances``   : solde versé sur leur compte (``montant_en_caisse`` > 0) ;
- ``acheteurs`` : ni dette ni avance ;
- ``tous``      : tous les clients.

Le client de passage (« Autres »), les clients supprimés et ceux qui ont
demandé à ne plus être contactés ne reçoivent jamais de message.

Une campagne prépare d'abord la liste des destinataires (journal
``messages_clients``, statut « en_attente »), puis l'envoi se fait en
arrière-plan : l'administrateur suit la progression dans l'historique.
"""
import copy
import html
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from bson import ObjectId
from flask import current_app

from extensions import db
from services import sms_service
from services.client_service import NOM_CLIENT_PAR_DEFAUT, normaliser_telephone
from services.tva_service import decimales_devise

SEGMENTS = {
    "debiteurs": {"libelle": "Clients qui doivent de l'argent",
                  "description": "Reste à payer supérieur à 0 sur leurs achats."},
    "avances": {"libelle": "Clients ayant un solde versé",
                "description": "Ont versé de l'argent sur leur compte (avance non encore utilisée)."},
    "acheteurs": {"libelle": "Clients sans dette ni avance",
                  "description": "Clients à jour, sans solde sur leur compte."},
    "tous": {"libelle": "Tous les clients", "description": "Tous les clients du magasin."},
}
CANAUX = ("sms", "email")

VARIABLES = {
    "nom": "Nom du client",
    "dette": "Reste à payer (ex. 15 000 FCFA)",
    "total_achats": "Montant total des achats non soldés",
    "deja_paye": "Montant déjà réglé sur ces achats",
    "detail_achats": "Détail de chaque achat non soldé : date, articles, total, payé, reste (pour l'email)",
    "detail_achats_court": "Résumé des achats non soldés, ex. « votre achat du 14/10/2026 (Riz x2) » (pour le SMS)",
    "detail_paiement": "Phrase complète : total, montant déjà réglé et reste à payer",
    "avance": "Solde versé sur le compte du client",
    "mention_avance": "Phrase sur le solde versé, vide si le client n'a pas d'avance",
    "magasin": "Nom du magasin",
    "telephone_magasin": "Téléphone du magasin",
    "date": "Date du jour (JJ/MM/AAAA)",
}

MODELES_DEFAUT = {
    "debiteurs": {
        "actif": True,
        "sms": "Bonjour {nom}, {magasin} vous rappelle le règlement de {detail_achats_court}. "
               "{detail_paiement}{mention_avance} Merci de régulariser votre situation. "
               "Contact : {telephone_magasin}",
        "email_sujet": "Rappel de règlement - {magasin}",
        "email_corps": "Bonjour {nom},\n\nNous vous contactons au sujet des sommes restant à régler chez {magasin}. "
                       "Voici le détail de votre situation au {date} :\n\n{detail_achats}\n\n"
                       "{detail_paiement}{mention_avance}\n\n"
                       "Nous vous remercions de bien vouloir régulariser votre situation dans les meilleurs délais. "
                       "Notre équipe reste à votre disposition au {telephone_magasin} pour toute question.\n\n"
                       "Cordialement,\nL'équipe {magasin}",
    },
    "avances": {
        "actif": False,
        "sms": "Bonjour {nom}, {magasin} vous informe que vous disposez d'un solde de {avance} sur votre "
               "compte client. Il sera automatiquement déduit de vos prochains achats. Merci de votre confiance.",
        "email_sujet": "Votre solde client - {magasin}",
        "email_corps": "Bonjour {nom},\n\nNous vous informons que vous disposez d'un solde de {avance} sur votre "
                       "compte client chez {magasin} (situation au {date}). Ce montant sera automatiquement déduit "
                       "de vos prochains achats.\n\nNous vous remercions de votre confiance.\n\n"
                       "Cordialement,\nL'équipe {magasin}",
    },
    "acheteurs": {
        "actif": False,
        "sms": "Bonjour {nom}, toute l'équipe de {magasin} vous remercie de votre fidélité. Nous restons à "
               "votre disposition pour vos prochains achats. Contact : {telephone_magasin}",
        "email_sujet": "Merci de votre fidélité - {magasin}",
        "email_corps": "Bonjour {nom},\n\nToute l'équipe de {magasin} vous remercie de votre fidélité. "
                       "Nous restons à votre disposition pour vos prochains achats au {telephone_magasin}.\n\n"
                       "Cordialement,\nL'équipe {magasin}",
    },
    "tous": {
        "actif": False,
        "sms": "Bonjour {nom}, {magasin} vous informe : ",
        "email_sujet": "Information - {magasin}",
        "email_corps": "Bonjour {nom},\n\n\n\nCordialement,\nL'équipe {magasin}",
    },
}

# Textes par défaut des versions précédentes : un magasin qui ne les a pas
# modifiés reçoit automatiquement les nouveaux textes.
ANCIENS_MODELES = {
    "Bonjour {nom}, sauf erreur de notre part, vous restez devoir {dette} à {magasin}. "
    "Merci de passer régler votre solde. Infos : {telephone_magasin}",
    "Rappel de paiement - {magasin}",
    "Bonjour {nom},\n\nSauf erreur de notre part, il reste {dette} à régler sur vos achats "
    "chez {magasin} (situation au {date}).\n\nMerci de passer régler votre solde dès que "
    "possible. Pour toute question, contactez-nous au {telephone_magasin}.\n\n"
    "Cordialement,\n{magasin}",
    "Bonjour {nom}, vous disposez d'un solde de {avance} chez {magasin}. "
    "Il sera déduit de vos prochains achats. Merci de votre confiance !",
    "Votre solde chez {magasin}",
    "Bonjour {nom},\n\nVous disposez d'un solde de {avance} sur votre compte chez {magasin} "
    "(situation au {date}). Il sera automatiquement déduit de vos prochains achats.\n\n"
    "Merci de votre confiance,\n{magasin}",
    "Bonjour {nom}, merci pour votre fidélité ! {magasin} vous attend pour vos prochains achats. "
    "Infos : {telephone_magasin}",
    "Merci pour votre fidélité - {magasin}",
    "Bonjour {nom},\n\nMerci pour votre fidélité ! Toute l'équipe de {magasin} vous attend "
    "pour vos prochains achats.\n\nPour toute question : {telephone_magasin}.\n\n"
    "À bientôt,\n{magasin}",
    "Bonjour {nom},\n\n\n\nCordialement,\n{magasin}",
}
PARAMETRES_DEFAUT = {
    "canaux": {"sms": True, "email": False},
    "modeles": MODELES_DEFAUT,
    # Plage horaire des envois programmés et automatiques (heure locale) :
    # en dehors, l'envoi attend l'ouverture de la plage suivante.
    "options": {"delai_relance_heures": 24, "heure_debut_envoi": 8, "heure_fin_envoi": 20},
}

LONGUEUR_MAX = {"sms": 480, "email_sujet": 150, "email_corps": 5000}
DELAI_MAX_HEURES = 24 * 30
CAMPAGNE_INTERROMPUE_APRES = timedelta(minutes=30)
_EMAIL_VALIDE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_VARIABLE = re.compile(r"\{(\w+)\}")


class ErreurMessagerie(ValueError):
    """Demande refusée (message lisible pour l'utilisateur)."""


# ---------------------------------------------------------------------------
# Paramètres et modèles
# ---------------------------------------------------------------------------
def _table(nom):
    return db.tables[nom]


def get_parametres(magasin_id):
    """Paramètres du magasin, complétés par les valeurs par défaut."""
    doc = _table("parametres_messages").find_one({"magasin_id": ObjectId(str(magasin_id))}) or {}
    params = copy.deepcopy(PARAMETRES_DEFAUT)
    params["canaux"].update({k: bool(v) for k, v in (doc.get("canaux") or {}).items() if k in CANAUX})
    for segment, modele in (doc.get("modeles") or {}).items():
        if segment in params["modeles"] and isinstance(modele, dict):
            params["modeles"][segment].update(
                {k: v for k, v in modele.items() if k in ("actif", "sms", "email_sujet", "email_corps")
                 and not (isinstance(v, str) and v in ANCIENS_MODELES)})
    params["options"].update(doc.get("options") or {})
    return params


def verifier_modele(texte, champ):
    """Erreur lisible si le texte est trop long ou utilise une variable inconnue."""
    if not isinstance(texte, str):
        return f"Le champ {champ} doit être un texte."
    if len(texte) > LONGUEUR_MAX[champ]:
        return f"Le texte est trop long ({len(texte)} caractères, {LONGUEUR_MAX[champ]} maximum)."
    inconnues = sorted(set(_VARIABLE.findall(texte)) - set(VARIABLES))
    if inconnues:
        return ("Variable(s) inconnue(s) : " + ", ".join("{" + v + "}" for v in inconnues)
                + ". Variables possibles : " + ", ".join("{" + v + "}" for v in VARIABLES) + ".")
    return None


def enregistrer_parametres(magasin_id, data, user_id):
    """Valide et enregistre les paramètres envoyés (seuls les champs connus)."""
    if not isinstance(data, dict):
        raise ErreurMessagerie("Données invalides.")
    params = get_parametres(magasin_id)

    for canal, actif in (data.get("canaux") or {}).items():
        if canal not in CANAUX:
            raise ErreurMessagerie(f"Canal inconnu : {canal}.")
        params["canaux"][canal] = bool(actif)

    for segment, modele in (data.get("modeles") or {}).items():
        if segment not in SEGMENTS or not isinstance(modele, dict):
            raise ErreurMessagerie(f"Type de message inconnu : {segment}.")
        for champ, valeur in modele.items():
            if champ == "actif":
                params["modeles"][segment]["actif"] = bool(valeur)
                continue
            if champ not in LONGUEUR_MAX:
                raise ErreurMessagerie(f"Champ inconnu : {champ}.")
            erreur = verifier_modele(valeur, champ)
            if erreur:
                raise ErreurMessagerie(f"{SEGMENTS[segment]['libelle']} : {erreur}")
            params["modeles"][segment][champ] = valeur.strip()
        if params["modeles"][segment]["actif"] and not params["modeles"][segment]["sms"].strip() \
                and not params["modeles"][segment]["email_corps"].strip():
            raise ErreurMessagerie(f"{SEGMENTS[segment]['libelle']} : le message ne peut pas être vide.")

    options = data.get("options") or {}
    if "delai_relance_heures" in options:
        try:
            delai = int(options["delai_relance_heures"])
        except (TypeError, ValueError):
            raise ErreurMessagerie("Le délai entre deux messages doit être un nombre d'heures.")
        if not 0 <= delai <= DELAI_MAX_HEURES:
            raise ErreurMessagerie(f"Le délai doit être compris entre 0 et {DELAI_MAX_HEURES} heures.")
        params["options"]["delai_relance_heures"] = delai
    for cle in ("heure_debut_envoi", "heure_fin_envoi"):
        if cle in options:
            try:
                params["options"][cle] = int(options[cle])
            except (TypeError, ValueError):
                raise ErreurMessagerie("Les heures d'envoi doivent être des nombres entiers.")
    debut, fin = params["options"]["heure_debut_envoi"], params["options"]["heure_fin_envoi"]
    if not (0 <= debut < fin <= 24):
        raise ErreurMessagerie("La plage d'envoi doit commencer avant de finir (entre 0 h et 24 h).")

    # Seuls les textes modifiés par le magasin sont enregistrés : les autres
    # suivent les textes par défaut, y compris leurs améliorations futures.
    modeles = {
        segment: {k: v for k, v in modele.items() if k == "actif" or v != MODELES_DEFAUT[segment][k]}
        for segment, modele in params["modeles"].items()
    }
    _table("parametres_messages").update_one(
        {"magasin_id": ObjectId(str(magasin_id))},
        {"$set": {"canaux": params["canaux"], "modeles": modeles, "options": params["options"],
                  "updated_at": datetime.utcnow(), "modifie_par": str(user_id)},
         "$setOnInsert": {"created_at": datetime.utcnow()}},
        upsert=True,
    )
    return params


def etat_canaux():
    """Ce qui est configuré sur la plateforme (fournisseur SMS, serveur email)."""
    cfg = current_app.config
    email_ok = bool(cfg.get("EMAIL_HOST") and cfg.get("MAIL_SENDER"))
    return {"sms": sms_service.etat_sms(), "email": {"configure": email_ok}}


# ---------------------------------------------------------------------------
# Situation des clients
# ---------------------------------------------------------------------------
def libelle_devise(devise):
    code = str(devise or "XAF").upper()
    return "FCFA" if code in ("XAF", "XOF") else code


def formater_montant(valeur, devise):
    decimales = decimales_devise(devise)
    texte = f"{float(valeur or 0):,.{decimales}f}".replace(",", " ")
    if decimales:
        entier, dec = texte.rsplit(".", 1)
        texte = f"{entier},{dec}"
    return f"{texte} {libelle_devise(devise)}"


def _magasin(magasin_id):
    return db.tables.magasin.find_one({"_id": ObjectId(str(magasin_id))}) or {}


def _date_locale(valeur):
    """Date enregistrée en UTC -> heure locale du magasin (APP_TIMEZONE)."""
    if not isinstance(valeur, datetime):
        return None
    if valeur.tzinfo is None:
        valeur = valeur.replace(tzinfo=timezone.utc)
    return valeur.astimezone().replace(tzinfo=None)


def _achat(vente, total, paye):
    """Résumé d'une vente non soldée : date, produits, total, payé, reste."""
    produits = {}
    for ligne in vente.get("produits") or []:
        nom = (ligne.get("nom") or "Article").strip()
        produits[nom] = produits.get(nom, 0) + int(float(ligne.get("quantite", 0) or 0))
    return {
        "date": _date_locale(vente.get("date_commande") or vente.get("created_at")),
        "produits": [(nom, qte) for nom, qte in produits.items()],
        "dette_ancienne": float(vente.get("dette_ancienne", 0) or 0),
        "total": total,
        "paye": paye,
        "reste": round(total - paye, 2),
    }


def situation_clients(magasin_id):
    """Clients du magasin avec leur dette, leur avance et leurs catégories."""
    mid = ObjectId(str(magasin_id))
    achats = {}  # client_id -> ventes non soldées
    for vente in db.tables.livraisons.find({"magasin_id": mid, "deleted_at": {"$exists": False}}):
        total = float(vente.get("montant_total", 0) or 0)
        paye = float(vente.get("montant_paye", 0) or 0)
        if total - paye > 0:
            achats.setdefault(str(vente.get("client_id")), []).append(_achat(vente, total, paye))
    # Dettes d'avant Lumistock : présentées comme un achat antérieur.
    for d in db.tables.dettes_ouverture.find({"magasin_id": mid, "deleted_at": {"$exists": False}}):
        total, paye = float(d.get("montant", 0) or 0), float(d.get("montant_paye", 0) or 0)
        if total - paye > 0:
            achats.setdefault(str(d.get("client_id")), []).append({
                "date": _date_locale(d.get("date_origine") or d.get("created_at")), "produits": [],
                "dette_ancienne": 0.0, "total": total, "paye": paye, "reste": round(total - paye, 2),
                "ouverture": True,
            })

    clients = []
    for c in db.tables.clients.find({"magasin_id": mid, "deleted_at": {"$exists": False}}):
        if (c.get("nom") or "").strip() == NOM_CLIENT_PAR_DEFAUT:
            continue  # client de passage
        cid = str(c["_id"])
        non_soldes = sorted(achats.get(cid, []), key=lambda a: a["date"] or datetime.min)
        dette = round(sum(a["reste"] for a in non_soldes), 2)
        avance = round(float(c.get("montant_en_caisse", 0) or 0), 2)
        segments = ["tous"]
        if dette > 0:
            segments.append("debiteurs")
        if avance > 0:
            segments.append("avances")
        if dette <= 0 and avance <= 0:
            segments.append("acheteurs")
        telephone = normaliser_telephone(c.get("telephone"))
        email = (c.get("email") or "").strip()
        clients.append({
            "client_id": cid,
            "nom": (c.get("nom") or "").strip(),
            "telephone": telephone if sms_service.numero_sms_valide(telephone) else None,
            "email": email if _EMAIL_VALIDE.match(email) else None,
            "dette": dette,
            "total_achats": round(sum(a["total"] for a in non_soldes), 2),
            "deja_paye": round(sum(a["paye"] for a in non_soldes), 2),
            "achats": non_soldes,
            "avance": avance,
            "segments": segments,
            "ne_pas_contacter": bool(c.get("ne_pas_contacter", False)),
        })
    return clients


def resume_segments(magasin_id):
    """Nombre de clients par catégorie et montant total dû."""
    clients = situation_clients(magasin_id)
    resume = {s: {"clients": 0, "joignables_sms": 0, "joignables_email": 0} for s in SEGMENTS}
    for c in clients:
        for s in c["segments"]:
            resume[s]["clients"] += 1
            if not c["ne_pas_contacter"]:
                resume[s]["joignables_sms"] += bool(c["telephone"])
                resume[s]["joignables_email"] += bool(c["email"])
    return {
        "segments": resume,
        "total_dette": round(sum(c["dette"] for c in clients), 2),
        "total_avances": round(sum(c["avance"] for c in clients), 2),
    }


# ---------------------------------------------------------------------------
# Rendu des messages
# ---------------------------------------------------------------------------
def _nom_affiche(nom):
    """« anse » -> « Anse » : un nom saisi tout en minuscules est mis en forme."""
    nom = " ".join((nom or "").split())
    return nom.title() if nom.islower() else nom


def _telephone_affiche(numero):
    """+237652478484 -> +237 652 47 84 84 (numéros camerounais)."""
    numero = normaliser_telephone(numero) or (numero or "")
    if re.fullmatch(r"\+237\d{9}", numero):
        n = numero[4:]
        return f"+237 {n[0:3]} {n[3:5]} {n[5:7]} {n[7:9]}"
    return numero


def _liste_produits(produits, maximum):
    textes = [f"{nom} x{qte}" for nom, qte in produits[:maximum]]
    if len(produits) > maximum:
        textes.append(f"et {len(produits) - maximum} autre{'s' if len(produits) - maximum > 1 else ''} article"
                      f"{'s' if len(produits) - maximum > 1 else ''}")
    return ", ".join(textes)


def _libelle_achat(achat):
    date = achat["date"].strftime("%d/%m/%Y") if achat["date"] else "date inconnue"
    return f"achat du {date}"


def detail_achats(client, devise):
    """Détail complet (email) : une ligne par achat non soldé."""
    lignes = []
    for achat in client.get("achats") or []:
        produits = ("solde de votre crédit antérieur" if achat.get("ouverture")
                    else _liste_produits(achat["produits"], 10) or "articles divers")
        ligne = f"- {_libelle_achat(achat).capitalize()} : {produits}"
        if achat["dette_ancienne"] > 0:
            ligne += f" (dont solde antérieur de {formater_montant(achat['dette_ancienne'], devise)})"
        ligne += (f"\n  Total : {formater_montant(achat['total'], devise)}"
                  f" | Déjà réglé : {formater_montant(achat['paye'], devise)}"
                  f" | Reste à payer : {formater_montant(achat['reste'], devise)}")
        lignes.append(ligne)
    return "\n".join(lignes)


def detail_achats_court(client):
    """Résumé (SMS), accordé au nombre d'achats :
    « votre achat du 14/10/2026 (Riz x2, Huile x1) » ;
    « vos achats du 12/10/2026 (…) et du 14/10/2026 (…), ainsi que de 3 achats antérieurs »."""
    achats = client.get("achats") or []
    if not achats:
        return "vos achats"
    recents = achats[-2:]
    parties = []
    for achat in recents:
        date = f"du {achat['date'].strftime('%d/%m/%Y')}" if achat["date"] else "à une date non précisée"
        produits = _liste_produits(achat["produits"], 3)
        parties.append(f"{date} ({produits})" if produits else date)
    texte = ("votre achat " if len(achats) == 1 else "vos achats ") + " et ".join(parties)
    autres = len(achats) - len(recents)
    if autres:
        texte += f", ainsi que de {autres} achat{'s' if autres > 1 else ''} antérieur{'s' if autres > 1 else ''}"
    return texte


def detail_paiement(client, devise):
    """Phrase sur le total, le montant déjà réglé et le reste à payer."""
    dette = formater_montant(client.get("dette", 0), devise)
    if float(client.get("deja_paye", 0) or 0) > 0:
        return (f"Sur un total de {formater_montant(client.get('total_achats', 0), devise)}, vous avez déjà "
                f"réglé {formater_montant(client['deja_paye'], devise)} ; il vous reste {dette} à payer.")
    return f"Le montant restant à payer est de {dette}."


def variables_client(client, magasin):
    devise = magasin.get("devise")
    avance = float(client.get("avance", 0) or 0)
    return {
        "nom": _nom_affiche(client.get("nom")),
        "dette": formater_montant(client.get("dette", 0), devise),
        "total_achats": formater_montant(client.get("total_achats", 0), devise),
        "deja_paye": formater_montant(client.get("deja_paye", 0), devise),
        "detail_achats": detail_achats(client, devise),
        "detail_achats_court": detail_achats_court(client),
        "detail_paiement": detail_paiement(client, devise),
        "avance": formater_montant(avance, devise),
        "mention_avance": (f" Vous disposez par ailleurs d'un solde de {formater_montant(avance, devise)} "
                           "sur votre compte.") if avance > 0 else "",
        "magasin": magasin.get("denomination") or "",
        "telephone_magasin": _telephone_affiche(magasin.get("telephone")),
        "date": datetime.now().strftime("%d/%m/%Y"),
    }


def rendre(modele, valeurs):
    """Remplace les variables connues ; le reste du texte est conservé tel quel.
    Les espaces en trop laissés par une variable vide sont retirés."""
    texte = _VARIABLE.sub(lambda m: str(valeurs.get(m.group(1), m.group(0))), modele or "")
    texte = re.sub(r"[ \t]{2,}", " ", texte)
    texte = re.sub(r" +([.,])", r"\1", texte)
    texte = re.sub(r"\.{2,}", ".", texte)
    return "\n".join(ligne.strip() for ligne in texte.split("\n")).strip()


def _derniers_contacts(magasin_id, depuis):
    """client_id -> dernier envoi réussi depuis ``depuis``."""
    derniers = {}
    for m in db.tables.messages_clients.find({"magasin_id": ObjectId(str(magasin_id))}):
        if m.get("statut") in ("envoye", "simule") and m.get("envoye_at") and m["envoye_at"] >= depuis:
            cid = str(m.get("client_id"))
            if cid not in derniers or m["envoye_at"] > derniers[cid]:
                derniers[cid] = m["envoye_at"]
    return derniers


TYPES_ENVOI = {
    "manuel": "Envoi groupé",
    "individuel": "Message individuel",
    "programme": "Envoi programmé",
    "automatique": "Relance automatique",
    "renvoi": "Renvoi",
}


def normaliser_filtres(filtres):
    """Conditions facultatives : dette minimale, ancienneté minimale de la
    dette (jours depuis l'achat non soldé le plus ancien)."""
    filtres = filtres or {}
    if not isinstance(filtres, dict):
        raise ErreurMessagerie("Filtres invalides.")
    propres = {}
    try:
        if filtres.get("dette_minimum") not in (None, ""):
            propres["dette_minimum"] = float(filtres["dette_minimum"])
        if filtres.get("anciennete_minimum_jours") not in (None, ""):
            propres["anciennete_minimum_jours"] = int(filtres["anciennete_minimum_jours"])
    except (TypeError, ValueError):
        raise ErreurMessagerie("Les conditions doivent être des nombres.")
    if propres.get("dette_minimum", 0) < 0 or not 0 <= propres.get("anciennete_minimum_jours", 0) <= 3650:
        raise ErreurMessagerie("Les conditions doivent être des nombres positifs.")
    return propres


def _passe_filtres(client, filtres):
    if not filtres:
        return True
    if client["dette"] < filtres.get("dette_minimum", 0):
        return False
    jours = filtres.get("anciennete_minimum_jours")
    if jours:
        dates = [a["date"] for a in client.get("achats") or [] if a["date"]]
        if not dates or min(dates) > datetime.now() - timedelta(days=jours):
            return False
    return True


def verifier_personnalise(modele):
    """Texte saisi pour un envoi ponctuel : mêmes règles que les modèles."""
    if not isinstance(modele, dict):
        raise ErreurMessagerie("Message personnalisé invalide.")
    propre = {}
    for champ in ("sms", "email_sujet", "email_corps"):
        if modele.get(champ) is None:
            continue
        erreur = verifier_modele(modele[champ], champ)
        if erreur:
            raise ErreurMessagerie(erreur)
        propre[champ] = modele[champ].strip()
    return propre


def destinataires(magasin_id, segment, canaux, params=None, client_ids=None, filtres=None,
                  ignorer_delai=False, personnalise=None):
    """Liste des clients visés, avec le message de chaque canal et, pour ceux
    qui ne recevront rien, la raison.

    :param ignorer_delai: envoi décidé explicitement (message individuel,
        renvoi) : le délai entre deux messages ne s'applique pas.
    :param personnalise: textes saisis pour cet envoi, à la place du modèle.
    """
    if segment not in SEGMENTS:
        raise ErreurMessagerie("Type de clients inconnu.")
    params = params or get_parametres(magasin_id)
    modele = {**params["modeles"][segment], **(personnalise or {})}
    magasin = _magasin(magasin_id)
    delai = 0 if ignorer_delai else int(params["options"].get("delai_relance_heures", 0) or 0)
    recents = _derniers_contacts(magasin_id, datetime.utcnow() - timedelta(hours=delai)) if delai else {}
    selection = {str(c) for c in client_ids} if client_ids else None

    resultat = []
    for client in situation_clients(magasin_id):
        if segment not in client["segments"] or not _passe_filtres(client, filtres):
            continue
        if selection is not None and client["client_id"] not in selection:
            continue
        valeurs = variables_client(client, magasin)
        ligne = {k: client[k] for k in ("client_id", "nom", "telephone", "email", "dette", "avance")}
        ligne["canaux"] = {}
        for canal in canaux:
            raison = None
            if client["ne_pas_contacter"]:
                raison = "Le client ne souhaite pas être contacté."
            elif client["client_id"] in recents:
                raison = f"Déjà contacté il y a moins de {delai} h."
            elif canal == "sms" and not client["telephone"]:
                raison = "Pas de numéro de téléphone valide."
            elif canal == "email" and not client["email"]:
                raison = "Pas d'adresse email valide."
            elif canal == "sms" and not modele["sms"].strip():
                raison = "Le message SMS est vide."
            elif canal == "email" and not modele["email_corps"].strip():
                raison = "Le message email est vide."
            if canal == "sms":
                texte = rendre(modele["sms"], valeurs)
                info = {"texte": texte, **sms_service.compter_sms(texte)}
            else:
                info = {"sujet": rendre(modele["email_sujet"], valeurs) or f"Message de {valeurs['magasin']}",
                        "texte": rendre(modele["email_corps"], valeurs)}
            info["joignable"] = raison is None
            info["raison"] = raison
            ligne["canaux"][canal] = info
        resultat.append(ligne)
    return sorted(resultat, key=lambda l: (-l["dette"], l["nom"].lower()))


def _verifier_canaux(params, canaux):
    canaux = [c for c in dict.fromkeys(canaux or []) if c]
    if not canaux:
        raise ErreurMessagerie("Choisissez au moins un canal (SMS ou email).")
    etat = etat_canaux()
    for canal in canaux:
        if canal not in CANAUX:
            raise ErreurMessagerie(f"Canal inconnu : {canal}.")
        if not params["canaux"].get(canal):
            raise ErreurMessagerie(f"Le canal {canal.upper()} est désactivé dans les paramètres du magasin.")
        if not etat[canal]["configure"]:
            raise ErreurMessagerie(
                "L'envoi de SMS n'est pas encore configuré sur la plateforme." if canal == "sms"
                else "L'envoi d'emails n'est pas encore configuré sur la plateforme.")
    return canaux


def apercu(magasin_id, segment, canaux, filtres=None, ignorer_delai=False, client_ids=None, personnalise=None):
    params = get_parametres(magasin_id)
    canaux = [c for c in (canaux or []) if c in CANAUX] or ["sms"]
    lignes = destinataires(magasin_id, segment, canaux, params, client_ids, normaliser_filtres(filtres),
                           ignorer_delai, verifier_personnalise(personnalise) if personnalise else None)
    return {
        "segment": segment,
        "modele_actif": params["modeles"][segment]["actif"],
        "destinataires": lignes,
        "totaux": {canal: {
            "joignables": sum(1 for l in lignes if l["canaux"][canal]["joignable"]),
            "sms_factures": sum(l["canaux"][canal].get("sms", 0) for l in lignes
                                if l["canaux"][canal]["joignable"]) if canal == "sms" else None,
        } for canal in canaux},
        "quota": quota_sms(magasin_id),
    }


# ---------------------------------------------------------------------------
# Quota mensuel de SMS
# ---------------------------------------------------------------------------
def sms_du_mois(magasin_id):
    debut = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = 0
    for m in db.tables.messages_clients.find({"magasin_id": ObjectId(str(magasin_id))}):
        if m.get("canal") == "sms" and m.get("statut") in ("envoye", "simule") \
                and m.get("envoye_at") and m["envoye_at"] >= debut:
            total += int(m.get("parts", 1) or 1)
    return total


def quota_sms(magasin_id):
    limite = int(current_app.config.get("SMS_QUOTA_MENSUEL", 0) or 0)
    utilises = sms_du_mois(magasin_id)
    return {"limite": limite or None, "utilises": utilises,
            "restants": max(limite - utilises, 0) if limite else None}


# ---------------------------------------------------------------------------
# Campagnes
# ---------------------------------------------------------------------------
def creer_campagne(magasin_id, user_id, segment, canaux, client_ids=None, filtres=None, ignorer_delai=False,
                   personnalise=None, type_envoi="manuel", origine=None):
    """Prépare une campagne et la liste de ses messages ; l'envoi est lancé
    ensuite par ``lancer_campagne``.

    Les destinataires et les montants sont calculés maintenant, à partir des
    ventes et des comptes clients du jour.

    :param type_envoi: manuel, individuel, programme, automatique ou renvoi
    :param origine: {"regle_id"|"programme_id"|"campagne_id": ...} pour l'historique
    """
    params = get_parametres(magasin_id)
    if segment not in SEGMENTS:
        raise ErreurMessagerie("Type de clients inconnu.")
    if type_envoi not in TYPES_ENVOI:
        raise ErreurMessagerie("Type d'envoi inconnu.")
    personnalise = verifier_personnalise(personnalise) if personnalise else None
    if not personnalise and not params["modeles"][segment]["actif"]:
        raise ErreurMessagerie(
            f"Le message « {SEGMENTS[segment]['libelle']} » n'est pas activé dans les paramètres du magasin.")
    canaux = _verifier_canaux(params, canaux)
    filtres = normaliser_filtres(filtres)
    lignes = destinataires(magasin_id, segment, canaux, params, client_ids, filtres, ignorer_delai, personnalise)
    a_envoyer = [(l, c) for l in lignes for c in canaux if l["canaux"][c]["joignable"]]
    if not a_envoyer:
        raisons = sorted({l["canaux"][c]["raison"] for l in lignes for c in canaux if l["canaux"][c]["raison"]})
        raise ErreurMessagerie(erreur_aucun_destinataire(segment, lignes, client_ids, raisons))

    quota = quota_sms(magasin_id)
    sms_prevus = sum(l["canaux"]["sms"]["sms"] for l, c in a_envoyer if c == "sms")
    if quota["limite"] and sms_prevus > quota["restants"]:
        raise ErreurMessagerie(
            f"Quota de SMS du mois insuffisant : {sms_prevus} SMS nécessaires, {quota['restants']} restants.")

    maintenant = datetime.utcnow()
    mid = ObjectId(str(magasin_id))
    campagne_id = _table("campagnes_messages").insert_one({
        "magasin_id": mid, "cree_par": str(user_id) if user_id else None, "segment": segment,
        "canaux_envoi": canaux, "type_envoi": type_envoi, "origine": origine or {}, "filtres": filtres,
        "personnalise": bool(personnalise),
        "statut": "en_cours", "total": len(a_envoyer), "envoyes": 0, "echecs": 0,
        "ignores": sum(1 for l in lignes for c in canaux if not l["canaux"][c]["joignable"]),
        "created_at": maintenant, "updated_at": maintenant,
    }).inserted_id
    for ligne, canal in a_envoyer:
        info = ligne["canaux"][canal]
        doc = {
            "campagne_id": campagne_id, "magasin_id": mid, "client_id": ObjectId(ligne["client_id"]),
            "canal": canal, "segment": segment, "client_nom": ligne["nom"],
            "destinataire": ligne["telephone"] if canal == "sms" else ligne["email"],
            "contenu": info["texte"], "statut": "en_attente", "created_at": maintenant,
        }
        if canal == "sms":
            doc["parts"] = info["sms"]
        else:
            doc["sujet"] = info["sujet"]
        _table("messages_clients").insert_one(doc)
    return str(campagne_id)


def erreur_aucun_destinataire(segment, lignes, client_ids, raisons):
    """Message clair quand personne ne recevra le message."""
    if lignes:
        if client_ids and len(client_ids) == 1 and raisons:
            return "Ce client ne peut pas recevoir le message : " + " ".join(raisons)
        return "Aucun client joignable pour ce message."
    if client_ids and len(client_ids) == 1:
        precision = {"debiteurs": " (il n'a pas de dette en cours)",
                     "avances": " (il n'a pas de solde versé)",
                     "acheteurs": " (il a une dette ou un solde versé)"}.get(segment, "")
        return f"Ce client ne fait pas partie des « {SEGMENTS[segment]['libelle']} »{precision}."
    return "Aucun client ne correspond à ce message."


def clients_d_une_campagne(magasin_id, campagne_id):
    """Clients d'un envoi passé (pour le renvoyer)."""
    c = _table("campagnes_messages").find_one({"_id": ObjectId(str(campagne_id)),
                                              "magasin_id": ObjectId(str(magasin_id))}) \
        if ObjectId.is_valid(str(campagne_id)) else None
    if not c:
        raise ErreurMessagerie("Campagne introuvable.")
    ids = sorted({str(m["client_id"]) for m in _table("messages_clients").find({"campagne_id": c["_id"]})})
    return c, ids


def _envoyer_email(magasin, destinataire, sujet, texte):
    from utils.email import _envoyer
    msg = MIMEMultipart("alternative")
    msg["Subject"] = sujet
    msg["From"] = current_app.config["MAIL_SENDER"]
    msg["To"] = destinataire
    if magasin.get("email"):
        msg["Reply-To"] = magasin["email"]  # les réponses vont au magasin
    corps_html = html.escape(texte).replace("\n", "<br>")
    msg.attach(MIMEText(texte, "plain", "utf-8"))
    msg.attach(MIMEText(
        f'<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.5;color:#0F172A">{corps_html}</div>',
        "html", "utf-8"))
    ok = _envoyer(msg, destinataire)
    return ok, None, None if ok else "Le serveur email a refusé l'envoi."


def executer_campagne(campagne_id):
    """Envoie les messages en attente d'une campagne (contexte d'application requis)."""
    campagne = _table("campagnes_messages").find_one({"_id": ObjectId(str(campagne_id))})
    if not campagne or campagne.get("statut") != "en_cours":
        return
    magasin = _magasin(campagne["magasin_id"])
    attente = 0.25 if sms_service.fournisseur() == "orange" else 0  # 5 SMS/s chez Orange
    simulation = sms_service.etat_sms()["simulation"]
    # Une campagne reprise repart des compteurs déjà enregistrés.
    envoyes, echecs = int(campagne.get("envoyes", 0) or 0), int(campagne.get("echecs", 0) or 0)
    messages = [m for m in _table("messages_clients").find({"campagne_id": campagne["_id"]})
                if m.get("statut") == "en_attente"]
    for i, m in enumerate(messages):
        if m["canal"] == "sms":
            ok, reference, erreur = sms_service.envoyer_sms(m["destinataire"], m["contenu"])
            if attente:
                time.sleep(attente)
        else:
            ok, reference, erreur = _envoyer_email(magasin, m["destinataire"], m.get("sujet", ""), m["contenu"])
        statut = ("simule" if simulation and m["canal"] == "sms" else "envoye") if ok else "echec"
        _table("messages_clients").update_one({"_id": m["_id"]}, {"$set": {
            "statut": statut, "reference": reference, "erreur": erreur,
            "envoye_at": datetime.utcnow() if ok else None, "updated_at": datetime.utcnow(),
        }})
        envoyes += ok
        echecs += not ok
        if i % 10 == 9 or i == len(messages) - 1:  # progression visible dans l'historique
            _table("campagnes_messages").update_one({"_id": campagne["_id"]}, {"$set": {
                "envoyes": envoyes, "echecs": echecs, "updated_at": datetime.utcnow()}})
    _table("campagnes_messages").update_one({"_id": campagne["_id"]}, {"$set": {
        "statut": "terminee", "envoyes": envoyes, "echecs": echecs,
        "termine_at": datetime.utcnow(), "updated_at": datetime.utcnow()}})


def lancer_campagne(campagne_id):
    """Exécute la campagne en arrière-plan (ou tout de suite si
    MESSAGERIE_SYNCHRONE, utilisé par les tests).

    Pendant une requête HTTP, la campagne n'est enregistrée (commit) qu'à la
    fin de la requête : l'envoi démarre donc une fois la réponse terminée,
    sinon le fil d'envoi ne trouverait pas encore la campagne en base.
    """
    from flask import after_this_request, has_request_context

    app = current_app._get_current_object()
    if app.config.get("MESSAGERIE_SYNCHRONE"):
        executer_campagne(campagne_id)
        return

    def travail():
        with app.app_context():
            try:
                executer_campagne(campagne_id)
            except Exception:
                app.logger.exception("Campagne de messages %s interrompue", campagne_id)

    def demarrer():
        threading.Thread(target=travail, name=f"campagne-{campagne_id}", daemon=True).start()

    if has_request_context():
        @after_this_request
        def apres(response):
            response.call_on_close(demarrer)
            return response
    else:
        demarrer()


def reprendre_campagne(magasin_id, campagne_id):
    """Relance les messages encore en attente d'une campagne interrompue
    (redémarrage du serveur pendant l'envoi)."""
    if not ObjectId.is_valid(str(campagne_id)):
        raise ErreurMessagerie("Campagne introuvable.")
    c = _table("campagnes_messages").find_one({"_id": ObjectId(str(campagne_id)),
                                              "magasin_id": ObjectId(str(magasin_id))})
    if not c:
        raise ErreurMessagerie("Campagne introuvable.")
    if _statut_affiche(c) != "interrompue":
        raise ErreurMessagerie("Seule une campagne interrompue peut être reprise.")
    _table("campagnes_messages").update_one({"_id": c["_id"]}, {"$set": {"updated_at": datetime.utcnow()}})
    return str(c["_id"])


def _statut_affiche(campagne):
    if campagne.get("statut") == "en_cours" and campagne.get("updated_at") \
            and datetime.utcnow() - campagne["updated_at"] > CAMPAGNE_INTERROMPUE_APRES:
        return "interrompue"
    return campagne.get("statut")


def _campagne_publique(c):
    return {
        "_id": str(c["_id"]), "segment": c.get("segment"),
        "segment_libelle": SEGMENTS.get(c.get("segment"), {}).get("libelle", c.get("segment")),
        "canaux": c.get("canaux_envoi") or [], "statut": _statut_affiche(c),
        "total": c.get("total", 0), "envoyes": c.get("envoyes", 0), "echecs": c.get("echecs", 0),
        "ignores": c.get("ignores", 0), "cree_par": c.get("cree_par"),
        "type_envoi": c.get("type_envoi", "manuel"),
        "type_libelle": TYPES_ENVOI.get(c.get("type_envoi", "manuel"), "Envoi groupé"),
        "origine": {k: str(v) for k, v in (c.get("origine") or {}).items()},
        "created_at": c.get("created_at"), "termine_at": c.get("termine_at"),
    }


def liste_campagnes(magasin_id, limite=100):
    campagnes = list(_table("campagnes_messages").find({"magasin_id": ObjectId(str(magasin_id))})
                     .sort("created_at", -1).limit(limite))
    auteurs = {}
    for c in campagnes:
        uid = c.get("cree_par")
        if uid and uid not in auteurs and ObjectId.is_valid(uid):
            u = db.tables.users.find_one({"_id": ObjectId(uid)}) or {}
            auteurs[uid] = f"{u.get('first_name', '')} {u.get('name', '')}".strip()
    resultat = []
    for c in campagnes:
        pub = _campagne_publique(c)
        pub["auteur"] = auteurs.get(c.get("cree_par"), "")
        resultat.append(pub)
    return resultat


def detail_campagne(magasin_id, campagne_id):
    if not ObjectId.is_valid(str(campagne_id)):
        return None
    c = _table("campagnes_messages").find_one({"_id": ObjectId(str(campagne_id)),
                                              "magasin_id": ObjectId(str(magasin_id))})
    if not c:
        return None
    messages = sorted(_table("messages_clients").find({"campagne_id": c["_id"]}),
                      key=lambda m: (m.get("client_nom") or "").lower())
    pub = _campagne_publique(c)
    pub["messages"] = [{
        "client_id": str(m.get("client_id")), "client_nom": m.get("client_nom", ""), "canal": m.get("canal"),
        "destinataire": m.get("destinataire"), "contenu": m.get("contenu"), "sujet": m.get("sujet"),
        "statut": m.get("statut"), "erreur": m.get("erreur"), "envoye_at": m.get("envoye_at"),
    } for m in messages]
    return pub


def historique_client(magasin_id, client_id):
    """Messages reçus par un client (fiche client)."""
    messages = [m for m in _table("messages_clients").find({"client_id": ObjectId(str(client_id))})
                if str(m.get("magasin_id")) == str(magasin_id)]
    messages.sort(key=lambda m: m.get("created_at") or datetime.min, reverse=True)
    return [{"canal": m.get("canal"), "segment": m.get("segment"), "contenu": m.get("contenu"),
             "statut": m.get("statut"), "envoye_at": m.get("envoye_at"), "created_at": m.get("created_at")}
            for m in messages]


# ---------------------------------------------------------------------------
# Test et préférences
# ---------------------------------------------------------------------------
def envoyer_test(magasin_id, segment, canal, destinataire):
    """Envoie le modèle à l'administrateur, avec des valeurs d'exemple."""
    params = get_parametres(magasin_id)
    if segment not in SEGMENTS:
        raise ErreurMessagerie("Type de message inconnu.")
    _verifier_canaux(params, [canal])
    magasin = _magasin(magasin_id)
    exemple = variables_client({
        "nom": "Client Exemple", "dette": 30000, "total_achats": 50000, "deja_paye": 20000, "avance": 0,
        "achats": [{"date": datetime.now(), "produits": [("Riz 25 kg", 2), ("Huile 5 L", 1)],
                    "dette_ancienne": 0, "total": 50000, "paye": 20000, "reste": 30000}],
    }, magasin)
    modele = params["modeles"][segment]
    if canal == "sms":
        numero = normaliser_telephone(destinataire)
        if not sms_service.numero_sms_valide(numero):
            raise ErreurMessagerie("Numéro de téléphone invalide (ex. +237690000000).")
        ok, _, erreur = sms_service.envoyer_sms(numero, "[TEST] " + rendre(modele["sms"], exemple))
    else:
        if not _EMAIL_VALIDE.match(destinataire or ""):
            raise ErreurMessagerie("Adresse email invalide.")
        ok, _, erreur = _envoyer_email(magasin, destinataire, "[TEST] " + rendre(modele["email_sujet"], exemple),
                                       rendre(modele["email_corps"], exemple))
    if not ok:
        raise ErreurMessagerie(erreur or "L'envoi du test a échoué.")
    return "Message de test envoyé" + (" (simulation : aucun SMS réel n'est parti)."
                                       if canal == "sms" and sms_service.etat_sms()["simulation"] else ".")


def definir_preference(magasin_id, client_id, ne_pas_contacter):
    """Le client ne veut plus (ou de nouveau) recevoir de messages."""
    if not ObjectId.is_valid(str(client_id)):
        raise ErreurMessagerie("Client introuvable.")
    result = db.tables.clients.update_one(
        {"_id": ObjectId(str(client_id)), "magasin_id": ObjectId(str(magasin_id))},
        {"$set": {"ne_pas_contacter": bool(ne_pas_contacter), "updated_at": datetime.utcnow()}})
    if not result.matched_count:
        raise ErreurMessagerie("Client introuvable.")
