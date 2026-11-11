"""Base de connaissances de Nansé, l'assistante du site public.

Chaque fiche décrit une fonction réelle de Lumistock (mêmes faits que le site
public, frontend/src/Modules/Public/site.ts). Pour répondre à une question,
l'assistante cherche d'abord les fiches les plus proches (``chercher``) et ne
répond qu'à partir de celles-ci : elle n'invente ni prix, ni délai, ni
fonction.
"""
import re
import unicodedata

from rapidfuzz import fuzz

FICHES = [
    {
        "id": "presentation",
        "titre": "Qu'est-ce que Lumistock ?",
        "mots": "lumistock logiciel presentation quoi c'est application gestion commerciale",
        "texte": "Lumistock est un logiciel de gestion commerciale pour les commerces du monde entier : "
                 "ventes, caisse, stock, crédit client, messagerie SMS/email, comptabilité SYSCOHADA, rapports et "
                 "assistant IA. Il s'utilise sur ordinateur, tablette et téléphone, en français, anglais, espagnol, "
                 "allemand, chinois et arabe. Il convient aux boutiques, quincailleries, supermarchés, grossistes, "
                 "dépôts de boissons, pharmacies, cosmétiques, librairies, matériaux de construction, pièces détachées.",
    },
    {
        "id": "modules",
        "titre": "Les modules",
        "mots": "modules fonctionnalites liste tout ce que fait inclus",
        "texte": "Neuf modules, tous inclus pour chaque magasin : Ventes ; Caisse (point de vente) ; Clients et crédit ; "
                 "Messagerie clients (SMS et email) ; Stock ; Achats et fournisseurs ; Comptabilité SYSCOHADA ; "
                 "Rapports et assistant IA ; Équipe et droits d'accès. Les modules partagent les mêmes données : une "
                 "vente met à jour le stock, la caisse, le compte du client et la comptabilité.",
    },
    {
        "id": "tarifs",
        "titre": "Tarifs et licence",
        "mots": "prix tarif cout combien payer abonnement licence offre devis gratuit essai mensuel",
        "texte": "Lumistock n'affiche pas de prix public. Le tarif dépend de la licence, c'est-à-dire du nombre de "
                 "comptes : managers, gérants et caissiers ; l'administrateur du magasin est toujours inclus. Tous les "
                 "modules sont inclus dans chaque offre, sans option payante. Pour obtenir un tarif : page Contact "
                 "(sujet « Tarifs et licence ») ou un conseiller Lumistock. Ne jamais donner de montant.",
    },
    {
        "id": "inscription",
        "titre": "S'inscrire et démarrer",
        "mots": "inscription inscrire creer magasin compte commencer demarrer validation valider combien de temps etapes",
        "texte": "Trois étapes : 1) inscrire le magasin sur la page « Créer mon magasin » (nom, activité, email, "
                 "téléphone, pays, ville, quartier, puis l'administrateur, puis facultativement les informations "
                 "légales et le logo) ; 2) l'équipe Lumistock vérifie et valide la demande, puis l'administrateur "
                 "reçoit un email avec un lien pour choisir son mot de passe ; 3) ajouter ou importer ses articles, "
                 "inviter son équipe, ouvrir la caisse. Aucun délai de validation précis n'est garanti.",
    },
    {
        "id": "plusieurs_magasins",
        "titre": "Plusieurs magasins",
        "mots": "plusieurs magasins succursales boutiques points de vente multi",
        "texte": "Chaque magasin s'inscrit séparément avec son propre email et son administrateur : son stock, son "
                 "équipe, ses clients et sa comptabilité restent séparés.",
    },
    {
        "id": "appareils",
        "titre": "Appareils et langues",
        "mots": "telephone tablette ordinateur installer navigateur mobile android application langue anglais francais",
        "texte": "Lumistock s'utilise sur ordinateur, tablette et téléphone, avec le même compte et les mêmes "
                 "données sur chaque appareil. Chaque utilisateur choisit sa langue à tout moment : français, anglais, "
                 "espagnol, allemand, chinois ou arabe.",
    },
    {
        "id": "ventes",
        "titre": "Ventes",
        "mots": "vente vendre facture pdf remise rabais ristourne reduction tva credit partiel retour annuler annulation",
        "texte": "Une vente se fait au comptant, en partie ou à crédit ; le reste dû passe sur le compte du client. "
                 "Réductions distinctes : rabais (défaut), remise (bon client), ristourne (volume d'affaires). La TVA "
                 "du pays du magasin (19,25 % au Cameroun) s'applique au montant net. Plusieurs paiements par vente "
                 "(espèces, Orange Money, MTN MoMo, banque, chèque). Facture PDF au nom de l'entreprise. Retours "
                 "partiels ou totaux : l'article revient en stock, le client est remboursé ou crédité. Une vente "
                 "annulée va à la corbeille et peut être restaurée.",
    },
    {
        "id": "caisse",
        "titre": "Caisse (point de vente)",
        "mots": "caisse caissier comptoir point de vente ticket imprimante thermique code barre scanner session fond ecart",
        "texte": "Écran de vente rapide : recherche par nom ou lecteur de codes-barres (Lumistock peut générer les "
                 "codes), client de passage ou enregistré, monnaie à rendre calculée, ticket 80 mm pour imprimante "
                 "thermique. Chaque caissier a son compte et sa session de caisse : fond d'ouverture, espèces "
                 "comptées à la fermeture, écart enregistré. Plusieurs caissiers en même temps (selon la licence). "
                 "Un caissier ne voit ni prix d'achat, ni marges, ni finances ; remises seulement si l'administrateur "
                 "les autorise ; crédit seulement pour un client enregistré.",
    },
    {
        "id": "mobile_money",
        "titre": "Mobile money",
        "mots": "orange money mtn momo mobile money paiement electronique",
        "texte": "Orange Money et MTN MoMo sont des modes de paiement enregistrés à côté des espèces, de la banque et "
                 "du chèque. Le paiement lui-même se fait sur le téléphone du client : Lumistock enregistre "
                 "l'encaissement, le suit dans la trésorerie et permet le rapprochement du relevé mobile money.",
    },
    {
        "id": "credit_client",
        "titre": "Clients et crédit",
        "mots": "client dette credit cahier reglement avance impaye recouvrer debiteur abandon",
        "texte": "Fiche client (nom, téléphone, email, ville, quartier). La dette d'avant Lumistock (cahier de crédit) "
                 "se saisit sur la fiche. Un règlement solde d'abord cette dette, puis les ventes impayées les plus "
                 "anciennes ; le surplus devient une avance sur le compte du client. Un petit reste peut être "
                 "abandonné avec validation d'un responsable. Le tableau de bord liste les débiteurs et les clients "
                 "sans achat depuis 60 jours.",
    },
    {
        "id": "messagerie",
        "titre": "Messagerie SMS et email",
        "mots": "sms email message relance rappel campagne whatsapp notification client",
        "texte": "Envoi de messages par SMS (fournisseur Orange ou Twilio) et par email, par catégorie de clients "
                 "(débiteurs, clients avec avance, acheteurs, tous), avec des modèles modifiables et des variables "
                 "({nom}, {dette}, {avance}, {magasin}). Envois programmés, relances automatiques, délai minimal "
                 "entre deux messages, clients désinscrits toujours exclus, historique des envois, quota mensuel de "
                 "SMS possible. Pas de WhatsApp.",
    },
    {
        "id": "stock",
        "titre": "Stock",
        "mots": "stock inventaire produit article rupture alerte seuil import excel code barre conditionnement carton avarie perime dormant",
        "texte": "Catalogue (nom, image, catégorie, unité, prix de vente et d'achat), conditionnements (pièce, carton, "
                 "sac…), codes-barres, import et export Excel. Seuil d'alerte par article, ruptures sur le tableau de "
                 "bord, stock qui dort (60 jours sans vente). Journal de chaque mouvement (vente, retour, "
                 "approvisionnement, avarie, périmé), inventaire valorisé, rapport de stock annuel, corbeille.",
    },
    {
        "id": "valorisation",
        "titre": "Valorisation du stock",
        "mots": "cmup fifo lifo valorisation cout moyen marge prix d'achat cout d'achat lots",
        "texte": "Chaque entrée en stock crée un lot avec son prix d'achat. La méthode, choisie par l'administrateur, "
                 "est CMUP (par défaut), FIFO ou LIFO ; le coût de chaque vente et la marge sont calculés selon "
                 "cette méthode. Le coût d'achat de l'article est recalculé à chaque entrée ou sortie : moyenne "
                 "pondérée en CMUP, coût des lots restants en FIFO et LIFO. Exemple : 10 achetés à 100 puis 30 à "
                 "200 donnent un coût de 175 en CMUP. La fiche article détaille les lots et la valeur du stock.",
    },
    {
        "id": "numeros_serie",
        "titre": "Numéros de série",
        "mots": "numero de serie serie machine telephone ordinateur garantie tracabilite unite imei",
        "texte": "Option à activer dans Paramètres › Stock, pour les articles vendus à l'unité avec un numéro "
                 "unique (machines, téléphones…). Chaque unité est enregistrée avec son numéro à l'arrivée "
                 "(création, approvisionnement, facture fournisseur), désignée à la vente au scan ou dans une "
                 "liste, et suivie aux retours, annulations et avaries. Le numéro figure sur le ticket et la "
                 "facture. Le rapport « Numéros de série » montre quelle unité est sortie, quand et pour quel "
                 "client, et l'historique complet de chaque numéro.",
    },
    {
        "id": "achats",
        "titre": "Achats et fournisseurs",
        "mots": "fournisseur achat approvisionnement approvisionner reapprovisionner facture fournisseur echeance avoir",
        "texte": "Fiches fournisseurs ; approvisionnements (le stock monte, un lot est créé avec le prix d'achat) ; "
                 "factures fournisseurs avec échéance, paiements partiels, avoirs, pièces jointes et TVA récupérable.",
    },
    {
        "id": "gerants",
        "titre": "Gérants et stock sur le terrain",
        "mots": "gerant vendeur terrain depot tournee livreur stock confie invendus",
        "texte": "Un gérant reçoit du stock du magasin et vend depuis ce stock dans son propre espace (ventes, "
                 "clients, rapports), puis rend les invendus au magasin. Idéal pour un dépôt ou une tournée.",
    },
    {
        "id": "comptabilite",
        "titre": "Comptabilité SYSCOHADA",
        "mots": "comptabilite comptable syscohada ohada bilan journal tva impot declaration cloture exercice tresorerie rapprochement bancaire charges immobilisations emprunt",
        "texte": "Les opérations saisies (ventes, encaissements, achats, charges, immobilisations, financements) sont "
                 "passées automatiquement au journal selon le plan SYSCOHADA révisé. Compte de résultat, trésorerie "
                 "par compte (caisse, banque, mobile money), bilan SYSCOHADA, TVA à reverser mois par mois avec report "
                 "du crédit, impôt sur le résultat (minimum fiscal), rapprochement bancaire, verrouillage des périodes "
                 "et clôture d'exercice. Journal exportable pour le comptable.",
    },
    {
        "id": "pays",
        "titre": "Pays et monnaie",
        "mots": "pays cameroun gabon congo tchad centrafrique guinee equatoriale fcfa franc cfa monnaie devise afrique monde international europe france etranger",
        "texte": "Utilisable partout dans le monde. La devise est celle du magasin (FCFA sans centimes par défaut) "
                 "et le taux de TVA par défaut dépend du pays du magasin, pour environ 70 pays d'Afrique, d'Europe, "
                 "d'Amérique et d'Asie (par exemple Cameroun 19,25 %, Gabon 18 %, Congo 18 %, Tchad 18 %, Centrafrique 19 %, Guinée "
                 "équatoriale 15 %), modifiables. La devise est réglable dans les informations du magasin.",
    },
    {
        "id": "rapports",
        "titre": "Rapports et tableaux de bord",
        "mots": "rapport tableau de bord statistiques chiffre d'affaires export excel pdf imprimer analyse",
        "texte": "Un tableau de bord par rôle (onglets stock, ventes, clients, finances, équipe). Cinq rapports : "
                 "ventes, encaissements, mouvements de stock, stock annuel, inventaire ; export Excel, PDF ou "
                 "impression.",
    },
    {
        "id": "ia_magasin",
        "titre": "Assistant IA du magasin",
        "mots": "intelligence artificielle ia assistant analyse conseil vocal",
        "texte": "Dans l'application, l'assistant IA rédige l'analyse du magasin (synthèse, priorités de la semaine, "
                 "points de vigilance) à partir des seuls chiffres du magasin, répond aux questions et propose un "
                 "résumé vocal.",
    },
    {
        "id": "equipe",
        "titre": "Équipe et droits",
        "mots": "equipe employe role droit acces permission manager caissier admin administrateur inviter desactiver",
        "texte": "Quatre rôles : administrateur (tous les droits), manager, gérant, caissier. Invitation par email avec "
                 "un lien à usage unique. L'administrateur règle 19 droits par rôle (créer/supprimer des articles, "
                 "annuler une vente, voir la comptabilité…). Désactiver un compte coupe l'accès immédiatement.",
    },
    {
        "id": "securite",
        "titre": "Sécurité",
        "mots": "securite donnees protection mot de passe double authentification 2fa piratage confidentialite sauvegarde",
        "texte": "Double authentification facultative (code à 6 chiffres + codes de secours), compte verrouillé 15 "
                 "minutes après 5 mots de passe erronés, aucun mot de passe envoyé par email, mots de passe hachés, "
                 "déconnexion à distance, historique des connexions, droits vérifiés par le serveur, magasins "
                 "séparés, périodes comptables verrouillables, corbeille avant suppression définitive.",
    },
    {
        "id": "mot_de_passe",
        "titre": "Mot de passe oublié",
        "mots": "mot de passe oublie connexion connecter acceder compte bloque",
        "texte": "Sur la page de connexion, « Mot de passe oublié ? » envoie par email un lien pour en choisir un "
                 "nouveau. Après 5 essais erronés, attendre 15 minutes.",
    },
    {
        "id": "contact",
        "titre": "Contacter Lumistock",
        "mots": "contact contacter telephone email joindre demonstration demo rendez-vous support aide humain conseiller",
        "texte": "Page Contact du site (formulaire, réponse par email), téléphone +237 695 683 485. Une démonstration "
                 "se demande par le formulaire de contact (sujet « Découvrir Lumistock / démonstration »). Dans ce "
                 "chat, un conseiller Lumistock peut aussi reprendre la conversation.",
    },
]


def _normaliser(texte):
    texte = unicodedata.normalize("NFD", str(texte or "").lower())
    texte = "".join(c for c in texte if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]+", " ", texte)


_MOTS_VIDES = set("le la les un une des de du d l et ou a au aux en dans pour par sur avec est ce que qui quoi "
                  "je tu il nous vous ils mon ma mes votre vos son sa ses on y ne pas plus est-ce comment "
                  "the a an is are of to for in on with what how do does can i my your".split())


def _mots(texte):
    return {m for m in _normaliser(texte).split() if len(m) > 2 and m not in _MOTS_VIDES}


def chercher(question, nombre=4):
    """Fiches les plus proches de la question, de la plus pertinente à la moins pertinente."""
    mots_question = _mots(question)
    q = _normaliser(question)
    resultats = []
    for fiche in FICHES:
        mots_fiche = _mots(fiche["mots"] + " " + fiche["titre"])
        # Mots en commun (avec tolérance aux fautes) + proximité globale du texte.
        communs = sum(1 for m in mots_question if any(fuzz.ratio(m, f) >= 85 for f in mots_fiche))
        score = communs * 20 + fuzz.token_set_ratio(q, _normaliser(fiche["mots"])) * 0.3
        resultats.append((score, fiche))
    resultats.sort(key=lambda r: r[0], reverse=True)
    return [f for s, f in resultats[:nombre] if s >= 25] or [r[1] for r in resultats[:2]]
