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
