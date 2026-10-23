# services/messagerie_planification.py
"""Envois programmés et relances automatiques de la messagerie clients.

- **Envoi programmé** : un message (pour un client ou une catégorie de
  clients) à envoyer à une date et une heure choisies. Les destinataires et
  les montants sont calculés au moment de l'envoi, pas à la programmation :
  un client qui a payé entre-temps ne reçoit pas de rappel.
- **Relance automatique** : une règle récurrente (chaque jour, certains jours
  de la semaine ou du mois, à une heure donnée), par exemple « chaque lundi à
  9 h, rappeler leur dette aux clients qui doivent plus de 10 000 FCFA depuis
  plus de 7 jours ». Le délai entre deux messages à un même client s'applique.

Les envois programmés et automatiques ne partent que dans la plage horaire du
magasin (8 h - 20 h par défaut) : hors plage, ils attendent son ouverture.

Le planificateur tourne dans un fil d'exécution du serveur et vérifie toutes
les 30 secondes ce qui est dû. Chaque envoi est « réservé » en base avant
d'être exécuté : avec plusieurs processus serveur, il ne part qu'une fois.
"""
import re
import threading
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from flask import current_app

from extensions import db
from services import messagerie_service as svc
from services.messagerie_service import ErreurMessagerie

FREQUENCES = {"quotidienne": "Chaque jour", "hebdomadaire": "Chaque semaine", "mensuelle": "Chaque mois"}
JOURS_SEMAINE = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
INTERVALLE_PLANIFICATEUR = 30  # secondes
CONSERVATION_HISTORIQUE = timedelta(days=180)
MAX_REGLES_PAR_MAGASIN = 20
MAX_PROGRAMMES_PAR_MAGASIN = 200


# ---------------------------------------------------------------------------
# Heures : stockage en UTC, affichage et règles en heure locale du magasin
# ---------------------------------------------------------------------------
def heure_locale():
    return datetime.now().replace(microsecond=0)


def vers_utc(local):
    """Heure locale (naïve) -> UTC naïve, comme en base."""
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def vers_local(utc):
    if utc is None:
        return None
    return utc.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)


def _iso_local(utc):
    local = vers_local(utc)
    return local.isoformat() if local else None


def dans_plage(local, options):
    return int(options["heure_debut_envoi"]) <= local.hour < int(options["heure_fin_envoi"])


def lire_date(valeur):
    """Date choisie dans l'interface (ISO, avec ou sans fuseau) -> heure locale naïve."""
    if not isinstance(valeur, str) or not valeur.strip():
        raise ErreurMessagerie("Choisissez la date et l'heure de l'envoi.")
    texte = valeur.strip().replace("Z", "+00:00")
    try:
        date = datetime.fromisoformat(texte)
    except ValueError:
        raise ErreurMessagerie("Date d'envoi invalide.")
    if date.tzinfo is not None:
        date = date.astimezone().replace(tzinfo=None)
    return date.replace(second=0, microsecond=0)


def _verifier_date_envoi(local, options):
    maintenant = heure_locale()
    if local < maintenant + timedelta(minutes=1):
        raise ErreurMessagerie("La date d'envoi doit être dans le futur.")
    if local > maintenant + timedelta(days=366):
        raise ErreurMessagerie("La date d'envoi ne peut pas dépasser un an.")
    if not dans_plage(local, options):
        raise ErreurMessagerie(
            f"Choisissez une heure entre {options['heure_debut_envoi']} h et {options['heure_fin_envoi']} h "
            "(plage d'envoi du magasin, modifiable dans les paramètres).")


def _clients_du_magasin(magasin_id, client_ids):
    """Vérifie que les clients visés appartiennent au magasin."""
    if client_ids is None:
        return None
    if not isinstance(client_ids, list) or not client_ids:
        raise ErreurMessagerie("Sélectionnez au moins un client.")
    ids = []
    for cid in dict.fromkeys(str(c) for c in client_ids):
        client = db.tables.clients.find_one({"_id": ObjectId(cid)}) if ObjectId.is_valid(cid) else None
        if not client or str(client.get("magasin_id")) != str(magasin_id) or "deleted_at" in client:
            raise ErreurMessagerie("Client introuvable dans votre magasin.")
        ids.append(cid)
    return ids


def _preparer_envoi(magasin_id, segment, canaux, filtres, personnalise):
    """Contrôles communs à un envoi programmé et à une relance automatique."""
    params = svc.get_parametres(magasin_id)
    if segment not in svc.SEGMENTS:
        raise ErreurMessagerie("Type de clients inconnu.")
    personnalise = svc.verifier_personnalise(personnalise) if personnalise else None
    if not personnalise and not params["modeles"][segment]["actif"]:
        raise ErreurMessagerie(
            f"Le message « {svc.SEGMENTS[segment]['libelle']} » n'est pas activé dans les paramètres du magasin.")
    canaux = svc._verifier_canaux(params, canaux)
    return params, canaux, svc.normaliser_filtres(filtres), personnalise


# ---------------------------------------------------------------------------
# Envois programmés
# ---------------------------------------------------------------------------
def programmer_envoi(magasin_id, user_id, segment, canaux, date_envoi, client_ids=None, filtres=None,
                     ignorer_delai=False, personnalise=None):
    """Programme un message à une date ; retourne l'identifiant de l'envoi."""
    params, canaux, filtres, personnalise = _preparer_envoi(magasin_id, segment, canaux, filtres, personnalise)
    local = lire_date(date_envoi)
    _verifier_date_envoi(local, params["options"])
    client_ids = _clients_du_magasin(magasin_id, client_ids)
    if client_ids:
        # Vérification immédiate (la situation sera recalculée à l'envoi) : on
        # ne programme pas, par exemple, un rappel de dette à un client à jour.
        lignes = svc.destinataires(magasin_id, segment, canaux, params, client_ids, filtres, True, personnalise)
        if not lignes:
            raise ErreurMessagerie(svc.erreur_aucun_destinataire(segment, lignes, client_ids, []))
    mid = ObjectId(str(magasin_id))
    en_attente = sum(1 for p in db.tables.envois_programmes.find({"magasin_id": mid})
                     if p.get("statut") == "programme")
    if en_attente >= MAX_PROGRAMMES_PAR_MAGASIN:
        raise ErreurMessagerie(f"Vous avez déjà {MAX_PROGRAMMES_PAR_MAGASIN} envois programmés en attente.")
    maintenant = datetime.utcnow()
    return str(db.tables.envois_programmes.insert_one({
        "magasin_id": mid, "cree_par": str(user_id), "segment": segment, "canaux_envoi": canaux,
        "client_ids": client_ids, "date_prevue": vers_utc(local), "statut": "programme",
        "type_envoi": "individuel" if client_ids and len(client_ids) == 1 else "programme",
        "filtres": filtres, "ignorer_delai": bool(ignorer_delai), "personnalise": personnalise,
        "created_at": maintenant, "updated_at": maintenant,
    }).inserted_id)


def _noms_clients(ids):
    noms = []
    for cid in ids or []:
        client = db.tables.clients.find_one({"_id": ObjectId(cid)}) if ObjectId.is_valid(cid) else None
        noms.append((client or {}).get("nom", "Client supprimé"))
    return noms


def liste_programmes(magasin_id):
    """Envois à venir (par date), puis les 50 derniers exécutés, échoués ou annulés."""
    tous = list(db.tables.envois_programmes.find({"magasin_id": ObjectId(str(magasin_id))}))
    a_venir = sorted((p for p in tous if p.get("statut") == "programme"), key=lambda p: p["date_prevue"])
    passes = sorted((p for p in tous if p.get("statut") != "programme"),
                    key=lambda p: p.get("updated_at") or datetime.min, reverse=True)[:50]
    resultat = []
    for p in a_venir + passes:
        resultat.append({
            "_id": str(p["_id"]), "segment": p.get("segment"),
            "segment_libelle": svc.SEGMENTS.get(p.get("segment"), {}).get("libelle", ""),
            "canaux": p.get("canaux_envoi") or [], "date_prevue": _iso_local(p.get("date_prevue")),
            "statut": p.get("statut"), "type_envoi": p.get("type_envoi"),
            "clients": _noms_clients(p.get("client_ids")) if p.get("client_ids") else None,
            "filtres": p.get("filtres") or {}, "personnalise": bool(p.get("personnalise")),
            "campagne_id": str(p["campagne_id"]) if p.get("campagne_id") else None,
            "erreur": p.get("erreur"),
        })
    return resultat


def annuler_programme(magasin_id, programme_id):
    if not ObjectId.is_valid(str(programme_id)):
        raise ErreurMessagerie("Envoi programmé introuvable.")
    result = db.tables.envois_programmes.update_one(
        {"_id": ObjectId(str(programme_id)), "magasin_id": ObjectId(str(magasin_id)), "statut": "programme"},
        {"$set": {"statut": "annule", "updated_at": datetime.utcnow()}})
    if not result.modified_count:
        raise ErreurMessagerie("Cet envoi n'est plus programmé (déjà parti ou annulé).")


def _executer_programme(p):
    """Réserve puis exécute un envoi programmé dû."""
    reserve = db.tables.envois_programmes.update_one(
        {"_id": p["_id"], "statut": "programme"},
        {"$set": {"statut": "en_cours", "updated_at": datetime.utcnow()}})
    if not reserve.modified_count:
        return  # déjà pris par un autre processus
    changes = {"updated_at": datetime.utcnow()}
    try:
        campagne_id = svc.creer_campagne(
            p["magasin_id"], p.get("cree_par"), p["segment"], p.get("canaux_envoi"), p.get("client_ids"),
            p.get("filtres"), p.get("ignorer_delai", False), p.get("personnalise"),
            type_envoi=p.get("type_envoi", "programme"), origine={"programme_id": str(p["_id"])})
        changes.update(statut="envoye", campagne_id=ObjectId(campagne_id))
    except ErreurMessagerie as e:
        changes.update(statut="echec", erreur=str(e))
        campagne_id = None
    db.tables.envois_programmes.update_one({"_id": p["_id"]}, {"$set": changes})
    if campagne_id:
        svc.lancer_campagne(campagne_id)


def executer_programmes_dus(maintenant=None):
    maintenant = maintenant or datetime.utcnow()
    for p in list(db.tables.envois_programmes.find({})):
        if p.get("statut") != "programme" or p["date_prevue"] > maintenant:
            continue
        params = svc.get_parametres(p["magasin_id"])
        if not dans_plage(vers_local(maintenant), params["options"]):
            continue  # en retard (serveur arrêté) et hors plage : attend l'ouverture
        try:
            _executer_programme(p)
        except Exception:
            current_app.logger.exception("Envoi programmé %s en erreur", p["_id"])


# ---------------------------------------------------------------------------
# Relances automatiques
# ---------------------------------------------------------------------------
def _valider_regle(magasin_id, data, existante=None):
    if not isinstance(data, dict):
        raise ErreurMessagerie("Données invalides.")
    regle = dict(existante or {})
    for cle in ("nom", "segment", "canaux", "frequence", "jours", "heure", "filtres", "actif"):
        if cle in data:
            regle[cle] = data[cle]
    nom = str(regle.get("nom") or "").strip()
    if not nom or len(nom) > 80:
        raise ErreurMessagerie("Donnez un nom à la relance (80 caractères maximum).")
    params, canaux, filtres, _ = _preparer_envoi(magasin_id, regle.get("segment"),
                                                 regle.get("canaux") or regle.get("canaux_envoi"),
                                                 regle.get("filtres"), None)
    frequence = regle.get("frequence")
    if frequence not in FREQUENCES:
        raise ErreurMessagerie("Fréquence inconnue (chaque jour, semaine ou mois).")
    try:
        jours = sorted({int(j) for j in (regle.get("jours") or [])})
    except (TypeError, ValueError):
        raise ErreurMessagerie("Jours invalides.")
    if frequence == "hebdomadaire" and (not jours or not all(0 <= j <= 6 for j in jours)):
        raise ErreurMessagerie("Choisissez au moins un jour de la semaine.")
    if frequence == "mensuelle" and (not jours or not all(1 <= j <= 28 for j in jours)):
        raise ErreurMessagerie("Choisissez au moins un jour du mois (du 1er au 28).")
    if frequence == "quotidienne":
        jours = []
    heure = str(regle.get("heure") or "")
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", heure):
        raise ErreurMessagerie("Heure invalide (format HH:MM).")
    h = int(heure[:2])
    options = params["options"]
    if not options["heure_debut_envoi"] <= h < options["heure_fin_envoi"]:
        raise ErreurMessagerie(
            f"Choisissez une heure entre {options['heure_debut_envoi']} h et {options['heure_fin_envoi']} h "
            "(plage d'envoi du magasin).")
    return {"nom": nom, "segment": regle["segment"], "canaux_envoi": canaux, "frequence": frequence,
            "jours": jours, "heure": heure, "filtres": filtres, "actif": bool(regle.get("actif", True))}


def prochaine_execution(regle, apres):
    """Prochaine date (heure locale) strictement après ``apres``."""
    h, m = int(regle["heure"][:2]), int(regle["heure"][3:])
    jour = apres.replace(hour=h, minute=m, second=0, microsecond=0)
    for decalage in range(0, 400):
        candidat = jour + timedelta(days=decalage)
        if candidat <= apres:
            continue
        if regle["frequence"] == "quotidienne" \
                or (regle["frequence"] == "hebdomadaire" and candidat.weekday() in regle["jours"]) \
                or (regle["frequence"] == "mensuelle" and candidat.day in regle["jours"]):
            return candidat
    return None


def description_regle(regle):
    if regle["frequence"] == "quotidienne":
        quand = "Chaque jour"
    elif regle["frequence"] == "hebdomadaire":
        quand = "Chaque " + ", ".join(JOURS_SEMAINE[j] for j in regle["jours"])
    else:
        quand = "Le " + ", ".join("1er" if j == 1 else str(j) for j in regle["jours"]) + " de chaque mois"
    h, m = int(regle["heure"][:2]), regle["heure"][3:]
    return f"{quand} à {h} h" + (f" {m}" if m != "00" else "")


def _regle_publique(r):
    return {
        "_id": str(r["_id"]), "nom": r.get("nom"), "segment": r.get("segment"),
        "segment_libelle": svc.SEGMENTS.get(r.get("segment"), {}).get("libelle", ""),
        "canaux": r.get("canaux_envoi") or [], "frequence": r.get("frequence"), "jours": r.get("jours") or [],
        "heure": r.get("heure"), "filtres": r.get("filtres") or {}, "actif": bool(r.get("actif")),
        "description": description_regle(r),
        "prochaine_execution": _iso_local(r.get("prochaine_execution")) if r.get("actif") else None,
        "derniere_execution": _iso_local(r.get("derniere_execution")),
        "dernier_resultat": r.get("dernier_resultat"),
    }


def _table_regles():
    return db.tables.regles_messages


def liste_regles(magasin_id):
    regles = list(_table_regles().find({"magasin_id": ObjectId(str(magasin_id))}))
    return [_regle_publique(r) for r in sorted(regles, key=lambda r: r.get("created_at") or datetime.min)]


def creer_regle(magasin_id, user_id, data):
    mid = ObjectId(str(magasin_id))
    if _table_regles().count_documents({"magasin_id": mid}) >= MAX_REGLES_PAR_MAGASIN:
        raise ErreurMessagerie(f"{MAX_REGLES_PAR_MAGASIN} relances automatiques au maximum par magasin.")
    regle = _valider_regle(magasin_id, data)
    prochaine = prochaine_execution(regle, heure_locale())
    maintenant = datetime.utcnow()
    regle.update(magasin_id=mid, cree_par=str(user_id), created_at=maintenant, updated_at=maintenant,
                 prochaine_execution=vers_utc(prochaine) if prochaine else None)
    regle_id = _table_regles().insert_one(regle).inserted_id
    return _regle_publique(_table_regles().find_one({"_id": regle_id}))


def _regle_du_magasin(magasin_id, regle_id):
    regle = _table_regles().find_one({"_id": ObjectId(str(regle_id)), "magasin_id": ObjectId(str(magasin_id))}) \
        if ObjectId.is_valid(str(regle_id)) else None
    if not regle:
        raise ErreurMessagerie("Relance automatique introuvable.")
    return regle


def modifier_regle(magasin_id, regle_id, data):
    existante = _regle_du_magasin(magasin_id, regle_id)
    regle = _valider_regle(magasin_id, data, existante)
    prochaine = prochaine_execution(regle, heure_locale())
    regle.update(updated_at=datetime.utcnow(), prochaine_execution=vers_utc(prochaine) if prochaine else None)
    _table_regles().update_one({"_id": existante["_id"]}, {"$set": regle})
    return _regle_publique(_table_regles().find_one({"_id": existante["_id"]}))


def supprimer_regle(magasin_id, regle_id):
    regle = _regle_du_magasin(magasin_id, regle_id)
    _table_regles().delete_one({"_id": regle["_id"]})


def _executer_regle(regle, maintenant_utc, reserver=True):
    """Exécute une relance ; la réservation avance la prochaine date avant
    l'envoi, pour qu'un autre processus ne l'exécute pas en même temps."""
    if reserver:
        prochaine = prochaine_execution(regle, vers_local(maintenant_utc))
        reserve = _table_regles().update_one(
            {"_id": regle["_id"], "prochaine_execution": regle["prochaine_execution"]},
            {"$set": {"prochaine_execution": vers_utc(prochaine) if prochaine else None,
                      "derniere_execution": maintenant_utc}})
        if not reserve.modified_count:
            return None
    resultat = {"date": vers_local(maintenant_utc).isoformat()}
    campagne_id = None
    try:
        campagne_id = svc.creer_campagne(
            regle["magasin_id"], None, regle["segment"], regle.get("canaux_envoi"), None, regle.get("filtres"),
            type_envoi="automatique", origine={"regle_id": str(regle["_id"])})
        resultat["campagne_id"] = campagne_id
    except ErreurMessagerie as e:
        # « Aucun client joignable » est normal : tout le monde est à jour ou déjà relancé.
        resultat["info" if "Aucun client" in str(e) else "erreur"] = str(e)
    changes = {"dernier_resultat": resultat}
    if not reserver:
        changes["derniere_execution"] = maintenant_utc
    _table_regles().update_one({"_id": regle["_id"]}, {"$set": changes})
    if campagne_id:
        svc.lancer_campagne(campagne_id)
    return resultat


def executer_regle_maintenant(magasin_id, regle_id):
    """Lance la relance tout de suite, sans changer sa prochaine date."""
    regle = _regle_du_magasin(magasin_id, regle_id)
    return _executer_regle(regle, datetime.utcnow(), reserver=False)


def executer_regles_dues(maintenant=None):
    maintenant = maintenant or datetime.utcnow()
    for regle in list(_table_regles().find({})):
        if not regle.get("actif") or not regle.get("prochaine_execution") or regle["prochaine_execution"] > maintenant:
            continue
        params = svc.get_parametres(regle["magasin_id"])
        if not dans_plage(vers_local(maintenant), params["options"]):
            continue
        try:
            _executer_regle(regle, maintenant)
        except Exception:
            current_app.logger.exception("Relance automatique %s en erreur", regle["_id"])


# ---------------------------------------------------------------------------
# Planificateur
# ---------------------------------------------------------------------------
def tick(maintenant=None):
    """Un passage du planificateur : envois programmés puis relances dues."""
    executer_programmes_dus(maintenant)
    executer_regles_dues(maintenant)
    limite = (maintenant or datetime.utcnow()) - CONSERVATION_HISTORIQUE
    db.tables.envois_programmes.delete_many(
        {"statut": {"$in": ["envoye", "echec", "annule"]}, "updated_at": {"$lt": limite}})


_planificateur = {"fil": None}
_verrou = threading.Lock()


def demarrer_planificateur(app):
    """Démarre (une fois par processus) le fil qui exécute ``tick``."""
    with _verrou:
        if _planificateur["fil"] is not None:
            return
        arret = threading.Event()

        def boucle():
            while not arret.wait(INTERVALLE_PLANIFICATEUR):
                with app.app_context():
                    try:
                        tick()
                    except Exception:
                        app.logger.exception("Planificateur de messages : passage en erreur")

        fil = threading.Thread(target=boucle, name="planificateur-messages", daemon=True)
        fil.start()
        _planificateur.update(fil=fil, arret=arret)
