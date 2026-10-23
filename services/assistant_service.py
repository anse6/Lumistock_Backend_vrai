"""Nansé, l'assistante du site public de Lumistock (chat des visiteurs).

Déroulement d'une conversation
------------------------------
1. Le visiteur commence à écrire : le chat lui demande son nom et son email,
   puis la conversation est créée (``demarrer``). Il reçoit un jeton secret
   (gardé dans son navigateur) qui seul permet de lire et d'écrire dans SA
   conversation ; seule son empreinte SHA-256 est enregistrée.
2. Chaque question est envoyée à l'IA avec les fiches de la base de
   connaissances les plus proches (services.assistant_connaissances) : Nansé
   ne répond qu'à partir de ces faits. L'IA indique aussi si la question est
   « pertinente » (une vraie question sur Lumistock ou sur le commerce, pas
   une salutation) et si le visiteur demande un humain.
3. À la 3e question pertinente (ou si le visiteur demande un humain), les
   comptes staff (super-admin) reçoivent un email pour aller répondre, et la
   conversation passe « en attente » : Nansé continue de répondre en attendant.
4. Dès qu'un membre du staff répond, la conversation passe « avec un
   conseiller » : Nansé se tait, et le visiteur voit le nom du conseiller sur
   chaque réponse. Le staff peut suivre plusieurs conversations à la fois,
   rendre la main à Nansé ou fermer la conversation.

Statuts : ``ia`` → ``attente`` → ``conseiller`` ; ``fermee`` à tout moment.
"""
import hashlib
import hmac
import json
import logging
import re
import secrets
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

from bson import ObjectId
from flask import current_app

from extensions import db
from services.assistant_connaissances import chercher
from services.ia_contexte import IAErreur, IANonConfiguree, appeler_ia

journal = logging.getLogger(__name__)

NOM_ASSISTANT = "Nansé"
SEUIL_QUESTIONS = 3  # questions pertinentes avant de prévenir le staff
RAPPEL_STAFF = timedelta(minutes=30)  # délai minimal entre deux emails au staff pour une conversation
HISTORIQUE_IA = 12  # derniers messages transmis à l'IA
TEXTE_MAX = 2000
STATUTS = {"ia": "Nansé répond", "attente": "Conseiller prévenu", "conseiller": "Avec un conseiller", "fermee": "Fermée"}
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

AVIS_CONSEILLER = {
    "fr": "J'ai prévenu un conseiller Lumistock : il va vous répondre ici même. En attendant, je reste à votre disposition.",
    "en": "I've let a Lumistock advisor know: they'll answer you right here. Meanwhile, I'm still here to help.",
}
AVIS_ARRIVEE = {
    "fr": "{nom} a rejoint la conversation.",
    "en": "{nom} joined the conversation.",
}
AVIS_RETOUR_IA = {
    "fr": "Le conseiller a rendu la main à Nansé, qui reprend la conversation.",
    "en": "The advisor handed the conversation back to Nansé.",
}
AVIS_FERMEE = {
    "fr": "Cette conversation est terminée. Merci d'avoir contacté Lumistock ! Écrivez de nouveau pour en commencer une autre.",
    "en": "This conversation has ended. Thank you for contacting Lumistock! Write again to start a new one.",
}
SECOURS = {
    "fr": "Je n'arrive pas à formuler une réponse pour le moment. Voici ce que j'ai trouvé à ce sujet :\n\n{fiche}\n\n"
          "Un conseiller Lumistock peut aussi vous répondre ici.",
    "en": "I can't put together an answer right now. Here is what I found on this topic (in French):\n\n{fiche}\n\n"
          "A Lumistock advisor can also answer you here.",
}

CONSIGNES = """Tu es Nansé, l'assistante du site web de Lumistock, un logiciel de gestion commerciale (ventes, caisse, stock, crédit client, comptabilité SYSCOHADA) pour les commerces d'Afrique centrale.
Tu parles à un visiteur du site, {nom}, qui découvre Lumistock ou l'utilise déjà.

Règles :
- Réponds UNIQUEMENT à partir des FICHES ci-dessous. Si la réponse n'y est pas, dis-le simplement et propose qu'un conseiller Lumistock réponde dans ce chat ou via la page Contact.
- N'invente jamais de prix, de délai, de fonction, de chiffre ni de client. Pour un tarif, explique que le prix dépend de la licence (nombre de comptes) et propose un conseiller.
- Réponds dans la langue du visiteur ({langue_nom}), en 2 à 6 phrases, ton chaleureux et professionnel. Markdown léger autorisé (listes courtes, **gras**). Pas de titres.
- Vouvoie toujours le visiteur, même s'il te tutoie (en anglais : « you »). Appelle-le par son prénom de temps en temps, sans excès.
- Ne demande jamais de mot de passe ni de données bancaires.
- Si la question n'a rien à voir avec Lumistock ou la gestion d'un commerce, réponds brièvement et ramène poliment la conversation vers Lumistock.

Réponds en JSON strict, sans texte autour :
{{"reponse": "<ta réponse>", "pertinente": <true si le message est une vraie question ou demande sur Lumistock, ses fonctions, ses tarifs, l'inscription ou la gestion d'un commerce ; false pour une salutation, un remerciement, un message vide de sens ou hors sujet>, "demande_conseiller": <true seulement si le visiteur demande explicitement à parler à un humain, un conseiller, un commercial>}}

FICHES :
{fiches}"""


class ErreurAssistant(ValueError):
    def __init__(self, message, status=400, champ=None):
        super().__init__(message)
        self.status = status
        self.champ = champ


def _conversations():
    return db.tables.assistant_conversations


def _messages():
    return db.tables.assistant_messages


def _empreinte(jeton):
    return hashlib.sha256(str(jeton or "").encode("utf-8")).hexdigest()


def _iso(d):
    return d.isoformat() + "Z" if isinstance(d, datetime) else d


def _langue(valeur):
    return "en" if str(valeur or "").lower().startswith("en") else "fr"


def _nom_staff(staff):
    return f"{staff.get('first_name') or ''} {staff.get('name') or ''}".strip() or staff.get("email") or "Conseiller"


# --------------------------------------------------------------------------- messages
def _ajouter(conv, auteur, texte, auteur_nom=None, staff_id=None, pertinente=None, sources=None):
    """Enregistre un message et met à jour le résumé de la conversation."""
    maintenant = datetime.utcnow()
    doc = {
        "conversation_id": conv["_id"], "auteur": auteur, "auteur_nom": auteur_nom, "texte": texte,
        "staff_id": staff_id, "pertinente": pertinente, "sources": sources, "created_at": maintenant,
    }
    doc["_id"] = _messages().insert_one(doc).inserted_id
    modifs = {"dernier_message": texte[:160], "updated_at": maintenant}
    increments = {}
    if auteur == "visiteur":
        increments["non_lus_staff"] = 1
    elif auteur in ("ia", "conseiller"):
        increments["non_lus_visiteur"] = 1
    mise_a_jour = {"$set": modifs}
    if increments:
        mise_a_jour["$inc"] = increments
    _conversations().update_one({"_id": conv["_id"]}, mise_a_jour)
    return doc


def serialiser_message(m):
    return {
        "id": str(m["_id"]),
        "auteur": m.get("auteur"),
        "auteur_nom": m.get("auteur_nom"),
        "texte": m.get("texte") or "",
        "pertinente": m.get("pertinente"),
        "created_at": _iso(m.get("created_at")),
    }


def _liste_messages(conv_id, depuis=None):
    messages = list(_messages().find({"conversation_id": conv_id}))
    if depuis:
        messages = [m for m in messages if m.get("created_at") and m["created_at"] > depuis]
    messages.sort(key=lambda m: (m.get("created_at") or datetime.min, str(m["_id"])))
    return messages


def _lire_depuis(valeur):
    if not valeur:
        return None
    try:
        return datetime.fromisoformat(str(valeur).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


# --------------------------------------------------------------------------- visiteur
def serialiser_conversation(conv, messages=None, staff=False):
    donnees = {
        "id": str(conv["_id"]),
        "nom": conv.get("nom"),
        "statut": conv.get("statut") or "ia",
        "statut_libelle": STATUTS.get(conv.get("statut") or "ia"),
        "agent_nom": conv.get("agent_nom"),
        "langue": conv.get("langue") or "fr",
        "assistant": NOM_ASSISTANT,
    }
    if staff:
        donnees.update({
            "email": conv.get("email"),
            "questions_pertinentes": int(conv.get("questions_pertinentes") or 0),
            "non_lus": int(conv.get("non_lus_staff") or 0),
            "dernier_message": conv.get("dernier_message"),
            "page": conv.get("page"),
            "notifie_le": _iso(conv.get("notifie_le")),
            "derniere_activite": _iso(conv.get("derniere_activite")),
            "created_at": _iso(conv.get("created_at")),
            "updated_at": _iso(conv.get("updated_at") or conv.get("created_at")),
            "en_ligne": bool(conv.get("derniere_activite")
                             and datetime.utcnow() - conv["derniere_activite"] < timedelta(seconds=45)),
        })
    if messages is not None:
        donnees["messages"] = [serialiser_message(m) for m in messages]
    return donnees


def demarrer(data, ip):
    """Crée la conversation d'un visiteur identifié. Renvoie (conversation, jeton)."""
    data = data or {}
    nom = re.sub(r"\s+", " ", str(data.get("nom") or "")).strip()
    if len(nom) < 2:
        raise ErreurAssistant("Indiquez votre nom.", champ="nom")
    if len(nom) > 80:
        raise ErreurAssistant("Votre nom est trop long (80 caractères maximum).", champ="nom")
    email = str(data.get("email") or "").strip().lower()
    if not EMAIL.match(email) or len(email) > 150:
        raise ErreurAssistant("Adresse email invalide.", champ="email")
    jeton = secrets.token_urlsafe(32)
    maintenant = datetime.utcnow()
    conv = {
        "nom": nom, "email": email, "langue": _langue(data.get("langue")), "statut": "ia",
        "jeton": _empreinte(jeton), "questions_pertinentes": 0, "non_lus_staff": 0, "non_lus_visiteur": 0,
        "ip": ip, "page": str(data.get("page") or "")[:200] or None,
        "derniere_activite": maintenant, "created_at": maintenant, "updated_at": maintenant,
    }
    conv["_id"] = _conversations().insert_one(conv).inserted_id
    return conv, jeton


def acceder(conversation_id, jeton):
    """Conversation du visiteur, si le jeton est le bon. :raises ErreurAssistant."""
    if not ObjectId.is_valid(str(conversation_id)) or not jeton:
        raise ErreurAssistant("Conversation introuvable.", 404)
    conv = _conversations().find_one({"_id": ObjectId(str(conversation_id))})
    if not conv or not hmac.compare_digest(conv.get("jeton") or "", _empreinte(jeton)):
        raise ErreurAssistant("Conversation introuvable.", 404)
    return conv


def lire_visiteur(conv, depuis=None):
    """Messages du visiteur (tous, ou ceux après `depuis`) ; marque sa présence."""
    maintenant = datetime.utcnow()
    _conversations().update_one({"_id": conv["_id"]}, {"$set": {
        "derniere_activite": maintenant, "non_lus_visiteur": 0, "vu_par_visiteur_le": maintenant}})
    return serialiser_conversation(conv, _liste_messages(conv["_id"], _lire_depuis(depuis)))


def _fiches_texte(fiches):
    return "\n\n".join(f"[{f['titre']}]\n{f['texte']}" for f in fiches)


def _demander_ia(conv, question):
    """Réponse de Nansé : (texte, pertinente, demande_conseiller, ids des fiches)."""
    fiches = chercher(question)
    langue = conv.get("langue") or "fr"
    prenom = (conv.get("nom") or "").split(" ")[0]
    historique = _liste_messages(conv["_id"])[-HISTORIQUE_IA:]
    messages = [{"role": "system", "content": CONSIGNES.format(
        nom=prenom, langue_nom="anglais" if langue == "en" else "français", fiches=_fiches_texte(fiches))}]
    for m in historique[:-1]:  # le dernier est la question, ajoutée ci-dessous
        if m.get("auteur") == "visiteur":
            messages.append({"role": "user", "content": m["texte"]})
        elif m.get("auteur") in ("ia", "conseiller"):
            messages.append({"role": "assistant", "content": m["texte"]})
    messages.append({"role": "user", "content": question})
    sources = [f["id"] for f in fiches]
    try:
        brut = appeler_ia(messages, temperature=0.3, max_tokens=600)
    except (IANonConfiguree, IAErreur):
        return SECOURS[langue].format(fiche=fiches[0]["texte"]), _pertinence_simple(question), False, sources
    reponse, pertinente, humain = _lire_json(brut, question)
    return reponse, pertinente, humain, sources


def _lire_json(brut, question):
    """Décode la réponse JSON de l'IA ; si elle n'en est pas, le texte sert de réponse."""
    texte = brut.strip()
    debut, fin = texte.find("{"), texte.rfind("}")
    if debut != -1 and fin > debut:
        try:
            data = json.loads(texte[debut:fin + 1])
            reponse = str(data.get("reponse") or "").strip()
            if reponse:
                return reponse, bool(data.get("pertinente")), bool(data.get("demande_conseiller"))
        except (ValueError, AttributeError):
            pass
    return texte, _pertinence_simple(question), False


_SALUTATIONS = re.compile(r"^(bonjour|bonsoir|salut|hello|hi|hey|merci|thanks|thank you|ok|d'accord|oui|non|cc|coucou)[\s!.?]*$", re.I)


def _pertinence_simple(question):
    """Sans IA : une question pertinente a au moins 4 mots et n'est pas une simple formule de politesse."""
    q = question.strip()
    return not _SALUTATIONS.match(q) and len(q.split()) >= 4


def envoyer_visiteur(conv, texte):
    """Message du visiteur, puis réponse de Nansé si aucun conseiller n'a repris la main.

    Renvoie la conversation à jour et les nouveaux messages.
    """
    texte = str(texte or "").strip()
    if not texte:
        raise ErreurAssistant("Écrivez votre message.")
    if len(texte) > TEXTE_MAX:
        raise ErreurAssistant(f"Votre message est trop long ({TEXTE_MAX} caractères maximum).")
    if conv.get("statut") == "fermee":
        raise ErreurAssistant("Cette conversation est terminée.", 409)

    nouveaux = [_ajouter(conv, "visiteur", texte, auteur_nom=conv.get("nom"))]
    _conversations().update_one({"_id": conv["_id"]}, {"$set": {"derniere_activite": datetime.utcnow()}})

    if conv.get("statut") == "conseiller":
        # Un conseiller a repris la main : Nansé se tait ; rappel par email si personne n'a répondu depuis longtemps.
        _rappeler_staff(conv, texte)
        return _recharger(conv), nouveaux

    reponse, pertinente, humain, sources = _demander_ia(conv, texte)
    langue = conv.get("langue") or "fr"
    _messages().update_one({"_id": nouveaux[0]["_id"]}, {"$set": {"pertinente": pertinente}})
    nouveaux[0]["pertinente"] = pertinente
    nouveaux.append(_ajouter(conv, "ia", reponse, auteur_nom=NOM_ASSISTANT, sources=sources))

    # L'IA peut mettre quelques secondes : on relit la conversation, un conseiller a pu reprendre la main entre-temps.
    conv = _recharger(conv)
    nombre = int(conv.get("questions_pertinentes") or 0) + (1 if pertinente else 0)
    if pertinente:
        _conversations().update_one({"_id": conv["_id"]}, {"$inc": {"questions_pertinentes": 1}})
    deja_prevenu = bool(conv.get("notifie_le"))
    if conv.get("statut") == "ia" and ((nombre >= SEUIL_QUESTIONS and not deja_prevenu) or humain):
        _conversations().update_one({"_id": conv["_id"]}, {"$set": {"statut": "attente"}})
        nouveaux.append(_ajouter(conv, "systeme", AVIS_CONSEILLER[langue]))
        _prevenir_staff(_recharger(conv))
    return _recharger(conv), nouveaux


def _recharger(conv):
    return _conversations().find_one({"_id": conv["_id"]})


# --------------------------------------------------------------------------- emails au staff
def _emails_staff():
    return [u["email"] for u in db.tables.users.find({"role": "staff"}) if u.get("is_active") and u.get("email")]


def _email_staff(conv, questions, destinataire, rappel=False):
    from utils.email import lien_frontend

    lien = lien_frontend(f"/superadmin/dashboard/assistant?c={conv['_id']}")
    nom = conv.get("nom") or "Un visiteur"
    sujet = (f"Nouveau message de {nom} dans le chat Lumistock" if rappel
             else f"{nom} attend un conseiller dans le chat Lumistock")
    intro = (f"{nom} ({conv.get('email')}) a écrit de nouveau dans le chat :" if rappel
             else f"{nom} ({conv.get('email')}) a posé plusieurs questions à {NOM_ASSISTANT} et attend un conseiller :")
    liste_txt = "\n".join(f"- {q}" for q in questions)
    liste_html = "".join(f'<li style="margin:4px 0">{escape(q)}</li>' for q in questions)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = sujet
    msg["From"] = current_app.config.get("MAIL_SENDER")
    msg["To"] = destinataire
    plain = f"Bonjour,\n\n{intro}\n{liste_txt}\n\nRépondre : {lien}\n\n{NOM_ASSISTANT}, l'assistante Lumistock\n"
    html = (
        '<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.6;color:#0f172a;max-width:600px">'
        f"<p>Bonjour,</p><p>{escape(intro)}</p>"
        f'<ul style="padding-left:20px;color:#334155">{liste_html}</ul>'
        f'<p style="margin:28px 0"><a href="{escape(lien)}" style="background:#3b0764;color:#ffffff;'
        'padding:12px 22px;border-radius:8px;text-decoration:none;font-weight:bold">Répondre dans le chat</a></p>'
        f'<p style="color:#64748b;font-size:13px">{NOM_ASSISTANT}, l\'assistante Lumistock</p></div>'
    )
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    return msg


def _prevenir_staff(conv, rappel=False, questions=None):
    destinataires = _emails_staff()
    if questions is None:
        questions = [m["texte"] for m in _liste_messages(conv["_id"])
                     if m.get("auteur") == "visiteur" and m.get("pertinente")][-5:]
    _conversations().update_one({"_id": conv["_id"]}, {"$set": {"notifie_le": datetime.utcnow()}})
    if not destinataires:
        journal.warning("Aucun compte staff actif à prévenir pour la conversation %s.", conv["_id"])
        return
    from utils.email import envoyer_en_arriere_plan

    envoyer_en_arriere_plan(current_app._get_current_object(),
                            [(_email_staff(conv, questions, d, rappel), d) for d in destinataires])


def _rappeler_staff(conv, texte):
    notifie = conv.get("notifie_le")
    if notifie and datetime.utcnow() - notifie < RAPPEL_STAFF:
        return
    _prevenir_staff(conv, rappel=True, questions=[texte])


# --------------------------------------------------------------------------- staff
def obtenir(conversation_id):
    if not ObjectId.is_valid(str(conversation_id)):
        raise ErreurAssistant("Conversation introuvable.", 404)
    conv = _conversations().find_one({"_id": ObjectId(str(conversation_id))})
    if not conv:
        raise ErreurAssistant("Conversation introuvable.", 404)
    return conv


def lister(statut=None, recherche=None):
    tous = list(_conversations().find({}))
    convs = [c for c in tous if not statut or (c.get("statut") or "ia") == statut]
    motif = (recherche or "").strip().lower()
    if motif:
        convs = [c for c in convs if motif in f"{c.get('nom', '')} {c.get('email', '')} {c.get('dernier_message', '')}".lower()]
    # Les conversations qui attendent un conseiller d'abord, puis les plus récentes.
    convs.sort(key=lambda c: (c.get("updated_at") or c.get("created_at") or datetime.min), reverse=True)
    convs.sort(key=lambda c: 0 if (c.get("statut") == "attente" or int(c.get("non_lus_staff") or 0)) else 1)
    compteurs = {s: sum(1 for c in tous if (c.get("statut") or "ia") == s) for s in STATUTS}
    return {
        "conversations": [serialiser_conversation(c, staff=True) for c in convs],
        "compteurs": {**compteurs, "tous": len(tous), "non_lus": non_lus(tous)},
        "statuts": STATUTS,
        "seuil": SEUIL_QUESTIONS,
    }


def non_lus(conversations=None):
    """Conversations à traiter : en attente d'un conseiller ou avec des messages non lus."""
    convs = conversations if conversations is not None else _conversations().find({})
    return sum(1 for c in convs if c.get("statut") != "fermee"
               and (c.get("statut") == "attente" or int(c.get("non_lus_staff") or 0) > 0))


def ouvrir(conversation_id, depuis=None):
    """Détail d'une conversation pour le staff ; l'ouvrir marque ses messages comme lus."""
    conv = obtenir(conversation_id)
    if int(conv.get("non_lus_staff") or 0):
        _conversations().update_one({"_id": conv["_id"]}, {"$set": {"non_lus_staff": 0}})
        conv["non_lus_staff"] = 0
    return serialiser_conversation(conv, _liste_messages(conv["_id"], _lire_depuis(depuis)), staff=True)


def repondre(conversation_id, texte, staff):
    """Réponse d'un membre du staff : il reprend la main, son nom s'affiche chez le visiteur."""
    conv = obtenir(conversation_id)
    texte = str(texte or "").strip()
    if not texte:
        raise ErreurAssistant("Écrivez votre réponse.")
    if len(texte) > TEXTE_MAX:
        raise ErreurAssistant(f"La réponse est limitée à {TEXTE_MAX} caractères.")
    if conv.get("statut") == "fermee":
        raise ErreurAssistant("Cette conversation est fermée.", 409)
    nom = _nom_staff(staff)
    langue = conv.get("langue") or "fr"
    if conv.get("statut") != "conseiller" or conv.get("agent_id") != str(staff["_id"]):
        _ajouter(conv, "systeme", AVIS_ARRIVEE[langue].format(nom=nom))
    _conversations().update_one({"_id": conv["_id"]}, {"$set": {
        "statut": "conseiller", "agent_id": str(staff["_id"]), "agent_nom": nom, "non_lus_staff": 0}})
    _ajouter(conv, "conseiller", texte, auteur_nom=nom, staff_id=str(staff["_id"]))
    return ouvrir(conv["_id"])


def changer_statut(conversation_id, statut):
    """Le staff rend la main à Nansé (``ia``) ou ferme la conversation (``fermee``)."""
    conv = obtenir(conversation_id)
    if statut not in ("ia", "fermee"):
        raise ErreurAssistant("Statut inconnu.")
    if conv.get("statut") == statut:
        return ouvrir(conv["_id"])
    langue = conv.get("langue") or "fr"
    _ajouter(conv, "systeme", (AVIS_RETOUR_IA if statut == "ia" else AVIS_FERMEE)[langue])
    modifs = {"statut": statut}
    if statut == "ia":
        modifs.update({"agent_id": None, "agent_nom": None})
    _conversations().update_one({"_id": conv["_id"]}, {"$set": modifs})
    return ouvrir(conv["_id"])
