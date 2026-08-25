# -*- coding: utf-8 -*-
"""
Brique 2 : moteur de règles.

Ce moteur ne fait JAMAIS remonter une erreur "certaine" : il propose des
alertes, chacune assortie d'un niveau de confiance et d'une justification.
Le positionnement reste strictement celui défini avec le client : on ne
cherche que ce qui joue EN FAVEUR du propriétaire, jamais l'inverse.

La base de règles ci-dessous est un point de départ à enrichir au fil des
dossiers réellement traités (voir Phase 0 de la feuille de route : tester
sur son propre avis / celui d'un proche, à titre personnel et non commercial).
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Alerte:
    type_erreur: str
    description: str
    confiance: str  # "forte", "moyenne", "faible_necessite_verification_terrain"
    piece_a_verifier: str
    action_recommandee: str
    reference_legale: str = ""  # article de loi ou source DGFIP précise, pour le rapport de justification


def regle_surface_ponderee(champs: dict, surface_reelle_m2: Optional[float] = None) -> list[Alerte]:
    """
    Compare la surface pondérée retenue par le fisc à la surface réelle du
    bien, quand cette dernière est connue (mesurée sur place ou déclarée
    par le client). Sans surface réelle fournie, on ne peut pas conclure :
    la fonction de doute doit alors s'activer.
    """
    alertes = []
    surface_fiscale = champs.get("surface_ponderee_m2", {}).get("valeur_candidate")

    if surface_fiscale is None:
        alertes.append(Alerte(
            type_erreur="surface_ponderee_illisible",
            description="La surface pondérée n'a pas pu être lue automatiquement dans le document.",
            confiance="faible_necessite_verification_terrain",
            piece_a_verifier="fiche d'évaluation cadastrale",
            action_recommandee="Relire le document manuellement, ou demander une version plus lisible.",
            reference_legale="Article 1494 du CGI (méthode d'évaluation cadastrale des locaux d'habitation)",
        ))
        return alertes

    if surface_reelle_m2 is None:
        alertes.append(Alerte(
            type_erreur="surface_ponderee_a_confirmer",
            description=f"Surface fiscale trouvée : {surface_fiscale} m². "
                        "Aucune surface réelle de comparaison n'a été fournie.",
            confiance="faible_necessite_verification_terrain",
            piece_a_verifier="mesure réelle du bien (visite ou plan fiable)",
            action_recommandee="Mesurer ou faire confirmer la surface réelle avant de conclure.",
            reference_legale="Article 1494 du CGI (méthode d'évaluation cadastrale des locaux d'habitation)",
        ))
        return alertes

    try:
        ecart = float(str(surface_fiscale).replace(",", ".")) - float(surface_reelle_m2)
    except ValueError:
        alertes.append(Alerte(
            type_erreur="surface_ponderee_illisible",
            description=f"La valeur extraite pour la surface pondérée ('{surface_fiscale}') n'est pas "
                        "un nombre exploitable — probablement une erreur de lecture automatique du document.",
            confiance="faible_necessite_verification_terrain",
            piece_a_verifier="fiche d'évaluation cadastrale (relecture manuelle du champ surface)",
            action_recommandee="Relire manuellement la surface pondérée sur le document original.",
            reference_legale="Article 1494 du CGI (méthode d'évaluation cadastrale)",
        ))
        return alertes

    if ecart is not None and ecart > 0:
        alertes.append(Alerte(
            type_erreur="surface_surevaluee",
            description=f"La surface retenue par le fisc ({surface_fiscale} m²) est supérieure "
                        f"à la surface réelle mesurée ({surface_reelle_m2} m²), écart de {ecart:.1f} m².",
            confiance="forte" if ecart >= 5 else "moyenne",
            piece_a_verifier="fiche d'évaluation cadastrale + mesure réelle",
            action_recommandee="Dossier à instruire : réclamation potentielle en faveur du propriétaire.",
            reference_legale="Article 1494 du CGI (méthode d'évaluation cadastrale) et article 1388 du CGI (base d'imposition)",
        ))
    return alertes


def regle_annee_evaluation(champs: dict) -> list[Alerte]:
    """
    Une évaluation ancienne, jamais révisée, est un signal statistique
    (30 à 40% des avis contiennent une erreur selon les sources publiques
    déjà citées dans le dossier PDF) — mais ce n'est jamais, à elle seule,
    une preuve d'erreur : elle doit systématiquement déclencher une
    vérification plus poussée plutôt qu'une conclusion automatique.
    """
    alertes = []
    annee = champs.get("annee_evaluation", {}).get("valeur_candidate")
    if annee and annee.isdigit() and int(annee) < 1990:
        alertes.append(Alerte(
            type_erreur="evaluation_ancienne",
            description=f"Évaluation datée de {annee}, jamais révisée depuis longtemps : "
                        "signal statistique fort de risque d'erreur (voir dossier PDF, section 4).",
            confiance="moyenne",
            piece_a_verifier="fiche d'évaluation cadastrale complète",
            action_recommandee="Vérifier en priorité la catégorie de confort et les éléments annexés.",
            reference_legale="Article 1517 du CGI (révision périodique des valeurs locatives)",
        ))
    return alertes


def regle_element_disparu(champs: dict, elements_declares_disparus: Optional[list[str]] = None) -> list[Alerte]:
    """
    Vérifie si des éléments que le client déclare avoir supprimés (piscine,
    véranda, dépendance...) apparaissent toujours dans le texte du document.
    Cette règle a volontairement besoin d'une déclaration du client car un
    élément détruit n'est, par nature, pas mesurable sur les seules pièces
    administratives : c'est un cas typique de la fonction de doute.
    """
    alertes = []
    if not elements_declares_disparus:
        return alertes

    texte = champs.get("_texte_brut_pour_recherche", "")
    for element in elements_declares_disparus:
        if element.lower() in texte.lower():
            alertes.append(Alerte(
                type_erreur="element_disparu_toujours_taxe",
                description=f"Le client indique que « {element} » n'existe plus, mais ce terme "
                            "apparaît toujours dans le document fiscal.",
                confiance="faible_necessite_verification_terrain",
                piece_a_verifier="constat visuel sur place ou photo datée",
                action_recommandee="Déplacement recommandé pour constater et documenter la disparition.",
                reference_legale="Article 1406 du CGI (obligation de déclaration des changements de consistance ou d'affectation d'un bien)",
            ))
    return alertes


def regle_coherence_base_valeur_locative(champs: dict) -> list[Alerte]:
    """
    Idée ajoutée : la base d'imposition doit normalement correspondre à
    environ 50% de la valeur locative cadastrale (abattement forfaitaire).
    Si ce ratio est très éloigné de 50%, deux explications possibles :
    soit une vraie anomalie sur le dossier, soit une erreur de lecture du
    document par l'outil lui-même. Dans les deux cas, cela doit remonter
    comme un point à vérifier plutôt que d'être ignoré silencieusement.
    """
    alertes = []
    base = champs.get("base_imposition_euros", {}).get("valeur_candidate")
    valeur_locative = champs.get("valeur_locative_cadastrale", {}).get("valeur_candidate")

    if base is None or valeur_locative is None:
        return alertes

    try:
        base_f = float(str(base).replace(" ", "").replace(",", "."))
        vlc_f = float(str(valeur_locative).replace(" ", "").replace(",", "."))
        if vlc_f == 0:
            return alertes
        ratio = base_f / vlc_f
    except ValueError:
        return alertes

    if not (0.4 <= ratio <= 0.6):
        alertes.append(Alerte(
            type_erreur="incoherence_base_valeur_locative",
            description=f"Le ratio base d'imposition / valeur locative ({ratio:.0%}) s'écarte fortement "
                        "des 50% attendus après abattement forfaitaire.",
            confiance="faible_necessite_verification_terrain",
            piece_a_verifier="avis de taxe foncière complet",
            action_recommandee="Vérifier manuellement ces deux montants : possible erreur de lecture "
                                "automatique OU véritable anomalie du dossier à creuser.",
            reference_legale="Article 1388 du CGI (base d'imposition égale à 50% de la valeur locative cadastrale)",
        ))
    return alertes


def estimer_gain_potentiel(champs: dict, alertes: list[Alerte], surface_reelle_m2=None) -> Optional[dict]:
    """
    Idée ajoutée : donner un ordre de grandeur du gain financier potentiel
    pour le propriétaire, afin de prioriser les dossiers les plus rentables
    à instruire en premier — utile dès qu'il y aura plusieurs clients en
    parallèle.

    Estimation volontairement prudente et approximative : un pourcentage de
    correction de la base d'imposition proportionnel à l'écart de surface,
    appliqué à une base indicative si le taux d'imposition local n'est pas
    connu. À affiner avec de vrais taux communaux du 06 par la suite.
    """
    base = champs.get("base_imposition_euros", {}).get("valeur_candidate")
    surface_fiscale = champs.get("surface_ponderee_m2", {}).get("valeur_candidate")

    if base is None or surface_fiscale is None or surface_reelle_m2 is None:
        return None

    try:
        base_f = float(str(base).replace(" ", "").replace(",", "."))
        surface_fiscale_f = float(str(surface_fiscale).replace(",", "."))
    except ValueError:
        return None

    if surface_fiscale_f == 0:
        return None

    ecart_relatif = (surface_fiscale_f - surface_reelle_m2) / surface_fiscale_f
    if ecart_relatif <= 0:
        return None

    base_corrigee_estimee = base_f * (1 - ecart_relatif)
    gain_base_estime = base_f - base_corrigee_estimee

    # Taux moyen indicatif communes du 06 (à remplacer par le taux réel dès qu'il est connu)
    taux_moyen_indicatif = 0.40  # 40%, ordre de grandeur, PAS une valeur officielle
    gain_annuel_estime_euros = gain_base_estime * taux_moyen_indicatif

    return {
        "gain_base_imposition_estime_euros": round(gain_base_estime, 0),
        "gain_annuel_taxe_estime_euros": round(gain_annuel_estime_euros, 0),
        "avertissement": "Estimation approximative avec un taux indicatif (40%) — à remplacer par le "
                          "taux communal réel du bien avant toute annonce chiffrée au client.",
    }


def regle_comparaison_permis_construire(champs: dict, champs_permis: Optional[dict] = None) -> list[Alerte]:
    """
    Compare la surface retenue par le fisc à la surface déclarée sur le permis
    de construire du bien, quand ce document est disponible. Un écart peut
    signaler soit une extension non déclarée au fisc (défavorable à
    l'administration, donc à ignorer dans notre logique pro-propriétaire),
    soit une surface fiscale supérieure à ce qui a réellement été autorisé
    et construit (favorable au propriétaire, donc à instruire).
    """
    alertes = []
    if not champs_permis:
        return alertes

    surface_fiscale = champs.get("surface_ponderee_m2", {}).get("valeur_candidate")
    surface_permis = champs_permis.get("surface_ponderee_m2", {}).get("valeur_candidate")

    if surface_fiscale is None or surface_permis is None:
        alertes.append(Alerte(
            type_erreur="permis_donnees_incompletes",
            description="Permis de construire fourni mais surface non lisible automatiquement dedans.",
            confiance="faible_necessite_verification_terrain",
            piece_a_verifier="permis de construire (pièce PCMI ou DP)",
            action_recommandee="Relire manuellement la surface de plancher créée indiquée sur le permis.",
            reference_legale="Article 1406 du CGI (déclaration des éléments d'information nécessaires à l'établissement de l'impôt)",
        ))
        return alertes

    try:
        sf = float(str(surface_fiscale).replace(",", "."))
        sp = float(str(surface_permis).replace(",", "."))
    except ValueError:
        return alertes

    if sf > sp:
        alertes.append(Alerte(
            type_erreur="surface_fiscale_superieure_au_permis",
            description=f"La surface retenue par le fisc ({sf} m²) dépasse la surface autorisée par le "
                        f"permis de construire ({sp} m²).",
            confiance="moyenne",
            piece_a_verifier="permis de construire + fiche d'évaluation cadastrale",
            action_recommandee="Vérifier si la totalité de la surface a bien été construite et si la "
                                "différence ne provient pas d'une erreur de saisie côté administration.",
            reference_legale="Article 1406 du CGI (consistance déclarée) et article 1494 du CGI (évaluation cadastrale)",
        ))
    return alertes


def _plafond_exoneration_age(nb_parts: float) -> int:
    """
    Article 1417-I du CGI — tableau officiel DGFIP (impots.gouv.fr, page
    'Mon âge me permet-il d'être exonéré de taxe foncière ?', mise à jour
    du 20/07/2026) : 12 679€ pour 1 part, +1 693€ par quart de part
    supplémentaire (donc +3 386€ par demi-part). Métropole uniquement.
    """
    quarts_supp = max(0, round((nb_parts - 1) * 4))
    return 12679 + quarts_supp * 1693


def _plafond_plafonnement_revenu(nb_parts: float) -> int:
    """
    Article 1417-II du CGI — tableau officiel du formulaire DGFIP
    2041-DPTF-SD / Cerfa 14770 (impots.gouv.fr) : 29 815€ pour 1 part,
    36 781€ pour 1,5 part (+6 966€, écart réel non lissé), puis +5 484€ par
    demi-part au-delà. Précision au demi-part (pas de quart de part publié
    pour ce dispositif). Métropole uniquement.
    """
    demi_parts_supp = max(0, round((nb_parts - 1) * 2))
    if demi_parts_supp == 0:
        return 29815
    return 29815 + 6966 + max(0, demi_parts_supp - 1) * 5484


def regle_maintien_exoneration(exonerations: Optional[dict] = None) -> list[Alerte]:
    """
    Article 1391-II du CGI (source : impots.gouv.fr, page 'Exonérations et
    dégrèvements', section 'L'exonération spéciale en faveur des personnes
    âgées...'). Dispositif peu connu : un contribuable qui PERD le bénéfice
    de l'exonération (âge 75+, ASPA, ASI, AAH) car son RFR dépasse le
    plafond ne perd pas tout d'un coup. Il conserve l'exonération totale
    pendant les 2 années suivant la perte d'éligibilité, puis bénéficie
    d'un abattement de 2/3 de la valeur locative la 3ème année et de 1/3
    la 4ème année. Ce mécanisme dégressif est fréquemment oublié car il
    suppose de suivre l'historique du dossier sur plusieurs années.
    """
    alertes = []
    if not exonerations:
        return alertes

    annees_depuis_perte = exonerations.get("annees_depuis_perte_eligibilite")
    if annees_depuis_perte is None:
        return alertes

    if annees_depuis_perte in (1, 2):
        alertes.append(Alerte(
            type_erreur="maintien_exoneration_totale",
            description=f"Le client a perdu son éligibilité à l'exonération il y a "
                        f"{annees_depuis_perte} an(s), mais le dispositif de maintien prévoit "
                        "encore une exonération TOTALE pour cette période (2 ans après la perte "
                        "d'éligibilité).",
            confiance="forte",
            piece_a_verifier="avis de taxe foncière de l'année en cours et des années précédentes",
            action_recommandee="Vérifier que l'exonération totale est bien encore appliquée ; "
                                "sinon réclamation avec l'historique du dossier en justificatif.",
            reference_legale="Article 1391-II du CGI (dispositif de maintien de l'exonération)",
        ))
    elif annees_depuis_perte == 3:
        alertes.append(Alerte(
            type_erreur="maintien_exoneration_abattement_deux_tiers",
            description="Le client a perdu son éligibilité à l'exonération il y a 3 ans : un "
                        "abattement des DEUX TIERS de la valeur locative doit s'appliquer cette année.",
            confiance="forte",
            piece_a_verifier="avis de taxe foncière de l'année en cours",
            action_recommandee="Vérifier que l'abattement de 2/3 est bien appliqué sur la valeur "
                                "locative ; c'est un point technique souvent manqué.",
            reference_legale="Article 1391-II du CGI (dispositif de maintien de l'exonération, abattement dégressif)",
        ))
    elif annees_depuis_perte == 4:
        alertes.append(Alerte(
            type_erreur="maintien_exoneration_abattement_un_tiers",
            description="Le client a perdu son éligibilité à l'exonération il y a 4 ans : un "
                        "abattement d'UN TIERS de la valeur locative doit s'appliquer cette année "
                        "(dernière année du dispositif de maintien).",
            confiance="forte",
            piece_a_verifier="avis de taxe foncière de l'année en cours",
            action_recommandee="Vérifier que l'abattement de 1/3 est bien appliqué sur la valeur locative.",
            reference_legale="Article 1391-II du CGI (dispositif de maintien de l'exonération, abattement dégressif)",
        ))

    return alertes


def regle_construction_neuve(annees_depuis_achevement: Optional[float] = None) -> list[Alerte]:
    """
    Article 1383 du CGI (source : bofip.impots.gouv.fr, page 'Exonération
    de droit commun de deux ans') : les constructions nouvelles,
    reconstructions et additions de construction à usage d'habitation sont
    exonérées de taxe foncière durant les deux années qui suivent celle de
    leur achèvement. Condition formelle stricte (article 1406 du CGI) :
    déclaration par le formulaire H1 (n°6650) dans les 90 jours suivant
    l'achèvement, sous peine de perte du bénéfice de l'exonération.

    Cas d'usage typique : le client a construit ou agrandi récemment
    (véranda, extension, garage aménagé, maison neuve) et se demande si
    l'exonération a bien été appliquée sur ses deux premiers avis.
    """
    alertes = []
    if annees_depuis_achevement is None:
        return alertes

    if 0 <= annees_depuis_achevement <= 2:
        alertes.append(Alerte(
            type_erreur="exoneration_construction_neuve_a_verifier",
            description=f"Construction, reconstruction ou extension achevée il y a "
                        f"{annees_depuis_achevement:.1f} an(s) : exonération de 2 ans potentiellement "
                        "applicable (totale pour l'habitation, sauf délibération communale contraire).",
            confiance="moyenne",
            piece_a_verifier="formulaire H1 (n°6650) déposé, avis de taxe foncière des 2 dernières années",
            action_recommandee="Vérifier que le formulaire H1 a bien été déposé dans les 90 jours "
                                "suivant l'achèvement, et que l'exonération apparaît sur les avis "
                                "correspondants. Si le formulaire n'a pas été déposé à temps, "
                                "le bénéfice peut être définitivement perdu — à confirmer au cas par cas.",
            reference_legale="Article 1383 du CGI (exonération de 2 ans) et article 1406 du CGI "
                              "(déclaration H1 dans les 90 jours suivant l'achèvement)",
        ))
    elif 2 < annees_depuis_achevement <= 3:
        alertes.append(Alerte(
            type_erreur="exoneration_construction_neuve_periode_limite",
            description=f"Construction ou extension achevée il y a {annees_depuis_achevement:.1f} an(s) : "
                        "la période d'exonération de 2 ans vient probablement de s'achever, mais mérite "
                        "d'être vérifiée si le formulaire H1 a été déposé tardivement.",
            confiance="faible_necessite_verification_terrain",
            piece_a_verifier="date exacte de dépôt du formulaire H1 et avis de taxe foncière concernés",
            action_recommandee="Vérifier la date de dépôt du H1 par rapport à la date d'achèvement "
                                "réelle, et si les deux années d'exonération ont bien été appliquées "
                                "en intégralité.",
            reference_legale="Article 1383 du CGI et article 1406 du CGI",
        ))
    return alertes


def regle_exonerations(exonerations: Optional[dict] = None) -> list[Alerte]:
    """
    Vérifie l'éligibilité potentielle à plusieurs dispositifs, sur la base de
    réponses déclaratives du client : âge, RFR, nombre de parts fiscales,
    bénéfice de l'ASPA/ASI/AAH, travaux, et montant actuel de la taxe pour
    le plafonnement. Seuils sourcés EXCLUSIVEMENT depuis la DGFIP :
    impots.gouv.fr (pages officielles 'Mon âge me permet-il d'être exonéré
    de taxe foncière ?' et 'Ma taxe foncière est très élevée. Peut-elle être
    plafonnée ?') et le formulaire officiel 2041-DPTF-SD / Cerfa 14770. Ces
    seuils sont revalorisés chaque année : toujours reconfirmer sur
    impots.gouv.fr avant toute annonce définitive à un client.
    """
    alertes = []
    if not exonerations:
        return alertes

    age = exonerations.get("age")
    rfr = exonerations.get("rfr")
    nb_parts = exonerations.get("nb_parts") or 1.0
    plafond_age = _plafond_exoneration_age(nb_parts)

    # --- Dispositif âge (exonération 75 ans+ / dégrèvement 65-74 ans) ---
    if age is not None and age >= 75:
        if rfr is not None:
            if rfr <= plafond_age:
                alertes.append(Alerte(
                    type_erreur="exoneration_age_revenu",
                    description=f"Client âgé de {age} ans, RFR déclaré de {rfr}€, sous le "
                                f"plafond 2026 de {plafond_age}€ pour {nb_parts} part(s) : "
                                "exonération totale probable.",
                    confiance="forte",
                    piece_a_verifier="avis d'impôt sur le revenu 2025 du client (ligne RFR)",
                    action_recommandee="Vérifier que l'exonération est bien déjà appliquée sur "
                                        "l'avis ; sinon réclamation possible jusqu'au 31 décembre "
                                        "de l'année suivante, avec l'avis d'impôt en justificatif.",
                    reference_legale="Article 1391 du CGI et article 1417-I du CGI",
                ))
            else:
                alertes.append(Alerte(
                    type_erreur="exoneration_age_hors_plafond",
                    description=f"Client âgé de {age} ans, mais RFR déclaré de {rfr}€ au-dessus "
                                f"du plafond de {plafond_age}€ : exonération totale probablement "
                                "non applicable. Le plafonnement (voir plus bas) peut encore "
                                "s'appliquer si la résidence est principale.",
                    confiance="moyenne",
                    piece_a_verifier="avis d'impôt sur le revenu 2025 du client",
                    action_recommandee="Vérifier l'éligibilité au plafonnement de la taxe "
                                        "foncière en fonction des revenus (article 1391 B ter).",
                    reference_legale="Article 1391 du CGI et article 1417-I du CGI",
                ))
        else:
            alertes.append(Alerte(
                type_erreur="exoneration_age_a_verifier",
                description=f"Client âgé de {age} ans, mais le revenu fiscal de référence n'a "
                            "pas été renseigné.",
                confiance="faible_necessite_verification_terrain",
                piece_a_verifier="avis d'impôt sur le revenu du client",
                action_recommandee=f"Demander le RFR exact et le comparer au plafond de {plafond_age}€.",
                reference_legale="Article 1391 du CGI et article 1417-I du CGI",
            ))
    elif age is not None and 65 <= age < 75:
        alertes.append(Alerte(
            type_erreur="degrevement_65_74",
            description=f"Client âgé de {age} ans (entre 65 et 74 ans) : dégrèvement forfaitaire "
                        "de 100€ potentiel, sous les mêmes conditions de revenu.",
            confiance="moyenne",
            piece_a_verifier="avis d'impôt sur le revenu du client",
            action_recommandee=f"Vérifier que le RFR est bien sous le plafond de {plafond_age}€.",
            reference_legale="Article 1391 du CGI et article 1417-I du CGI",
        ))

    # --- ASPA / ASI / AAH ---
    if exonerations.get("aspa_asi"):
        alertes.append(Alerte(
            type_erreur="exoneration_aspa_asi",
            description="Client bénéficiaire de l'ASPA ou de l'ASI : exonération totale "
                        "applicable sans condition d'âge ni de RFR distincte.",
            confiance="forte",
            piece_a_verifier="notification d'attribution de l'ASPA ou de l'ASI",
            action_recommandee="Vérifier que l'exonération est bien appliquée sur l'avis ; "
                                "sinon réclamation avec la notification en justificatif.",
            reference_legale="Article 1391 du CGI",
        ))
    if exonerations.get("aah") and (rfr is None or rfr <= plafond_age):
        alertes.append(Alerte(
            type_erreur="exoneration_aah",
            description="Client bénéficiaire de l'AAH, sous le plafond de RFR applicable : "
                        "exonération potentielle.",
            confiance="moyenne" if rfr is not None else "faible_necessite_verification_terrain",
            piece_a_verifier="notification d'attribution de l'AAH et avis d'impôt sur le revenu",
            action_recommandee="Confirmer le RFR exact par rapport au plafond, puis vérifier "
                                "l'application sur l'avis de taxe foncière.",
            reference_legale="Article 1391 du CGI et article 1417-I du CGI",
        ))

    # --- Plafonnement en fonction du revenu (résidence principale uniquement) ---
    montant_tf = exonerations.get("montant_taxe_fonciere")
    residence_principale = exonerations.get("residence_principale")
    if residence_principale and rfr is not None and montant_tf is not None:
        plafond_plafonnement = _plafond_plafonnement_revenu(nb_parts)
        if rfr <= plafond_plafonnement:
            seuil_50pct = rfr * 0.5
            if montant_tf > seuil_50pct:
                degrevement_estime = round(montant_tf - seuil_50pct)
                alertes.append(Alerte(
                    type_erreur="plafonnement_revenu",
                    description=f"Résidence principale, RFR de {rfr}€ sous le plafond de "
                                f"{plafond_plafonnement}€, et taxe foncière ({montant_tf}€) "
                                f"supérieure à 50% du RFR ({seuil_50pct:.0f}€) : dégrèvement "
                                f"estimé à environ {degrevement_estime}€.",
                    confiance="forte",
                    piece_a_verifier="avis d'impôt sur le revenu + avis de taxe foncière + "
                                    "confirmation de non-assujettissement à l'IFI",
                    action_recommandee="Ce dégrèvement n'est jamais automatique : déposer une "
                                        "réclamation avec formulaire dédié (article R.196-2 du "
                                        "Livre des procédures fiscales), joindre les justificatifs.",
                    reference_legale="Article 1391 B ter du CGI et article 1417-II du CGI "
                                      "(formulaire officiel 2041-DPTF-SD / Cerfa 14770)",
                ))

    # --- Travaux d'économie d'énergie ---
    if exonerations.get("travaux_energetiques"):
        alertes.append(Alerte(
            type_erreur="exoneration_travaux",
            description="Travaux d'économie d'énergie déclarés par le client : exonération "
                        "temporaire potentielle.",
            confiance="moyenne",
            piece_a_verifier="factures des travaux et attestation le cas échéant",
            action_recommandee="Vérifier les conditions d'éligibilité et le délai de demande "
                                "(souvent limité dans le temps après la fin des travaux).",
            reference_legale="Article 1383-0 B du CGI",
        ))

    # --- Dispositif de maintien de l'exonération (dégressif sur 4 ans) ---
    alertes += regle_maintien_exoneration(exonerations)

    return alertes


def calculer_montant_recuperable(alertes: list[Alerte], exonerations: Optional[dict], gain_surface: Optional[dict]) -> dict:
    """
    Consolide, à partir des alertes détectées, une estimation du montant
    total que le client pourrait récupérer ou économiser, poste par poste.
    Sert de base au devis. Chaque poste est estimé prudemment et signalé
    comme approximatif : c'est un outil d'aide à la décision, pas un calcul
    officiel de l'administration.

    Important : l'exonération totale (âge 75+, ASPA, ASI, AAH) est un SEUL
    et même dispositif (article 1391 du CGI) — un client éligible par
    plusieurs voies à la fois (ex. 76 ans ET bénéficiaire de l'AAH) n'est
    exonéré qu'UNE fois, pas deux. Ce poste est donc compté une seule fois
    même si plusieurs alertes déclenchent la même exonération.
    """
    postes = []
    exonerations = exonerations or {}
    montant_tf = exonerations.get("montant_taxe_fonciere")

    types_presents = {a.type_erreur for a in alertes}

    types_exoneration_totale_1391 = {"exoneration_age_revenu", "exoneration_aspa_asi", "exoneration_aah"}
    if types_presents & types_exoneration_totale_1391 and montant_tf:
        postes.append({
            "libelle": "Exonération totale (article 1391 du CGI — âge, ASPA, ASI ou AAH)",
            "montant_annuel_estime": montant_tf,
            "recurrent": True,
        })

    if "degrevement_65_74" in types_presents:
        postes.append({"libelle": "Dégrèvement forfaitaire 65-74 ans", "montant_annuel_estime": 100, "recurrent": True})

    if "plafonnement_revenu" in types_presents:
        rfr = exonerations.get("rfr")
        if rfr is not None and montant_tf is not None:
            degrevement_estime = round(montant_tf - rfr * 0.5)
            if degrevement_estime > 0:
                postes.append({
                    "libelle": "Plafonnement en fonction du revenu",
                    "montant_annuel_estime": degrevement_estime,
                    "recurrent": True,
                })

    if "maintien_exoneration_totale" in types_presents and montant_tf:
        postes.append({"libelle": "Maintien de l'exonération totale (2 ans après perte d'éligibilité)", "montant_annuel_estime": montant_tf, "recurrent": True})
    if "maintien_exoneration_abattement_deux_tiers" in types_presents and montant_tf:
        postes.append({"libelle": "Abattement 2/3 (3ème année après perte d'éligibilité)", "montant_annuel_estime": round(montant_tf * 2 / 3), "recurrent": True})
    if "maintien_exoneration_abattement_un_tiers" in types_presents and montant_tf:
        postes.append({"libelle": "Abattement 1/3 (4ème année après perte d'éligibilité)", "montant_annuel_estime": round(montant_tf / 3), "recurrent": True})

    if gain_surface:
        montant_gain_surface = round(gain_surface.get("gain_annuel_taxe_estime_euros", 0))
        if montant_gain_surface > 0:
            postes.append({
                "libelle": "Correction de surface surévaluée",
                "montant_annuel_estime": montant_gain_surface,
                "recurrent": True,
            })

    total = sum(p["montant_annuel_estime"] for p in postes)
    return {
        "postes": postes,
        "total_annuel_estime": total,
        "avertissement": "Montants estimés à partir des informations saisies, à confirmer poste par "
                          "poste avec les avis réels du client avant tout engagement chiffré.",
    }


REGLES_ACTIVES = [regle_surface_ponderee, regle_annee_evaluation, regle_element_disparu,
                   regle_coherence_base_valeur_locative, regle_comparaison_permis_construire,
                   regle_exonerations, regle_construction_neuve]


def executer_moteur(champs: dict, texte_brut: str, surface_reelle_m2: Optional[float] = None,
                     elements_declares_disparus: Optional[list[str]] = None,
                     champs_permis: Optional[dict] = None,
                     exonerations: Optional[dict] = None,
                     annees_depuis_achevement_construction: Optional[float] = None) -> list[Alerte]:
    """Exécute toutes les règles actives et consolide les alertes."""
    champs = dict(champs)
    champs["_texte_brut_pour_recherche"] = texte_brut

    toutes_alertes: list[Alerte] = []
    toutes_alertes += regle_surface_ponderee(champs, surface_reelle_m2)
    toutes_alertes += regle_annee_evaluation(champs)
    toutes_alertes += regle_element_disparu(champs, elements_declares_disparus)
    toutes_alertes += regle_coherence_base_valeur_locative(champs)
    toutes_alertes += regle_comparaison_permis_construire(champs, champs_permis)
    toutes_alertes += regle_exonerations(exonerations)
    toutes_alertes += regle_construction_neuve(annees_depuis_achevement_construction)
    return toutes_alertes
    if surface_fiscale is None:
        alertes.append(Alerte(
            type_erreur="surface_ponderee_illisible",
            description="La surface pondérée n'a pas pu être lue automatiquement dans le document.",
            confiance="faible_necessite_verification_terrain",
            piece_a_verifier="fiche d'évaluation cadastrale",
            action_recommandee="Relire le document manuellement, ou demander une version plus lisible.",
        ))
        return alertes

    if surface_reelle_m2 is None:
        alertes.append(Alerte(
            type_erreur="surface_ponderee_a_confirmer",
            description=f"Surface fiscale trouvée : {surface_fiscale} m². "
                        "Aucune surface réelle de comparaison n'a été fournie.",
            confiance="faible_necessite_verification_terrain",
            piece_a_verifier="mesure réelle du bien (visite ou plan fiable)",
            action_recommandee="Mesurer ou faire confirmer la surface réelle avant de conclure.",
        ))
        return alertes

    try:
        ecart = float(str(surface_fiscale).replace(",", ".")) - float(surface_reelle_m2)
    except ValueError:
        ecart = None

    if ecart is not None and ecart > 0:
        alertes.append(Alerte(
            type_erreur="surface_surevaluee",
            description=f"La surface retenue par le fisc ({surface_fiscale} m²) est supérieure "
                        f"à la surface réelle mesurée ({surface_reelle_m2} m²), écart de {ecart:.1f} m².",
            confiance="forte" if ecart >= 5 else "moyenne",
            piece_a_verifier="fiche d'évaluation cadastrale + mesure réelle",
            action_recommandee="Dossier à instruire : réclamation potentielle en faveur du propriétaire.",
        ))
    return alertes


def regle_annee_evaluation(champs: dict) -> list[Alerte]:
    """
    Une évaluation ancienne, jamais révisée, est un signal statistique
    (30 à 40% des avis contiennent une erreur selon les sources publiques
    déjà citées dans le dossier PDF) — mais ce n'est jamais, à elle seule,
    une preuve d'erreur : elle doit systématiquement déclencher une
    vérification plus poussée plutôt qu'une conclusion automatique.
    """
    alertes = []
    annee = champs.get("annee_evaluation", {}).get("valeur_candidate")
    if annee and annee.isdigit() and int(annee) < 1990:
        alertes.append(Alerte(
            type_erreur="evaluation_ancienne",
            description=f"Évaluation datée de {annee}, jamais révisée depuis longtemps : "
                        "signal statistique fort de risque d'erreur (voir dossier PDF, section 4).",
            confiance="moyenne",
            piece_a_verifier="fiche d'évaluation cadastrale complète",
            action_recommandee="Vérifier en priorité la catégorie de confort et les éléments annexés.",
        ))
    return alertes


def regle_element_disparu(champs: dict, elements_declares_disparus: Optional[list[str]] = None) -> list[Alerte]:
    """
    Vérifie si des éléments que le client déclare avoir supprimés (piscine,
    véranda, dépendance...) apparaissent toujours dans le texte du document.
    Cette règle a volontairement besoin d'une déclaration du client car un
    élément détruit n'est, par nature, pas mesurable sur les seules pièces
    administratives : c'est un cas typique de la fonction de doute.
    """
    alertes = []
    if not elements_declares_disparus:
        return alertes

    texte = champs.get("_texte_brut_pour_recherche", "")
    for element in elements_declares_disparus:
        if element.lower() in texte.lower():
            alertes.append(Alerte(
                type_erreur="element_disparu_toujours_taxe",
                description=f"Le client indique que « {element} » n'existe plus, mais ce terme "
                            "apparaît toujours dans le document fiscal.",
                confiance="faible_necessite_verification_terrain",
                piece_a_verifier="constat visuel sur place ou photo datée",
                action_recommandee="Déplacement recommandé pour constater et documenter la disparition.",
            ))
    return alertes


def regle_coherence_base_valeur_locative(champs: dict) -> list[Alerte]:
    """
    Idée ajoutée : la base d'imposition doit normalement correspondre à
    environ 50% de la valeur locative cadastrale (abattement forfaitaire).
    Si ce ratio est très éloigné de 50%, deux explications possibles :
    soit une vraie anomalie sur le dossier, soit une erreur de lecture du
    document par l'outil lui-même. Dans les deux cas, cela doit remonter
    comme un point à vérifier plutôt que d'être ignoré silencieusement.
    """
    alertes = []
    base = champs.get("base_imposition_euros", {}).get("valeur_candidate")
    valeur_locative = champs.get("valeur_locative_cadastrale", {}).get("valeur_candidate")

    if base is None or valeur_locative is None:
        return alertes

    try:
        base_f = float(str(base).replace(" ", "").replace(",", "."))
        vlc_f = float(str(valeur_locative).replace(" ", "").replace(",", "."))
        if vlc_f == 0:
            return alertes
        ratio = base_f / vlc_f
    except ValueError:
        return alertes

    if not (0.4 <= ratio <= 0.6):
        alertes.append(Alerte(
            type_erreur="incoherence_base_valeur_locative",
            description=f"Le ratio base d'imposition / valeur locative ({ratio:.0%}) s'écarte fortement "
                        "des 50% attendus après abattement forfaitaire.",
            confiance="faible_necessite_verification_terrain",
            piece_a_verifier="avis de taxe foncière complet",
            action_recommandee="Vérifier manuellement ces deux montants : possible erreur de lecture "
                                "automatique OU véritable anomalie du dossier à creuser.",
        ))
    return alertes


def estimer_gain_potentiel(champs: dict, alertes: list[Alerte], surface_reelle_m2=None) -> Optional[dict]:
    """
    Idée ajoutée : donner un ordre de grandeur du gain financier potentiel
    pour le propriétaire, afin de prioriser les dossiers les plus rentables
    à instruire en premier — utile dès qu'il y aura plusieurs clients en
    parallèle.

    Estimation volontairement prudente et approximative : un pourcentage de
    correction de la base d'imposition proportionnel à l'écart de surface,
    appliqué à une base indicative si le taux d'imposition local n'est pas
    connu. À affiner avec de vrais taux communaux du 06 par la suite.
    """
    base = champs.get("base_imposition_euros", {}).get("valeur_candidate")
    surface_fiscale = champs.get("surface_ponderee_m2", {}).get("valeur_candidate")

    if base is None or surface_fiscale is None or surface_reelle_m2 is None:
        return None

    try:
        base_f = float(str(base).replace(" ", "").replace(",", "."))
        surface_fiscale_f = float(str(surface_fiscale).replace(",", "."))
    except ValueError:
        return None

    if surface_fiscale_f == 0:
        return None

    ecart_relatif = (surface_fiscale_f - surface_reelle_m2) / surface_fiscale_f
    if ecart_relatif <= 0:
        return None

    base_corrigee_estimee = base_f * (1 - ecart_relatif)
    gain_base_estime = base_f - base_corrigee_estimee

    # Taux moyen indicatif communes du 06 (à remplacer par le taux réel dès qu'il est connu)
    taux_moyen_indicatif = 0.40  # 40%, ordre de grandeur, PAS une valeur officielle
    gain_annuel_estime_euros = gain_base_estime * taux_moyen_indicatif

    return {
        "gain_base_imposition_estime_euros": round(gain_base_estime, 0),
        "gain_annuel_taxe_estime_euros": round(gain_annuel_estime_euros, 0),
        "avertissement": "Estimation approximative avec un taux indicatif (40%) — à remplacer par le "
                          "taux communal réel du bien avant toute annonce chiffrée au client.",
    }


def regle_comparaison_permis_construire(champs: dict, champs_permis: Optional[dict] = None) -> list[Alerte]:
    """
    Compare la surface retenue par le fisc à la surface déclarée sur le permis
    de construire du bien, quand ce document est disponible. Un écart peut
    signaler soit une extension non déclarée au fisc (défavorable à
    l'administration, donc à ignorer dans notre logique pro-propriétaire),
    soit une surface fiscale supérieure à ce qui a réellement été autorisé
    et construit (favorable au propriétaire, donc à instruire).
    """
    alertes = []
    if not champs_permis:
        return alertes

    surface_fiscale = champs.get("surface_ponderee_m2", {}).get("valeur_candidate")
    surface_permis = champs_permis.get("surface_ponderee_m2", {}).get("valeur_candidate")

    if surface_fiscale is None or surface_permis is None:
        alertes.append(Alerte(
            type_erreur="permis_donnees_incompletes",
            description="Permis de construire fourni mais surface non lisible automatiquement dedans.",
            confiance="faible_necessite_verification_terrain",
            piece_a_verifier="permis de construire (pièce PCMI ou DP)",
            action_recommandee="Relire manuellement la surface de plancher créée indiquée sur le permis.",
        ))
        return alertes

    try:
        sf = float(str(surface_fiscale).replace(",", "."))
        sp = float(str(surface_permis).replace(",", "."))
    except ValueError:
        return alertes

    if sf > sp:
        alertes.append(Alerte(
            type_erreur="surface_fiscale_superieure_au_permis",
            description=f"La surface retenue par le fisc ({sf} m²) dépasse la surface autorisée par le "
                        f"permis de construire ({sp} m²).",
            confiance="moyenne",
            piece_a_verifier="permis de construire + fiche d'évaluation cadastrale",
            action_recommandee="Vérifier si la totalité de la surface a bien été construite et si la "
                                "différence ne provient pas d'une erreur de saisie côté administration.",
        ))
    return alertes


REGLES_ACTIVES = [regle_surface_ponderee, regle_annee_evaluation, regle_element_disparu,
                   regle_coherence_base_valeur_locative, regle_comparaison_permis_construire]


def executer_moteur(champs: dict, texte_brut: str, surface_reelle_m2: Optional[float] = None,
                     elements_declares_disparus: Optional[list[str]] = None,
                     champs_permis: Optional[dict] = None) -> list[Alerte]:
    """Exécute toutes les règles actives et consolide les alertes."""
    champs = dict(champs)
    champs["_texte_brut_pour_recherche"] = texte_brut

    toutes_alertes: list[Alerte] = []
    toutes_alertes += regle_surface_ponderee(champs, surface_reelle_m2)
    toutes_alertes += regle_annee_evaluation(champs)
    toutes_alertes += regle_element_disparu(champs, elements_declares_disparus)
    toutes_alertes += regle_coherence_base_valeur_locative(champs)
    toutes_alertes += regle_comparaison_permis_construire(champs, champs_permis)
    return toutes_alertes
