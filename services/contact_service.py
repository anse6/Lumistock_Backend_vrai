"""Formulaire de contact du site public, traité par le staff (super-admin).

- Un visiteur envoie un message (POST /contact) : il est enregistré « nouveau ».
- Le staff le lit (il devient « lu »), change son statut, ajoute une note
  interne et répond par email depuis la plateforme ; chaque réponse est gardée
  dans l'historique du message.
"""
import re
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

from bson import ObjectId
from flask import current_app

from extensions import db

SUJETS = {
    "demonstration": "Découvrir Lumistock / démonstration",
    "tarifs": "Tarifs et licence",
    "inscription": "Inscription d'un magasin",
    "support": "Aide sur l'application",
    "partenariat": "Partenariat",
    "autre": "Autre",
}
STATUTS = {"nouveau": "Nouveau", "en_cours": "En cours", "repondu": "Répondu", "clos": "Clos"}
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TELEPHONE = re.compile(r"^\+?[\d\s.\-()]{6,22}$")


class ErreurContact(ValueError):
    def __init__(self, message, status=400, champ=None):
        super().__init__(message)
        self.status = status
        self.champ = champ


def _table():
    return db.tables.messages_contact


def _texte(data, cle, maximum, obligatoire=False, minimum=0, libelle=""):
    valeur = re.sub(r"[ \t]+", " ", str(data.get(cle) or "")).strip()
    if obligatoire and not valeur:
        raise ErreurContact(f"{libelle} : ce champ est requis.", champ=cle)
    if valeur and len(valeur) < minimum:
        raise ErreurContact(f"{libelle} : {minimum} caractères minimum.", champ=cle)
    if len(valeur) > maximum:
        raise ErreurContact(f"{libelle} : {maximum} caractères maximum.", champ=cle)
    return valeur


def creer(data, ip):
    """Enregistre un message du formulaire public. :raises ErreurContact."""
    data = data or {}
    if data.get("consentement") is not True:
        raise ErreurContact("Merci d'accepter que nous utilisions ces informations pour vous répondre.", champ="consentement")
    nom = _texte(data, "nom", 120, True, 2, "Nom")
    email = _texte(data, "email", 150, True, 0, "Email").lower()
    if not EMAIL.match(email):
        raise ErreurContact("Adresse email invalide.", champ="email")
    telephone = _texte(data, "telephone", 22, False, 0, "Téléphone")
    if telephone and not TELEPHONE.match(telephone):
        raise ErreurContact("Numéro de téléphone invalide.", champ="telephone")
    entreprise = _texte(data, "entreprise", 150, False, 0, "Entreprise")
    sujet = str(data.get("sujet") or "")
    if sujet not in SUJETS:
        raise ErreurContact("Choisissez le sujet de votre message.", champ="sujet")
    message = str(data.get("message") or "").strip()
    if len(message) < 10:
        raise ErreurContact("Votre message est trop court (10 caractères minimum).", champ="message")
    if len(message) > 5000:
        raise ErreurContact("Votre message est trop long (5 000 caractères maximum).", champ="message")

    doc = {
        "nom": nom, "email": email, "telephone": telephone or None, "entreprise": entreprise or None,
        "sujet": sujet, "message": message, "statut": "nouveau", "lu": False, "note": None,
        "reponses": [], "ip": ip, "created_at": datetime.utcnow(),
    }
    doc["_id"] = _table().insert_one(doc).inserted_id
    return doc


# --------------------------------------------------------------------------- staff
def _iso(d):
    return d.isoformat() + "Z" if isinstance(d, datetime) else d


def serialiser(doc, complet=False):
    donnees = {
        "id": str(doc["_id"]),
        "nom": doc.get("nom"),
        "email": doc.get("email"),
        "telephone": doc.get("telephone"),
        "entreprise": doc.get("entreprise"),
        "sujet": doc.get("sujet"),
        "sujet_libelle": SUJETS.get(doc.get("sujet"), doc.get("sujet")),
        "statut": doc.get("statut") or "nouveau",
        "lu": bool(doc.get("lu")),
        "extrait": (doc.get("message") or "")[:160],
        "nb_reponses": len(doc.get("reponses") or []),
        "created_at": _iso(doc.get("created_at")),
    }
    if complet:
        donnees.update({
            "message": doc.get("message"),
            "note": doc.get("note"),
            "reponses": [{**r, "le": _iso(r.get("le"))} for r in (doc.get("reponses") or [])],
            "traite_le": _iso(doc.get("traite_le")),
        })
    return donnees


def lister(statut=None, recherche=None):
    query = {}
    if statut in STATUTS:
        query["statut"] = statut
    motif = re.escape((recherche or "").strip())
    if motif:
        query["$or"] = [{c: {"$regex": motif, "$options": "i"}} for c in ("nom", "email", "entreprise", "message")]
    messages = list(_table().find(query))
    messages.sort(key=lambda m: m.get("created_at") or datetime.min, reverse=True)
    tous = list(_table().find({}))
    compteurs = {s: sum(1 for m in tous if (m.get("statut") or "nouveau") == s) for s in STATUTS}
    return {
        "messages": [serialiser(m) for m in messages],
        "compteurs": {**compteurs, "tous": len(tous), "non_lus": sum(1 for m in tous if not m.get("lu"))},
        "statuts": STATUTS,
        "sujets": SUJETS,
    }


def non_lus():
    return sum(1 for m in _table().find({}) if not m.get("lu"))


def obtenir(message_id):
    if not ObjectId.is_valid(str(message_id)):
        raise ErreurContact("Message introuvable.", 404)
    doc = _table().find_one({"_id": ObjectId(str(message_id))})
    if not doc:
        raise ErreurContact("Message introuvable.", 404)
    return doc


def ouvrir(message_id):
    """Détail d'un message ; l'ouvrir le marque comme lu."""
    doc = obtenir(message_id)
    if not doc.get("lu"):
        _table().update_one({"_id": doc["_id"]}, {"$set": {"lu": True}})
        doc["lu"] = True
    return serialiser(doc, complet=True)


def modifier(message_id, data, staff_id):
    doc = obtenir(message_id)
    modifs = {}
    if "statut" in data:
        if data["statut"] not in STATUTS:
            raise ErreurContact("Statut inconnu.")
        modifs["statut"] = data["statut"]
        if data["statut"] in ("repondu", "clos"):
            modifs.update({"traite_par": str(staff_id), "traite_le": datetime.utcnow()})
    if "note" in data:
        note = str(data.get("note") or "").strip()
        if len(note) > 2000:
            raise ErreurContact("La note interne est limitée à 2 000 caractères.")
        modifs["note"] = note or None
    if "lu" in data:
        modifs["lu"] = bool(data["lu"])
    if not modifs:
        raise ErreurContact("Aucune modification.")
    _table().update_one({"_id": doc["_id"]}, {"$set": modifs})
    return serialiser({**doc, **modifs}, complet=True)


def supprimer(message_id):
    doc = obtenir(message_id)
    _table().delete_one({"_id": doc["_id"]})


def _email_reponse(doc, texte, signature):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Re : votre message à Lumistock — {SUJETS.get(doc.get('sujet'), 'Contact')}"
    msg["From"] = current_app.config.get("MAIL_SENDER")
    msg["To"] = doc["email"]
    citation = "\n".join(f"> {ligne}" for ligne in (doc.get("message") or "").splitlines())
    plain = f"Bonjour {doc.get('nom')},\n\n{texte}\n\n{signature}\nL'équipe Lumistock\n\n---\nVotre message :\n{citation}\n"
    html = (
        '<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.6;color:#0f172a;max-width:600px">'
        f"<p>Bonjour {escape(doc.get('nom') or '')},</p>"
        f"<p>{escape(texte).replace(chr(10), '<br>')}</p>"
        f"<p>{escape(signature)}<br>L'équipe Lumistock</p>"
        '<hr style="border:none;border-top:1px solid #e2e8f0;margin:24px 0">'
        '<p style="color:#64748b;font-size:13px">Votre message :</p>'
        f'<blockquote style="margin:0;padding-left:12px;border-left:3px solid #3b1558;color:#64748b;font-size:13px">'
        f"{escape(doc.get('message') or '').replace(chr(10), '<br>')}</blockquote></div>"
    )
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    return msg


def repondre(message_id, texte, staff):
    """Envoie une réponse par email au visiteur et l'ajoute à l'historique."""
    from utils.email import _envoyer

    doc = obtenir(message_id)
    texte = str(texte or "").strip()
    if len(texte) < 2:
        raise ErreurContact("Écrivez votre réponse.")
    if len(texte) > 5000:
        raise ErreurContact("La réponse est limitée à 5 000 caractères.")
    signature = f"{staff.get('first_name') or ''} {staff.get('name') or ''}".strip()
    envoye = _envoyer(_email_reponse(doc, texte, signature), doc["email"])
    if not envoye:
        raise ErreurContact("L'email n'a pas pu être envoyé (configuration de la messagerie du serveur). "
                            "Réessayez ou répondez depuis votre boîte mail.", 502)
    reponse = {"texte": texte, "par": signature or staff.get("email"), "le": datetime.utcnow()}
    reponses = list(doc.get("reponses") or []) + [reponse]
    modifs = {"reponses": reponses, "statut": "repondu", "lu": True,
              "traite_par": str(staff["_id"]), "traite_le": datetime.utcnow()}
    _table().update_one({"_id": doc["_id"]}, {"$set": modifs})
    return serialiser({**doc, **modifs}, complet=True)
