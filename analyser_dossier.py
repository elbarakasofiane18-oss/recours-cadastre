# -*- coding: utf-8 -*-
"""
Brique 3 : orchestration + fonction de doute.

Script principal : prend un document, l'extrait, le passe dans le moteur de
règles, puis produit un rapport de synthèse avec une conclusion claire sur
le niveau de confiance global du dossier.

Usage minimal :
    python3 analyser_dossier.py mon_avis.pdf

Usage avec informations complémentaires (surface réelle connue, éléments
que le client déclare disparus) :
    python3 analyser_dossier.py mon_avis.pdf --surface-reelle 85 --disparu piscine véranda
"""

import argparse
import json
from pathlib import Path

from extraction import analyser_document
from moteur_regles import executer_moteur, Alerte, estimer_gain_potentiel
from suivi_dossiers import enregistrer_dossier


def niveau_doute_global(alertes: list[Alerte]) -> str:
    """
    Fonction de doute : détermine si le dossier peut être conclu depuis le
    bureau, ou s'il nécessite une vérification humaine / un déplacement
    sur le terrain avant d'aller plus loin.
    """
    if not alertes:
        return "AUCUNE ALERTE — probablement rien à réclamer sur les points vérifiés automatiquement."

    y_a_t_il_doute = any(a.confiance == "faible_necessite_verification_terrain" for a in alertes)
    y_a_t_il_forte = any(a.confiance == "forte" for a in alertes)

    if y_a_t_il_doute:
        return ("DOUTE — au moins un point nécessite une vérification humaine, "
                "éventuellement un déplacement sur le terrain, avant de conclure.")
    if y_a_t_il_forte:
        return "CONFIANCE ELEVEE — dossier probablement solide, à instruire pour réclamation."
    return "A AFFINER — signaux présents mais pas assez forts pour conclure seul."


def score_confiance_global(alertes: list[Alerte], methode_extraction: str) -> int:
    """
    Idée ajoutée : un score chiffré (0 à 100) est plus facile à trier et à
    prioriser qu'une simple étiquette textuelle, surtout dès qu'il y aura
    plusieurs dossiers en parallèle. Ce score reste indicatif — il vient en
    complément de la fonction de doute, jamais à sa place.
    """
    score = 70  # score de départ neutre

    if methode_extraction == "ocr":
        score -= 15  # l'OCR est moins fiable que le texte natif
    elif methode_extraction == "echec_ocr_indisponible":
        score -= 40  # quasiment aucune confiance possible sans texte exploitable

    for a in alertes:
        if a.confiance == "forte":
            score += 10
        elif a.confiance == "moyenne":
            score += 3
        elif a.confiance == "faible_necessite_verification_terrain":
            score -= 5  # chaque doute non résolu abaisse la confiance globale

    return max(0, min(100, score))


def generer_rapport(pdf_path: str, surface_reelle_m2=None, elements_disparus=None) -> dict:
    extraction = analyser_document(pdf_path)
    alertes = executer_moteur(
        champs=extraction["champs_candidats"],
        texte_brut=extraction["texte_brut"],
        surface_reelle_m2=surface_reelle_m2,
        elements_declares_disparus=elements_disparus,
    )
    gain_estime = estimer_gain_potentiel(extraction["champs_candidats"], alertes, surface_reelle_m2)

    rapport = {
        "fichier": extraction["fichier"],
        "methode_extraction": extraction["methode_extraction"],
        "champs_extraits": extraction["champs_candidats"],
        "alertes": [vars(a) for a in alertes],
        "score_confiance_0_100": score_confiance_global(alertes, extraction["methode_extraction"]),
        "conclusion_fonction_de_doute": niveau_doute_global(alertes),
        "gain_potentiel_estime": gain_estime,
    }
    return rapport


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyse un document fiscal et applique la fonction de doute.")
    parser.add_argument("pdf", help="Chemin vers le PDF à analyser")
    parser.add_argument("--surface-reelle", type=float, default=None,
                         help="Surface réelle mesurée du bien, en m² (si connue)")
    parser.add_argument("--disparu", nargs="*", default=None,
                         help="Éléments que le client déclare avoir supprimés (ex: piscine véranda)")
    parser.add_argument("--no-journal", action="store_true",
                         help="Ne pas enregistrer ce dossier dans le journal historique")
    args = parser.parse_args()

    rapport = generer_rapport(args.pdf, args.surface_reelle, args.disparu)

    print("\n===== RAPPORT D'ANALYSE =====\n")
    print(json.dumps(rapport, indent=2, ensure_ascii=False))

    sortie = Path(args.pdf).with_suffix(".rapport.json")
    with open(sortie, "w", encoding="utf-8") as f:
        json.dump(rapport, f, indent=2, ensure_ascii=False)
    print(f"\nRapport sauvegardé dans : {sortie}")

    if not args.no_journal:
        enregistrer_dossier(rapport)
        print("Dossier ajouté au journal historique (historique_dossiers.json).")
