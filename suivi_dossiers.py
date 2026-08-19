# -*- coding: utf-8 -*-
"""
Brique 4 (ajoutée) : journal des dossiers analysés.

Idée : sans mémoire, l'outil repart de zéro à chaque dossier. Ce module
garde une trace simple de chaque analyse (fichier, alertes trouvées,
conclusion) dans un fichier local. Objectifs :
  - Suivre au fil du temps la fréquence réelle de chaque type d'erreur
    (utile pour prioriser quelles règles enrichir en premier).
  - Distinguer, une fois les résultats réels connus (dossier gagné, perdu,
    ou jamais déposé), les cas où le prototype avait raison ou tort —
    première brique vers une vraie amélioration continue, sans pour
    autant prétendre faire du machine learning à ce stade.

Fichier de stockage : historique_dossiers.json (créé automatiquement).
"""

import json
from pathlib import Path
from datetime import datetime

FICHIER_HISTORIQUE = Path(__file__).parent / "historique_dossiers.json"


def charger_historique() -> list[dict]:
    if FICHIER_HISTORIQUE.exists():
        with open(FICHIER_HISTORIQUE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def enregistrer_dossier(rapport: dict, resultat_reel: str = "en_attente") -> None:
    """
    resultat_reel : "en_attente" (pas encore su), "gagne", "perdu",
    "non_depose". À mettre à jour manuellement une fois la réponse de
    l'administration connue (6 mois plus tard en moyenne, voir dossier PDF).
    """
    historique = charger_historique()
    historique.append({
        "date_analyse": datetime.now().isoformat(timespec="seconds"),
        "fichier": rapport.get("fichier"),
        "conclusion_fonction_de_doute": rapport.get("conclusion_fonction_de_doute"),
        "nb_alertes": len(rapport.get("alertes", [])),
        "types_alertes": [a["type_erreur"] for a in rapport.get("alertes", [])],
        "resultat_reel": resultat_reel,
    })
    with open(FICHIER_HISTORIQUE, "w", encoding="utf-8") as f:
        json.dump(historique, f, indent=2, ensure_ascii=False)


def statistiques() -> dict:
    """Petit tableau de bord basique, à consulter régulièrement."""
    historique = charger_historique()
    if not historique:
        return {"message": "Aucun dossier enregistré pour l'instant."}

    nb_total = len(historique)
    nb_gagnes = sum(1 for d in historique if d["resultat_reel"] == "gagne")
    nb_perdus = sum(1 for d in historique if d["resultat_reel"] == "perdu")
    nb_en_attente = sum(1 for d in historique if d["resultat_reel"] == "en_attente")

    frequence_types = {}
    for d in historique:
        for t in d["types_alertes"]:
            frequence_types[t] = frequence_types.get(t, 0) + 1

    return {
        "nb_dossiers_total": nb_total,
        "nb_gagnes": nb_gagnes,
        "nb_perdus": nb_perdus,
        "nb_en_attente": nb_en_attente,
        "taux_reussite_sur_dossiers_connus": round(nb_gagnes / (nb_gagnes + nb_perdus), 2) if (nb_gagnes + nb_perdus) else None,
        "frequence_par_type_erreur": dict(sorted(frequence_types.items(), key=lambda x: -x[1])),
    }


if __name__ == "__main__":
    stats = statistiques()
    print(json.dumps(stats, indent=2, ensure_ascii=False))
