"""Support interne : conversations entre l'administrateur d'un magasin et le
staff Lumistock (super-admin).

- Les deux côtés peuvent ouvrir une conversation : l'administrateur écrit à
  « l'équipe Lumistock » (tout le staff la voit), le staff choisit
  l'administrateur à qui il écrit.
- Chaque conversation a un sujet, un statut (ouverte / fermee) et un compteur
  de messages non lus de chaque côté. Écrire dans une conversation fermée la
  rouvre.
- Le destinataire reçoit un email s'il n'a pas regardé la conversation depuis
  quelques minutes, une seule fois par série de messages non vus (pas d'email
  à chaque message).

Contrôle d'accès : un administrateur ne voit que SES conversations ; le staff
les voit toutes.
"""
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

from bson import ObjectId
from flask import current_app

from extensions import db

STATUTS = {"ouverte": "Ouverte", "fermee": "Fermée"}
TEXTE_MAX = 4000
SUJET_MAX = 150
ABSENCE_AVANT_EMAIL = timedelta(minutes=2)  # le destinataire n'a pas regardé la conversation depuis…
NOM_EQUIPE = "Équipe Lumistock"


class ErreurSupport(ValueError):
    def __init__(self, message, status=400, champ=None):
        super().__init__(message)
        self.status = status
        self.champ = champ


def _conversations():
    return db.tables.support_conversations


def _messages():
    return db.tables.support_messages


def _iso(d):
    return d.isoformat() + "Z" if isinstance(d, datetime) else d


def _nom(user):
    return f"{user.get('first_name') or ''} {user.get('name') or ''}".strip() or user.get("email") or ""


def _cote(user):
    return "staff" if user.get("role") == "staff" else "admin"


def _autre(cote):
    return "admin" if cote == "staff" else "staff"


def _lire_depuis(valeur):
    if not valeur:
        return None
    try:
        return datetime.fromisoformat(str(valeur).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _texte(valeur, maximum, libelle, champ="texte"):
    texte = str(valeur or "").strip()
    if not texte:
        raise ErreurSupport(f"{libelle} : ce champ est requis.", champ=champ)
    if len(texte) > maximum:
        raise ErreurSupport(f"{libelle} : {maximum} caractères maximum.", champ=champ)
    return texte


# --------------------------------------------------------------------------- lecture
def serialiser_message(m):
    return {
        "id": str(m["_id"]),
        "auteur": m.get("auteur"),
        "auteur_nom": m.get("auteur_nom"),
        "texte": m.get("texte") or "",
        "created_at": _iso(m.get("created_at")),
    }


def _email_admin(admin_id):
    """E-mail de l'administrateur de la conversation (None si introuvable).
    Celui du personnel Lumistock n'est jamais envoyé aux magasins."""
    if not admin_id or not ObjectId.is_valid(str(admin_id)):
        return None
    admin = db.tables.users.find_one({"_id": ObjectId(str(admin_id))})
    return (admin or {}).get("email") or None


def serialiser(conv, cote, messages=None):
    donnees = {
        "id": str(conv["_id"]),
        "sujet": conv.get("sujet"),
        "statut": conv.get("statut") or "ouverte",
        "initie_par": conv.get("initie_par"),
        "admin_id": conv.get("admin_id"),
        "admin_nom": conv.get("admin_nom"),
        # Pour la fiche au survol de l'avatar (comme la liste des utilisateurs).
        "admin_email": _email_admin(conv.get("admin_id")),
        "magasin_id": str(conv.get("magasin_id")) if conv.get("magasin_id") else None,
        "magasin_nom": conv.get("magasin_nom"),
        "staff_nom": conv.get("staff_nom"),
        "dernier_message": conv.get("dernier_message"),
        # Non lus et dernière visite de l'autre côté, vus depuis celui qui regarde.
        "non_lus": int(conv.get(f"non_lus_{cote}") or 0),
        "vu_par_autre_le": _iso(conv.get(f"vu_{_autre(cote)}_le")),
        "created_at": _iso(conv.get("created_at")),
        "updated_at": _iso(conv.get("updated_at") or conv.get("created_at")),
    }
    if messages is not None:
        donnees["messages"] = [serialiser_message(m) for m in messages]
    return donnees


def _liste_messages(conv_id, depuis=None):
    messages = list(_messages().find({"conversation_id": conv_id}))
    if depuis:
        messages = [m for m in messages if m.get("created_at") and m["created_at"] > depuis]
    messages.sort(key=lambda m: (m.get("created_at") or datetime.min, str(m["_id"])))
    return messages


def _filtre(user):
    return {} if _cote(user) == "staff" else {"admin_id": str(user["_id"])}


def obtenir(conversation_id, user):
    """Conversation, si l'utilisateur y a accès. :raises ErreurSupport."""
    if not ObjectId.is_valid(str(conversation_id)):
        raise ErreurSupport("Conversation introuvable.", 404)
    conv = _conversations().find_one({"_id": ObjectId(str(conversation_id))})
    if not conv or (_cote(user) == "admin" and conv.get("admin_id") != str(user["_id"])):
        raise ErreurSupport("Conversation introuvable.", 404)
    return conv


def lister(user, statut=None, recherche=None):
    cote = _cote(user)
    tous = list(_conversations().find(_filtre(user)))
    convs = [c for c in tous if not statut or (c.get("statut") or "ouverte") == statut]
    motif = (recherche or "").strip().lower()
    if motif:
        convs = [c for c in convs if motif in " ".join(
            str(c.get(k) or "") for k in ("sujet", "admin_nom", "magasin_nom", "dernier_message", "staff_nom")).lower()]
    convs.sort(key=lambda c: c.get("updated_at") or c.get("created_at") or datetime.min, reverse=True)
    return {
        "conversations": [serialiser(c, cote) for c in convs],
        "compteurs": {
            **{s: sum(1 for c in tous if (c.get("statut") or "ouverte") == s) for s in STATUTS},
            "tous": len(tous),
            "non_lus": sum(1 for c in tous if int(c.get(f"non_lus_{cote}") or 0) > 0),
        },
        "statuts": STATUTS,
    }


def non_lus(user):
    cote = _cote(user)
    return sum(1 for c in _conversations().find(_filtre(user)) if int(c.get(f"non_lus_{cote}") or 0) > 0)


def ouvrir(conversation_id, user, depuis=None):
    """Détail ; l'ouvrir marque les messages comme lus et note la visite."""
    conv = obtenir(conversation_id, user)
    cote = _cote(user)
    modifs = {f"vu_{cote}_le": datetime.utcnow(), f"non_lus_{cote}": 0}
    _conversations().update_one({"_id": conv["_id"]}, {"$set": modifs})
    conv.update(modifs)
    return serialiser(conv, cote, _liste_messages(conv["_id"], _lire_depuis(depuis)))


# --------------------------------------------------------------------------- écriture
def _ajouter(conv, auteur, texte, auteur_id=None, auteur_nom=None):
    maintenant = datetime.utcnow()
    doc = {
        "conversation_id": conv["_id"], "magasin_id": conv.get("magasin_id"), "auteur": auteur,
        "auteur_id": auteur_id, "auteur_nom": auteur_nom, "texte": texte, "created_at": maintenant,
    }
    doc["_id"] = _messages().insert_one(doc).inserted_id
    maj = {"$set": {"dernier_message": texte[:160], "updated_at": maintenant}}
    if auteur in ("admin", "staff"):
        maj["$inc"] = {f"non_lus_{_autre(auteur)}": 1}
    _conversations().update_one({"_id": conv["_id"]}, maj)
    return doc


def admins(recherche=None):
    """Administrateurs de magasin à qui le staff peut écrire."""
    magasins = {str(m["_id"]): m for m in db.tables.magasin.find({})}
    resultat = []
    for u in db.tables.users.find({"role": "admin"}):
        magasin = magasins.get(str(u.get("magasin_id") or ""))
        resultat.append({
            "id": str(u["_id"]), "nom": _nom(u), "email": u.get("email"),
            "magasin_id": str(u.get("magasin_id")) if u.get("magasin_id") else None,
            "magasin_nom": (magasin or {}).get("denomination"),
            "actif": bool(u.get("is_active")) and bool((magasin or {}).get("is_active", False)),
        })
    motif = (recherche or "").strip().lower()
    if motif:
        resultat = [a for a in resultat if motif in f"{a['nom']} {a['email']} {a['magasin_nom'] or ''}".lower()]
    resultat.sort(key=lambda a: (not a["actif"], (a["magasin_nom"] or "").lower(), a["nom"].lower()))
    return resultat


def creer(user, data):
    """Nouvelle conversation, ouverte par un administrateur ou par le staff."""
    data = data or {}
    cote = _cote(user)
    sujet = _texte(data.get("sujet"), SUJET_MAX, "Sujet", "sujet")
    texte = _texte(data.get("texte"), TEXTE_MAX, "Message")
    if cote == "staff":
        admin_id = str(data.get("admin_id") or "")
        admin = db.tables.users.find_one({"_id": ObjectId(admin_id)}) if ObjectId.is_valid(admin_id) else None
        if not admin or admin.get("role") != "admin":
            raise ErreurSupport("Choisissez l'administrateur à qui écrire.", champ="admin_id")
    else:
        admin = user
    magasin_id = admin.get("magasin_id")
    magasin = db.tables.magasin.find_one({"_id": ObjectId(str(magasin_id))}) if magasin_id else None
    maintenant = datetime.utcnow()
    conv = {
        "magasin_id": ObjectId(str(magasin_id)) if magasin_id else None,
        "admin_id": str(admin["_id"]), "admin_nom": _nom(admin),
        "magasin_nom": (magasin or {}).get("denomination"),
        "sujet": sujet, "statut": "ouverte", "initie_par": cote,
        "staff_id": str(user["_id"]) if cote == "staff" else None,
        "staff_nom": _nom(user) if cote == "staff" else None,
        "non_lus_admin": 0, "non_lus_staff": 0, f"vu_{cote}_le": maintenant,
        "created_at": maintenant, "updated_at": maintenant,
    }
    conv["_id"] = _conversations().insert_one(conv).inserted_id
    _ajouter(conv, cote, texte, str(user["_id"]), _nom(user))
    _prevenir(_conversations().find_one({"_id": conv["_id"]}), cote, texte, nouvelle=True)
    return ouvrir(conv["_id"], user)


def ecrire(conversation_id, user, texte):
    conv = obtenir(conversation_id, user)
    cote = _cote(user)
    texte = _texte(texte, TEXTE_MAX, "Message")
    if conv.get("statut") == "fermee":
        _ajouter(conv, "systeme", f"Conversation rouverte par {_nom(user)}.")
        _conversations().update_one({"_id": conv["_id"]}, {"$set": {"statut": "ouverte"}})
    if cote == "staff":
        _conversations().update_one({"_id": conv["_id"]}, {"$set": {"staff_id": str(user["_id"]), "staff_nom": _nom(user)}})
    _ajouter(conv, cote, texte, str(user["_id"]), _nom(user))
    _prevenir(_conversations().find_one({"_id": conv["_id"]}), cote, texte)
    return ouvrir(conv["_id"], user)


def changer_statut(conversation_id, user, statut):
    conv = obtenir(conversation_id, user)
    if statut not in STATUTS:
        raise ErreurSupport("Statut inconnu.")
    if (conv.get("statut") or "ouverte") != statut:
        verbe = "fermée" if statut == "fermee" else "rouverte"
        _ajouter(conv, "systeme", f"Conversation {verbe} par {_nom(user)}.")
        _conversations().update_one({"_id": conv["_id"]}, {"$set": {"statut": statut}})
    return ouvrir(conv["_id"], user)


# --------------------------------------------------------------------------- emails
def _doit_prevenir(conv, destinataire):
    """Un email par série de messages non vus, et seulement si le destinataire n'est pas devant la conversation."""
    maintenant = datetime.utcnow()
    vu = conv.get(f"vu_{destinataire}_le")
    notifie = conv.get(f"notifie_{destinataire}_le")
    if vu and maintenant - vu < ABSENCE_AVANT_EMAIL:
        return False
    return not notifie or (vu is not None and notifie < vu)


def _email(conv, auteur_nom, texte, destinataire_email, pour, nouvelle):
    from utils.email import lien_frontend

    chemin = "/superadmin/dashboard/support" if pour == "staff" else "/admin/dashboard/support"
    lien = lien_frontend(f"{chemin}?c={conv['_id']}")
    origine = f"{auteur_nom} ({conv.get('magasin_nom') or 'magasin'})" if pour == "staff" else f"{auteur_nom}, {NOM_EQUIPE}"
    titre = "Nouvelle conversation" if nouvelle else "Nouveau message"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{titre} : {conv.get('sujet')} — Lumistock"
    msg["From"] = current_app.config.get("MAIL_SENDER")
    msg["To"] = destinataire_email
    extrait = texte if len(texte) <= 600 else texte[:600] + "…"
    plain = f"Bonjour,\n\n{origine} vous a écrit sur Lumistock (« {conv.get('sujet')} ») :\n\n{extrait}\n\nRépondre : {lien}\n"
    html = (
        '<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.6;color:#0f172a;max-width:600px">'
        f"<p>Bonjour,</p><p><strong>{escape(origine)}</strong> vous a écrit sur Lumistock "
        f"(« {escape(conv.get('sujet') or '')} ») :</p>"
        f'<blockquote style="margin:0;padding:12px 16px;border-left:3px solid #3b0764;background:#faf5ff;color:#334155">'
        f"{escape(extrait).replace(chr(10), '<br>')}</blockquote>"
        f'<p style="margin:28px 0"><a href="{escape(lien)}" style="background:#3b0764;color:#ffffff;padding:12px 22px;'
        'border-radius:8px;text-decoration:none;font-weight:bold">Répondre</a></p></div>'
    )
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    return msg


def _prevenir(conv, auteur_cote, texte, nouvelle=False):
    from utils.email import envoyer_en_arriere_plan

    pour = _autre(auteur_cote)
    if not _doit_prevenir(conv, pour):
        return
    if pour == "staff":
        emails = [u["email"] for u in db.tables.users.find({"role": "staff"}) if u.get("is_active") and u.get("email")]
        auteur_nom = conv.get("admin_nom")
    else:
        admin = db.tables.users.find_one({"_id": ObjectId(conv["admin_id"])}) if ObjectId.is_valid(str(conv.get("admin_id"))) else None
        emails = [admin["email"]] if admin and admin.get("is_active") and admin.get("email") else []
        auteur_nom = conv.get("staff_nom") or NOM_EQUIPE
    _conversations().update_one({"_id": conv["_id"]}, {"$set": {f"notifie_{pour}_le": datetime.utcnow()}})
    if emails:
        envoyer_en_arriere_plan(current_app._get_current_object(),
                                [(_email(conv, auteur_nom, texte, e, pour, nouvelle), e) for e in emails])

