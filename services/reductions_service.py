# services/reductions_service.py
"""Réductions commerciales d'une vente : rabais, remise et ristourne.

Définitions (usage comptable français / SYSCOHADA) :
- Rabais    : réduction exceptionnelle pour un défaut (qualité, non-conformité,
              retard de livraison...).
- Remise    : réduction habituelle liée au client ou à la commande
              (quantité achetée, fidélité, promotion...).
- Ristourne : réduction calculée sur le volume d'affaires réalisé avec le
              client sur une période, accordée sur cette vente.

Sur une même vente, elles s'appliquent EN CASCADE dans cet ordre :
rabais, puis remise sur le montant restant, puis ristourne sur le montant
restant. Une réduction en pourcentage porte donc sur le montant déjà réduit.

À ne pas confondre avec le champ historique ``ristourne`` d'une livraison :
celui-ci désigne le reste impayé abandonné au client (validé par le manager).
"""

ORDRE = ("rabais", "remise", "ristourne")
LIBELLES = {"rabais": "Rabais", "remise": "Remise", "ristourne": "Ristourne"}
MODES = ("pourcentage", "montant")


def valider_reductions(reductions):
    """Vérifie la structure envoyée par le client. Lève ValueError si invalide."""
    if reductions in (None, ""):
        return []
    if not isinstance(reductions, list):
        raise ValueError("« reductions » doit être une liste.")
    vus = set()
    propres = []
    for item in reductions:
        if not isinstance(item, dict):
            raise ValueError("Chaque réduction doit être un objet.")
        type_ = str(item.get("type", "")).lower()
        if type_ not in ORDRE:
            raise ValueError(f"Type de réduction inconnu : {item.get('type')}. Valeurs possibles : rabais, remise, ristourne.")
        if type_ in vus:
            raise ValueError(f"La réduction « {LIBELLES[type_]} » est indiquée deux fois.")
        vus.add(type_)
        mode = str(item.get("mode", "montant")).lower()
        if mode not in MODES:
            raise ValueError("Le mode d'une réduction doit être « pourcentage » ou « montant ».")
        try:
            valeur = float(item.get("valeur", 0) or 0)
        except (TypeError, ValueError):
            raise ValueError(f"Valeur invalide pour la réduction « {LIBELLES[type_]} ».")
        if valeur < 0:
            raise ValueError(f"La réduction « {LIBELLES[type_]} » ne peut pas être négative.")
        if mode == "pourcentage" and valeur > 100:
            raise ValueError(f"La réduction « {LIBELLES[type_]} » ne peut pas dépasser 100 %.")
        motif = str(item.get("motif") or "").strip()[:200]
        if valeur > 0:
            propres.append({"type": type_, "mode": mode, "valeur": valeur, "motif": motif})
    return propres


def appliquer_reductions(reductions, montant_brut, decimales=2):
    """Calcule les réductions en cascade sur ``montant_brut``.

    :return: (détail, total) où chaque élément du détail contient le type, le
             libellé, le mode, la valeur saisie, le motif, la base de calcul et
             le montant déduit.
    """
    from services.tva_service import arrondir

    propres = valider_reductions(reductions)
    par_type = {r["type"]: r for r in propres}
    base = max(float(montant_brut or 0), 0.0)
    detail, total = [], 0.0
    for type_ in ORDRE:
        r = par_type.get(type_)
        if not r:
            continue
        montant = base * r["valeur"] / 100 if r["mode"] == "pourcentage" else r["valeur"]
        montant = arrondir(min(montant, base), decimales)  # 0 décimale pour le franc CFA
        detail.append({
            "type": type_,
            "libelle": LIBELLES[type_],
            "mode": r["mode"],
            "valeur": r["valeur"],
            "motif": r["motif"],
            "base": round(base, 2),
            "montant": montant,
        })
        total += montant
        base -= montant
    return detail, round(total, 2)
