# -*- coding: utf-8 -*-
"""
Brique 1 : extraction de texte et de champs depuis les documents du dossier
(avis de taxe foncière, fiche d'évaluation cadastrale, permis de construire).

Usage :
    python3 extraction.py mon_avis.pdf

Ce module ne fait volontairement AUCUNE analyse d'erreur : il se contente de
lire le document et d'en extraire un texte brut + quelques champs candidats
repérés par motifs (regex). L'objectif est de poser une fondation fiable,
avant de brancher dessus un moteur de règles (brique 2) puis une fonction
de doute (brique 3).
"""

import re
import sys
import json
from pathlib import Path

import pdfplumber
import requests


def _extraction_est_pauvre(texte: str) -> bool:
    """
    Idée ajoutée : beaucoup de fiches d'évaluation cadastrale sont des scans
    (documents anciens, envoyés par le centre des impôts fonciers par courrier
    puis numérisés). Sur un scan, l'extraction de texte native renvoie souvent
    presque rien. On détecte ce cas pour basculer automatiquement vers l'OCR
    plutôt que de renvoyer un rapport vide en silence.
    """
    return len(texte.strip()) < 50


def _extraire_texte_par_ocr(pdf_path: str) -> str:
    """
    Bascule OCR : convertit chaque page en image puis lit le texte avec
    Tesseract. Plus lent que l'extraction native, mais indispensable pour
    les documents scannés. Nécessite : pip install pytesseract pdf2image
    (+ poppler et tesseract installés sur la machine).
    """
    try:
        import pytesseract
        from pdf2image import convert_from_path
    except ImportError:
        return ""  # OCR non disponible sur cette machine : on le signale plus haut

    images = convert_from_path(pdf_path)
    textes = [pytesseract.image_to_string(img, lang="fra") for img in images]
    return "\n".join(textes)


def extraire_texte(pdf_path: str) -> tuple[str, str]:
    """
    Extrait tout le texte brut d'un PDF, page par page.
    Retourne (texte, methode) où methode vaut "natif" ou "ocr" ou "echec_ocr_indisponible",
    pour que la fonction de doute sache si le texte source est fiable.
    """
    texte_complet = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            texte_page = page.extract_text() or ""
            texte_complet.append(texte_page)
    texte = "\n".join(texte_complet)

    if _extraction_est_pauvre(texte):
        texte_ocr = _extraire_texte_par_ocr(pdf_path)
        if texte_ocr.strip():
            return texte_ocr, "ocr"
        return texte, "echec_ocr_indisponible"

    return texte, "natif"


# Motifs (regex) pour repérer les champs clés d'un document fiscal français.
# Chaque motif est volontairement permissif : le but est de proposer une
# valeur candidate, jamais de trancher seul. C'est le rôle de la brique
# "fonction de doute" de dire si on peut faire confiance à ce qui a été
# trouvé ici ou s'il faut une vérification humaine.
MOTIFS_CHAMPS = {
    "surface_ponderee_m2": r"[Ss]urface[s]?\s+pond[ée]r[ée]e?s?\D{0,15}(\d{1,4}(?:[.,]\d+)?)\s*m[²2]?",
    "categorie_confort": r"[Cc]at[ée]gorie\D{0,10}([1-8]|I{1,3}|IV|V|VI{0,2})",
    "base_imposition_euros": r"[Bb]ase\D{0,15}(\d{1,3}(?:[ .]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)\s*(?:€|EUR)?",
    "valeur_locative_cadastrale": r"[Vv]aleur\s+locative\D{0,20}(\d{1,3}(?:[ .]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)\s*€?",
    "annee_evaluation": r"(?:[ée]valu[ée]e?s?\s+en|r[ée]f[ée]rence)\D{0,10}(19[5-9]\d|20[0-2]\d)",
    "adresse_bien": r"(?:[Aa]dresse\s+du\s+bien|[Ss]itu[ée])\D{0,5}[:\-]?\s*(.{5,80})",
}


def extraire_champs_candidats(texte: str) -> dict:
    """
    Recherche des champs candidats dans le texte brut, via motifs.
    Chaque champ renvoyé porte aussi un niveau de confiance grossier,
    pour être exploité ensuite par la fonction de doute (brique 3).
    """
    resultats = {}
    for nom_champ, motif in MOTIFS_CHAMPS.items():
        match = re.search(motif, texte, flags=re.IGNORECASE)
        if match:
            resultats[nom_champ] = {
                "valeur_candidate": match.group(1).strip(),
                "confiance": "a_verifier",  # jamais "certaine" à ce stade : voir brique 3
            }
        else:
            resultats[nom_champ] = {
                "valeur_candidate": None,
                "confiance": "absente",
            }
    return resultats


def analyser_document(pdf_path: str) -> dict:
    """Point d'entrée principal : lit un PDF et renvoie texte + champs candidats."""
    texte, methode_extraction = extraire_texte(pdf_path)
    champs = extraire_champs_candidats(texte)
    return {
        "fichier": Path(pdf_path).name,
        "methode_extraction": methode_extraction,  # "natif", "ocr", ou "echec_ocr_indisponible"
        "nb_caracteres_extraits": len(texte),
        "champs_candidats": champs,
        "texte_brut": texte,
    }


# ---------------------------------------------------------------------------
# Brique 1 bis : lecture assistée par IA, en complément des motifs (regex).
#
# Les motifs ci-dessus cassent dès qu'une commune ou un centre des impôts
# présente son document avec une formulation différente ("Surf. pond." au
# lieu de "Surface pondérée", tableau au lieu de texte suivi, etc.). L'IA
# comprend le sens du document plutôt qu'un motif figé, donc elle est bien
# plus robuste aux variations de mise en page — au prix d'un appel réseau
# payant (quelques centimes par document) et d'une clé API à configurer.
#
# Cette brique reste OPTIONNELLE : sans clé API configurée, l'outil continue
# de fonctionner normalement avec les motifs seuls.
# ---------------------------------------------------------------------------

CHAMPS_ATTENDUS = list(MOTIFS_CHAMPS.keys())

PROMPT_EXTRACTION_IA = """Tu es un assistant d'extraction de données pour un géomètre-topographe qui \
analyse des documents fiscaux français (avis de taxe foncière, fiche d'évaluation cadastrale).

Voici le texte brut extrait d'un document (peut contenir des erreurs de lecture OCR) :

---
{texte}
---

Extrait les champs suivants s'ils sont présents dans le texte, même si le libellé exact varie \
(par exemple "Surf. pondérée", "SP", ou un tableau) :
- surface_ponderee_m2 : la surface pondérée du local, en m² (nombre seul)
- categorie_confort : la catégorie de confort du local (1 à 8, ou chiffre romain)
- base_imposition_euros : la base d'imposition, en euros (nombre seul)
- valeur_locative_cadastrale : la valeur locative cadastrale, en euros (nombre seul)
- annee_evaluation : l'année de référence de l'évaluation cadastrale (4 chiffres)
- adresse_bien : l'adresse du bien concerné

Réponds UNIQUEMENT avec un objet JSON valide, sans texte avant ni après, sous cette forme exacte :
{{"surface_ponderee_m2": {{"valeur": "...", "confiance": "haute|moyenne|basse"}}, ...}}
Si un champ n'est pas présent dans le texte, mets "valeur": null et "confiance": "absente".
La confiance reflète ta certitude sur la lecture : "haute" si le champ est explicite et non ambigu, \
"basse" si tu dois déduire ou si le texte est confus."""


def extraire_champs_via_ia(texte_brut: str, cle_api: str, modele: str = "claude-haiku-4-5-20251001") -> dict:
    """
    Envoie le texte du document à l'API Claude pour une extraction plus
    robuste que les motifs (regex) seuls. Retourne un dict au même format
    que extraire_champs_candidats, avec une clé "source": "ia" ajoutée sur
    chaque champ pour tracer d'où vient chaque valeur dans le rapport final.

    Coût : de l'ordre de quelques centimes par document avec Claude Haiku,
    modèle volontairement choisi pour ce type de tâche répétitive et
    structurée plutôt qu'un modèle plus cher.
    """
    if not cle_api:
        raise ValueError("Aucune clé API fournie — configure ANTHROPIC_API_KEY dans les secrets.")

    # Le texte est tronqué pour rester raisonnable en coût et en latence ;
    # les champs recherchés se trouvent presque toujours dans les premiers
    # milliers de caractères d'un avis ou d'une fiche d'évaluation.
    texte_tronque = texte_brut[:8000]

    reponse = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": cle_api,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": modele,
            "max_tokens": 1000,
            "messages": [{"role": "user", "content": PROMPT_EXTRACTION_IA.format(texte=texte_tronque)}],
        },
        timeout=30,
    )
    reponse.raise_for_status()
    contenu = reponse.json()["content"][0]["text"].strip()

    # Nettoyage au cas où le modèle ajoute des balises markdown malgré la consigne
    if contenu.startswith("```"):
        contenu = contenu.split("```")[1]
        if contenu.startswith("json"):
            contenu = contenu[4:]
    contenu = contenu.strip()

    donnees_ia = json.loads(contenu)

    resultats = {}
    for champ in CHAMPS_ATTENDUS:
        info = donnees_ia.get(champ, {})
        resultats[champ] = {
            "valeur_candidate": info.get("valeur"),
            "confiance": info.get("confiance", "absente"),
            "source": "ia",
        }
    return resultats


def fusionner_extractions(champs_regex: dict, champs_ia: dict) -> dict:
    """
    Combine les résultats des motifs (regex) et de l'IA, champ par champ.
    Logique de fusion :
      - Si les deux sources sont d'accord sur la valeur → confiance forte,
        c'est le signal le plus fiable possible (double vérification).
      - Si seule une source a trouvé quelque chose → on la garde, avec sa
        confiance d'origine.
      - Si les deux sources se contredisent → on garde les deux valeurs
        visibles et on marque le champ "a_verifier" avec un niveau de
        confiance abaissé : c'est un cas typique pour la fonction de doute.
    """
    resultats = {}
    tous_les_champs = set(champs_regex.keys()) | set(champs_ia.keys())

    for champ in tous_les_champs:
        val_regex = (champs_regex.get(champ) or {}).get("valeur_candidate")
        val_ia = (champs_ia.get(champ) or {}).get("valeur_candidate")

        val_regex_norm = str(val_regex).strip().replace(",", ".") if val_regex else None
        val_ia_norm = str(val_ia).strip().replace(",", ".") if val_ia else None

        if val_regex_norm and val_ia_norm and val_regex_norm == val_ia_norm:
            resultats[champ] = {"valeur_candidate": val_regex, "confiance": "confirmee_double_lecture", "source": "regex+ia"}
        elif val_regex_norm and val_ia_norm and val_regex_norm != val_ia_norm:
            resultats[champ] = {
                "valeur_candidate": val_regex,
                "valeur_alternative_ia": val_ia,
                "confiance": "desaccord_entre_lectures",
                "source": "regex+ia",
            }
        elif val_regex_norm:
            resultats[champ] = {**champs_regex[champ], "source": "regex"}
        elif val_ia_norm:
            resultats[champ] = champs_ia[champ]
        else:
            resultats[champ] = {"valeur_candidate": None, "confiance": "absente", "source": "aucune"}

    return resultats


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage : python3 extraction.py chemin_vers_document.pdf")
        sys.exit(1)

    resultat = analyser_document(sys.argv[1])

    # Affichage lisible, sans le texte brut complet (trop long pour la console)
    resume = {k: v for k, v in resultat.items() if k != "texte_brut"}
    print(json.dumps(resume, indent=2, ensure_ascii=False))
    if resultat["methode_extraction"] == "echec_ocr_indisponible":
        print("\n⚠ Document probablement scanné et OCR indisponible sur cette machine.")
        print("   Installer : pip install pytesseract pdf2image --break-system-packages")
        print("   (+ tesseract-ocr et poppler-utils au niveau système)")

    # Sauvegarde complète (avec texte brut) pour inspection / réutilisation
    sortie = Path(sys.argv[1]).with_suffix(".extraction.json")
    with open(sortie, "w", encoding="utf-8") as f:
        json.dump(resultat, f, indent=2, ensure_ascii=False)
    print(f"\nRésultat complet sauvegardé dans : {sortie}")
