# -*- coding: utf-8 -*-
"""
Brique 5 : base de clients persistante.

Contrairement au journal historique_dossiers.json (qui ne garde que des
compteurs statistiques), ce module garde le dossier COMPLET de chaque
client : ses chiffres, les alertes détectées, le devis, le statut, et la
date d'envoi de sa réclamation — pour pouvoir les retrouver et les suivre
dans le temps, notamment leurs délais de réponse.

Important (limite technique à connaître) : sur Streamlit Community Cloud,
ce fichier vit sur le disque de l'application. Il survit tant que
l'application ne redémarre pas, mais peut être réinitialisé lors d'un
redéploiement. Pense à exporter régulièrement une sauvegarde (bouton dédié
dans l'onglet « Mes clients ») pour ne jamais perdre de données.
"""

import json
import time
import random
from pathlib import Path
from datetime import datetime, date

FICHIER_CLIENTS = Path(__file__).parent / "clients_enregistres.json"

STATUTS_POSSIBLES = ["en_attente", "gagne", "perdu", "non_depose"]


def _nouvel_id() -> str:
    return f"c_{int(time.time())}_{random.randint(1000, 9999)}"


def charger_clients() -> list[dict]:
    if FICHIER_CLIENTS.exists():
        with open(FICHIER_CLIENTS, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _sauvegarder_tout(clients: list[dict]) -> None:
    with open(FICHIER_CLIENTS, "w", encoding="utf-8") as f:
        json.dump(clients, f, indent=2, ensure_ascii=False)


def enregistrer_nouveau_client(nom: str, adresse: str, alertes_resume: list[dict],
                                total_annuel_estime: float, score: int, conclusion: str,
                                documents_recus: dict = None) -> str:
    """Ajoute un nouveau client à la base et renvoie son identifiant."""
    clients = charger_clients()
    client_id = _nouvel_id()
    clients.append({
        "id": client_id,
        "nom": nom or "Sans nom",
        "adresse": adresse or "",
        "date_creation": datetime.now().isoformat(timespec="seconds"),
        "alertes_resume": alertes_resume,  # liste de {type_erreur, confiance, reference_legale}
        "total_annuel_estime": total_annuel_estime,
        "score": score,
        "conclusion": conclusion,
        "statut": "en_attente",
        "date_envoi_reclamation": "",
        "documents_recus": documents_recus or {},
    })
    _sauvegarder_tout(clients)
    return client_id


def mettre_a_jour_client(client_id: str, statut: str = None, date_envoi_reclamation: str = None) -> None:
    clients = charger_clients()
    for c in clients:
        if c["id"] == client_id:
            if statut is not None:
                c["statut"] = statut
            if date_envoi_reclamation is not None:
                c["date_envoi_reclamation"] = date_envoi_reclamation
    _sauvegarder_tout(clients)


def supprimer_client(client_id: str) -> None:
    clients = charger_clients()
    clients = [c for c in clients if c["id"] != client_id]
    _sauvegarder_tout(clients)


def calculer_delai(date_envoi_str: str, delai_mois: int = 6) -> dict:
    """
    Calcule l'échéance de réponse de l'administration (délai_mois après
    l'envoi de la réclamation) et le nombre de jours restants.
    Renvoie None si aucune date d'envoi n'est renseignée.
    """
    if not date_envoi_str:
        return None
    try:
        envoi = datetime.strptime(date_envoi_str, "%Y-%m-%d").date()
    except ValueError:
        return None

    # Ajout du délai en mois sans dépendance externe (dateutil non garanti installé)
    mois_total = envoi.month - 1 + delai_mois
    annee = envoi.year + mois_total // 12
    mois = mois_total % 12 + 1
    jour = min(envoi.day, 28)  # simplification sûre pour éviter les erreurs de fin de mois
    echeance = date(annee, mois, jour)

    jours_restants = (echeance - date.today()).days
    return {
        "date_echeance": echeance.strftime("%d/%m/%Y"),
        "jours_restants": jours_restants,
        "depasse": jours_restants < 0,
        "urgent": 0 <= jours_restants <= 30,
    }


def statistiques_portefeuille() -> dict:
    clients = charger_clients()
    total_estime = sum(c.get("total_annuel_estime", 0) for c in clients)
    nb_gagnes = sum(1 for c in clients if c["statut"] == "gagne")
    nb_perdus = sum(1 for c in clients if c["statut"] == "perdu")
    nb_en_attente = sum(1 for c in clients if c["statut"] == "en_attente")
    return {
        "nb_clients": len(clients),
        "total_annuel_estime_portefeuille": total_estime,
        "nb_gagnes": nb_gagnes,
        "nb_perdus": nb_perdus,
        "nb_en_attente": nb_en_attente,
    }
