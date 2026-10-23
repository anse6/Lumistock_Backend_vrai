# services/ai_analytics_service.py
"""
Service d'analyse IA automatique des stocks et ventes.
Utilisé par le scheduler pour générer le rapport quotidien envoyé à l'admin.
"""

import os
from groq import Groq
from datetime import datetime, timedelta
from bson import ObjectId
from extensions import db


# ─────────────────────────────────────────────────────────────
#  Helpers de collecte de données
# ─────────────────────────────────────────────────────────────

def _get_produits_magasin(magasin_id):
    """Retourne tous les produits d'un magasin."""
    return list(db.tables.produits.find({"magasin_id": ObjectId(magasin_id)}))


def _get_livraisons_recentes(magasin_id, jours=30):
    """Retourne les livraisons des X derniers jours pour un magasin."""
    date_limite = datetime.utcnow() - timedelta(days=jours)
    return list(db.tables.livraisons.find({
        "magasin_id": ObjectId(magasin_id),
        "created_at": {"$gte": date_limite}
    }))


def _calculer_ventes_par_produit(livraisons):
    """
    Agrège la quantité vendue par produit_id sur les livraisons données.
    Retourne un dict { produit_id_str: quantite_totale_vendue }
    """
    ventes = {}
    for livraison in livraisons:
        for produit in livraison.get("produits", []):
            pid = str(produit.get("produit_id", ""))
            qte = int(produit.get("quantite", 0))
            ventes[pid] = ventes.get(pid, 0) + qte
    return ventes


def _identifier_alertes_locales(produits, ventes_30j):
    """
    Détecte les alertes sans IA (règles déterministes) pour enrichir le contexte.
    Retourne une liste d'alertes structurées.
    """
    alertes = []
    for p in produits:
        nom = p.get("nom", "Inconnu")
        qte_actuelle = int(p.get("quantite", 0))
        qte_initiale = int(p.get("quantite_initiale", 1)) or 1
        pid = str(p["_id"])
        ventes = ventes_30j.get(pid, 0)

        # 1. Stock critique (< 20% du stock initial)
        if qte_initiale > 0 and (qte_actuelle / qte_initiale) < 0.20 and qte_actuelle > 0:
            alertes.append({
                "type": "stock_critique",
                "produit": nom,
                "quantite_restante": qte_actuelle,
                "quantite_initiale": qte_initiale,
                "ventes_30j": ventes,
                "pourcentage_restant": round(qte_actuelle / qte_initiale * 100, 1)
            })

        # 2. Rupture de stock
        if qte_actuelle == 0:
            alertes.append({
                "type": "rupture",
                "produit": nom,
                "quantite_restante": 0,
                "ventes_30j": ventes,
            })

        # 3. Produit dormant (en stock mais jamais vendu en 30 jours)
        if qte_actuelle > 0 and ventes == 0:
            alertes.append({
                "type": "produit_dormant",
                "produit": nom,
                "quantite_restante": qte_actuelle,
                "ventes_30j": 0,
            })

        # 4. Vente rapide - risque de rupture (rythme de vente > stock restant pour 7 jours)
        if ventes > 0 and qte_actuelle > 0:
            rythme_par_jour = ventes / 30
            jours_restants = qte_actuelle / rythme_par_jour if rythme_par_jour > 0 else 999
            if jours_restants < 7:
                alertes.append({
                    "type": "rupture_imminente",
                    "produit": nom,
                    "quantite_restante": qte_actuelle,
                    "ventes_30j": ventes,
                    "jours_avant_rupture": round(jours_restants, 1)
                })

    return alertes


# ─────────────────────────────────────────────────────────────
#  Construction du prompt IA
# ─────────────────────────────────────────────────────────────

def _construire_prompt_analyse(magasin_nom, produits, alertes, ventes_30j, total_livraisons):
    """
    Construit le prompt envoyé à l'IA Groq pour générer une analyse intelligente.
    """
    # Résumé des produits
    lignes_produits = []
    for p in produits[:30]:  # Limiter à 30 produits pour ne pas dépasser les tokens
        pid = str(p["_id"])
        nom = p.get("nom", "Inconnu")
        qte = p.get("quantite", 0)
        qte_init = p.get("quantite_initiale", 0)
        unite = p.get("unite", "")
        prix = p.get("prix", 0)
        ventes = ventes_30j.get(pid, 0)
        lignes_produits.append(
            f"  - {nom}: stock={qte}{unite}, initial={qte_init}{unite}, prix={prix}, ventes_30j={ventes}"
        )

    # Résumé des alertes
    alertes_str = ""
    for a in alertes:
        t = a["type"]
        nom = a["produit"]
        if t == "stock_critique":
            alertes_str += f"\n  ⚠️ STOCK CRITIQUE: '{nom}' ({a['pourcentage_restant']}% restant, {a['quantite_restante']} unités)"
        elif t == "rupture":
            alertes_str += f"\n  🔴 RUPTURE: '{nom}' est en rupture de stock"
        elif t == "produit_dormant":
            alertes_str += f"\n  🟡 DORMANT: '{nom}' a {a['quantite_restante']} unités en stock mais 0 vente en 30 jours"
        elif t == "rupture_imminente":
            alertes_str += f"\n  🟠 RUPTURE IMMINENTE: '{nom}' - rupture dans ~{a['jours_avant_rupture']} jours au rythme actuel"

    date_rapport = datetime.utcnow().strftime("%d/%m/%Y")

    prompt = f"""Tu es un expert en gestion de stock et en analyse commerciale. 
Tu dois générer un rapport quotidien pour le magasin "{magasin_nom}" à la date du {date_rapport}.

DONNÉES DU MAGASIN :
- Nombre de produits : {len(produits)}
- Total livraisons (30 derniers jours) : {total_livraisons}
- Total ventes par produit (30 jours) :
{chr(10).join(lignes_produits)}

ALERTES DÉTECTÉES AUTOMATIQUEMENT :
{alertes_str if alertes_str else "  ✅ Aucune alerte critique détectée."}

INSTRUCTIONS :
Génère un rapport quotidien clair, professionnel et utile pour l'administrateur du magasin. Structure ta réponse EXACTEMENT ainsi :

## 📊 Résumé Exécutif
[2-3 phrases résumant la situation globale du magasin]

## 🔴 Alertes Prioritaires
[Liste des actions urgentes à faire AUJOURD'HUI - approvisionnements nécessaires, etc.]

## 🟡 Points de Vigilance
[Produits à surveiller dans les prochains jours]

## 📈 Opportunités Commerciales  
[Produits bien vendus à mettre en valeur, produits dormants à promouvoir]

## ✅ Recommandations
[3 à 5 recommandations concrètes et actionnables]

Sois concis, direct et pratique. Évite le jargon technique. L'admin doit pouvoir agir immédiatement après lecture."""

    return prompt


# ─────────────────────────────────────────────────────────────
#  Fonction principale d'analyse
# ─────────────────────────────────────────────────────────────

def analyser_stocks_magasin(magasin_id, magasin_nom="Votre magasin"):
    """
    Analyse complète des stocks et ventes d'un magasin via l'IA Groq.

    Args:
        magasin_id (str): ID MongoDB du magasin
        magasin_nom (str): Nom du magasin pour personnaliser le rapport

    Returns:
        dict: {
            "rapport_ia": str,          # Texte du rapport généré par l'IA
            "alertes": list,            # Liste des alertes structurées
            "nb_produits": int,
            "nb_alertes": int,
            "nb_livraisons_30j": int,
            "genere_le": str,           # Date ISO
            "erreur": bool
        }
    """
    try:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key or api_key == "your_groq_api_key_here":
            return {
                "rapport_ia": "⚠️ L'IA n'est pas configurée. Ajoutez GROQ_API_KEY dans le fichier .env.",
                "alertes": [],
                "nb_produits": 0,
                "nb_alertes": 0,
                "nb_livraisons_30j": 0,
                "genere_le": datetime.utcnow().isoformat(),
                "erreur": True
            }

        # 1. Collecte des données
        produits = _get_produits_magasin(magasin_id)
        livraisons = _get_livraisons_recentes(magasin_id, jours=30)
        ventes_30j = _calculer_ventes_par_produit(livraisons)

        # 2. Détection des alertes déterministes
        alertes = _identifier_alertes_locales(produits, ventes_30j)

        # 3. Construction du prompt
        prompt = _construire_prompt_analyse(
            magasin_nom=magasin_nom,
            produits=produits,
            alertes=alertes,
            ventes_30j=ventes_30j,
            total_livraisons=len(livraisons)
        )

        # 4. Appel à Groq
        client = Groq(api_key=api_key)
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Tu es un expert en gestion de stock et logistique pour des magasins africains. "
                        "Tu génères des rapports quotidiens précis, utiles et actionnables. "
                        "Utilise des emojis pour rendre le rapport lisible. "
                        "Réponds UNIQUEMENT en français."
                    )
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0.4,
            max_tokens=1500,
            stream=False,
        )

        rapport_ia = completion.choices[0].message.content

        return {
            "rapport_ia": rapport_ia,
            "alertes": alertes,
            "nb_produits": len(produits),
            "nb_alertes": len(alertes),
            "nb_livraisons_30j": len(livraisons),
            "genere_le": datetime.utcnow().isoformat(),
            "erreur": False
        }

    except Exception as e:
        print(f"[❌ AI Analytics] Erreur lors de l'analyse du magasin {magasin_id}: {e}")
        return {
            "rapport_ia": f"Erreur lors de la génération du rapport : {str(e)}",
            "alertes": [],
            "nb_produits": 0,
            "nb_alertes": 0,
            "nb_livraisons_30j": 0,
            "genere_le": datetime.utcnow().isoformat(),
            "erreur": True
        }
