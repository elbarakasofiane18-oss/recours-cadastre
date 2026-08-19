# -*- coding: utf-8 -*-
"""
Application locale avec import de fichiers réels.

Contrairement à l'outil cliquable dans le navigateur (qui ne peut pas lire
de vrais PDF), cette application tourne SUR TON ORDINATEUR et peut donc :
  - recevoir les fichiers envoyés par le client (avis de taxe foncière,
    fiche d'évaluation cadastrale, permis de construire),
  - les lire réellement (texte natif ou scan via OCR),
  - comparer les trois documents entre eux,
  - appliquer les règles fiscales stockées dans regles_fiscales.json,
  - générer le rapport et le courrier de réclamation.

INSTALLATION (une seule fois, dans un terminal) :
    pip install streamlit pdfplumber reportlab --break-system-packages
    # Pour l'OCR (recommandé, pour les documents scannés) :
    pip install pytesseract pdf2image --break-system-packages
    # + au niveau système : tesseract-ocr et poppler-utils

LANCEMENT :
    streamlit run app_local.py

Cela ouvre l'application dans ton navigateur, à l'adresse locale
http://localhost:8501 — tout reste sur ta machine, rien n'est envoyé
ailleurs.
"""

import json
import tempfile
from pathlib import Path

import streamlit as st

from extraction import analyser_document
from moteur_regles import executer_moteur
from suivi_dossiers import enregistrer_dossier, charger_historique, statistiques
from analyser_dossier import niveau_doute_global, score_confiance_global
from moteur_regles import estimer_gain_potentiel

DOSSIER_COURANT = Path(__file__).parent
FICHIER_REGLES = DOSSIER_COURANT / "regles_fiscales.json"


@st.cache_data
def charger_regles():
    with open(FICHIER_REGLES, "r", encoding="utf-8") as f:
        return json.load(f)


def sauver_pdf_temporaire(fichier_uploade) -> str:
    """Écrit le fichier uploadé sur disque temporairement pour pdfplumber."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(fichier_uploade.read())
        return tmp.name


def genererCourrier(nom_client, adresse, alertes):
    from datetime import date
    motifs = "\n".join(f"{i+1}. {a.description}" for i, a in enumerate(alertes))
    return f"""{nom_client or "[Nom du propriétaire]"}
{adresse or "[Adresse du bien]"}

À l'attention du Centre des Finances Publiques
Objet : Réclamation contentieuse - Taxe foncière - {adresse or "[adresse du bien]"}

Fait le {date.today().strftime('%d/%m/%Y')}

Madame, Monsieur,

Je conteste par la présente l'évaluation retenue pour le calcul de ma taxe foncière, pour les motifs suivants :

{motifs or "[Motifs à détailler après analyse]"}

Je vous prie de bien vouloir procéder à la révision de mon dossier.

Je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations distinguées.

{nom_client or "[Nom du propriétaire]"}

---
[Modèle généré automatiquement — à relire et compléter avant envoi.]
"""


st.set_page_config(page_title="Recours Cadastre — 06", layout="wide")

regles = charger_regles()

st.title("Recours Cadastre — analyse d'un dossier")
st.caption("06 · Taxe foncière · Application locale — les fichiers restent sur cet ordinateur")

with st.sidebar:
    st.subheader("Statistiques du cabinet")
    stats = statistiques()
    if "message" in stats:
        st.caption(stats["message"])
    else:
        st.metric("Dossiers analysés", stats["nb_dossiers_total"])
        st.metric("Gagnés / perdus", f'{stats["nb_gagnes"]} / {stats["nb_perdus"]}')
        if stats["taux_reussite_sur_dossiers_connus"] is not None:
            st.metric("Taux de réussite", f'{stats["taux_reussite_sur_dossiers_connus"]:.0%}')
    st.divider()
    st.caption("Règles fiscales chargées depuis regles_fiscales.json — modifiable sans toucher au code.")
    st.caption(f"Délai de réponse administration : {regles['delai_reponse_administration_mois']} mois")

col_gauche, col_droite = st.columns([1, 1])

with col_gauche:
    st.subheader("1. Identité du client")
    nom_client = st.text_input("Nom du client")
    adresse = st.text_input("Adresse du bien")

    st.subheader("2. Pièces du dossier")
    fichier_avis = st.file_uploader("Avis de taxe foncière (PDF)", type="pdf", key="avis")
    fichier_fiche = st.file_uploader("Fiche d'évaluation cadastrale (PDF)", type="pdf", key="fiche")
    fichier_permis = st.file_uploader("Permis de construire, si disponible (PDF)", type="pdf", key="permis")

    surface_reelle = st.number_input("Surface réelle mesurée (m²), si connue", min_value=0.0, value=0.0, step=1.0)
    elements_disparus_texte = st.text_input("Éléments déclarés disparus par le client (séparés par des virgules)")
    elements_disparus = [e.strip() for e in elements_disparus_texte.split(",") if e.strip()]

    lancer = st.button("Lancer l'analyse", type="primary", use_container_width=True)

with col_droite:
    st.subheader("3. Résultat")

    if lancer:
        if not fichier_fiche and not fichier_avis:
            st.error("Merci de déposer au moins l'avis de taxe foncière ou la fiche d'évaluation.")
        else:
            with st.spinner("Lecture des documents en cours (OCR si nécessaire)..."):
                doc_principal = fichier_fiche or fichier_avis
                chemin_principal = sauver_pdf_temporaire(doc_principal)
                extraction_principale = analyser_document(chemin_principal)

                champs_permis = None
                if fichier_permis:
                    chemin_permis = sauver_pdf_temporaire(fichier_permis)
                    extraction_permis = analyser_document(chemin_permis)
                    champs_permis = extraction_permis["champs_candidats"]

                alertes = executer_moteur(
                    champs=extraction_principale["champs_candidats"],
                    texte_brut=extraction_principale["texte_brut"],
                    surface_reelle_m2=surface_reelle if surface_reelle > 0 else None,
                    elements_declares_disparus=elements_disparus,
                    champs_permis=champs_permis,
                )
                gain = estimer_gain_potentiel(
                    extraction_principale["champs_candidats"], alertes,
                    surface_reelle if surface_reelle > 0 else None
                )
                score = score_confiance_global(alertes, extraction_principale["methode_extraction"])
                conclusion = niveau_doute_global(alertes)

            if extraction_principale["methode_extraction"] == "ocr":
                st.info("Document lu par reconnaissance de caractères (OCR) — vérifier les champs extraits.")
            elif extraction_principale["methode_extraction"] == "echec_ocr_indisponible":
                st.warning("Document probablement scanné et OCR indisponible sur cette machine. "
                           "Installer pytesseract et pdf2image pour l'activer.")

            c1, c2 = st.columns(2)
            c1.metric("Score de confiance", f"{score}/100")
            c2.metric("Gain annuel estimé", f'{gain["gainAnnuelEuros"] if gain else "—"} €' if gain else "—")

            if "DOUTE" in conclusion:
                st.warning(conclusion)
            elif "CONFIANCE" in conclusion:
                st.success(conclusion)
            else:
                st.info(conclusion)

            st.markdown("**Champs extraits automatiquement**")
            st.json({k: v["valeur_candidate"] for k, v in extraction_principale["champs_candidats"].items()})

            st.markdown("**Alertes détectées**")
            if not alertes:
                st.caption("Aucune alerte détectée.")
            for a in alertes:
                with st.expander(f"[{a.confiance}] {a.type_erreur}"):
                    st.write(a.description)
                    st.caption(a.action_recommandee)

            rapport = {
                "fichier": extraction_principale["fichier"],
                "alertes": [{"type_erreur": a.type_erreur} for a in alertes],
                "conclusion_fonction_de_doute": conclusion,
            }
            enregistrer_dossier(rapport)

            courrier = genererCourrier(nom_client, adresse, alertes)
            st.download_button(
                "Télécharger le courrier de réclamation",
                data=courrier,
                file_name=f"reclamation_{(nom_client or 'client').replace(' ', '_')}.txt",
                use_container_width=True,
            )
    else:
        st.caption("Dépose les documents à gauche puis clique sur « Lancer l'analyse ».")
