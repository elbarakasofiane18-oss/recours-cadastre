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
import io
import tempfile
from datetime import date
from pathlib import Path

import streamlit as st
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable

from extraction import analyser_document, extraire_champs_via_ia, fusionner_extractions
from moteur_regles import executer_moteur, calculer_montant_recuperable
from gestion_clients import (
    enregistrer_nouveau_client, mettre_a_jour_client, supprimer_client,
    charger_clients, calculer_delai, statistiques_portefeuille,
)
from suivi_dossiers import enregistrer_dossier, charger_historique, statistiques
from analyser_dossier import niveau_doute_global, score_confiance_global
from moteur_regles import estimer_gain_potentiel

DOSSIER_COURANT = Path(__file__).parent
FICHIER_REGLES = DOSSIER_COURANT / "regles_fiscales.json"

# Nom affiché comme prestataire sur les documents générés (devis, rapport).
# Modifie cette valeur si tu changes de nom commercial ou de statut juridique.
NOM_PRESTATAIRE = "Sofiane El Baraka"


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


def genererRapportJustificationPDF(nom_client, adresse, champs, alertes, gain, score, conclusion) -> bytes:
    """
    Rapport de justification : reprend les chiffres exacts du dossier du
    client et, pour chaque point soulevé, la référence légale ou la source
    DGFIP précise qui le fonde. Objectif : que le client (et l'administration
    en cas de réclamation) puisse vérifier chaque affirmation par lui-même.
    """
    navy = colors.HexColor("#1B2A4A")
    gold = colors.HexColor("#B08D57")
    gray = colors.HexColor("#555555")
    lightgray = colors.HexColor("#F2F2F2")

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("T", parent=styles["Title"], fontSize=19, textColor=navy, spaceAfter=4)
    subtitle_style = ParagraphStyle("S", parent=styles["Normal"], fontSize=10, textColor=gray, spaceAfter=14)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12.5, textColor=gold, spaceBefore=14, spaceAfter=6)
    body = ParagraphStyle("B", parent=styles["Normal"], fontSize=10, leading=14.5, spaceAfter=6)
    small = ParagraphStyle("Sm", parent=body, fontSize=8.7, textColor=gray, leftIndent=8)
    ref = ParagraphStyle("Ref", parent=body, fontSize=9, textColor=navy, leftIndent=8, spaceAfter=10)

    buffer = io.BytesIO()
    story = []

    story.append(Paragraph("Rapport de justification", title_style))
    story.append(Paragraph(
        f"Dossier : {nom_client or '[nom non renseigné]'} — {adresse or '[adresse non renseignée]'} — "
        f"généré le {date.today().strftime('%d/%m/%Y')} par {NOM_PRESTATAIRE}", subtitle_style
    ))
    story.append(HRFlowable(width="100%", thickness=1, color=gold))
    story.append(Spacer(1, 0.3*cm))

    story.append(Paragraph("Chiffres extraits du dossier du client", h2))
    lignes = [["Champ", "Valeur retenue"]]
    for cle, val in champs.items():
        if cle.startswith("_"):
            continue
        valeur = val.get("valeur_candidate") if isinstance(val, dict) else val
        lignes.append([cle.replace("_", " ").capitalize(), str(valeur) if valeur is not None else "—"])
    t = Table(lignes, colWidths=[7*cm, 8*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), navy),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, lightgray]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t)

    story.append(Paragraph("Synthèse", h2))
    story.append(Paragraph(f"<b>Score de confiance :</b> {score}/100", body))
    if gain:
        story.append(Paragraph(f"<b>Gain annuel estimé :</b> {gain['gain_annuel_taxe_estime_euros']:.0f} € (estimation, taux indicatif)", body))
    story.append(Paragraph(f"<b>Conclusion :</b> {conclusion}", body))

    story.append(Paragraph("Points soulevés, chiffres du client et texte de loi correspondant", h2))
    if not alertes:
        story.append(Paragraph("Aucun point n'a été détecté sur ce dossier avec les informations fournies.", body))
    for i, a in enumerate(alertes, 1):
        story.append(Paragraph(f"<b>{i}. {a.type_erreur.replace('_', ' ')}</b> — confiance : {a.confiance}", body))
        story.append(Paragraph(a.description, body))
        if a.reference_legale:
            story.append(Paragraph(f"Base légale / source DGFIP : {a.reference_legale}", ref))
        story.append(Paragraph(f"Pièce à vérifier : {a.piece_a_verifier}", small))
        story.append(Paragraph(f"Action recommandée : {a.action_recommandee}", small))
        story.append(Spacer(1, 0.25*cm))

    story.append(HRFlowable(width="100%", thickness=0.6, color=gray))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        "Toutes les références légales et seuils chiffrés de ce rapport proviennent exclusivement de "
        "sources DGFIP (impots.gouv.fr, bofip.impots.gouv.fr, formulaires officiels Cerfa). Ces seuils "
        "sont revalorisés chaque année : à reconfirmer sur impots.gouv.fr avant tout envoi définitif à "
        "l'administration. Ce rapport est un outil d'aide à la décision, pas un document officiel.",
        small
    ))

    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=2*cm, rightMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm,
                             title="Rapport de justification", author=nom_client or "Client")
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def genererDevisPDF(nom_client, adresse, postes, total_annuel, pourcentage_commission, montant_commission,
                     nom_prestataire="Sofiane El Baraka") -> bytes:
    """
    Devis client : reprend les postes détectés et leurs montants annuels
    estimés, puis calcule la commission due une fois l'économie obtenue.
    Le montant "récupéré par le client" reste une estimation tant que la
    décision de l'administration n'est pas connue (voir délai de 6 mois).
    """
    navy = colors.HexColor("#1B2A4A")
    gold = colors.HexColor("#B08D57")
    gray = colors.HexColor("#555555")
    lightgray = colors.HexColor("#F2F2F2")

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("T", parent=styles["Title"], fontSize=19, textColor=navy, spaceAfter=4)
    subtitle_style = ParagraphStyle("S", parent=styles["Normal"], fontSize=10, textColor=gray, spaceAfter=14)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12.5, textColor=gold, spaceBefore=14, spaceAfter=6)
    body = ParagraphStyle("B", parent=styles["Normal"], fontSize=10, leading=14.5, spaceAfter=6)
    small = ParagraphStyle("Sm", parent=body, fontSize=8.7, textColor=gray, leftIndent=8)

    buffer = io.BytesIO()
    story = []

    story.append(Paragraph("Devis — estimation des économies et honoraires", title_style))
    story.append(Paragraph(
        f"Dossier : {nom_client or '[nom non renseigné]'} — {adresse or '[adresse non renseignée]'} — "
        f"généré le {date.today().strftime('%d/%m/%Y')}", subtitle_style
    ))
    story.append(HRFlowable(width="100%", thickness=1, color=gold))
    story.append(Spacer(1, 0.3*cm))

    story.append(Paragraph("Détail des postes identifiés", h2))
    lignes = [["Poste", "Économie annuelle estimée"]]
    for p in postes:
        lignes.append([p["libelle"], f'{p["montant_annuel_estime"]:.0f} €'])
    lignes.append(["TOTAL ANNUEL ESTIMÉ", f"{total_annuel:.0f} €"])
    t = Table(lignes, colWidths=[10.5*cm, 4.5*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), navy),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, lightgray]),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#EFE7D8")),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(t)

    story.append(Paragraph("Honoraires (rémunération au succès)", h2))
    tableau_honoraires = [
        ["Économie annuelle estimée pour le client", f"{total_annuel:.0f} €"],
        [f"Commission ({pourcentage_commission:.0f}%)", f"{montant_commission:.0f} €"],
        ["Reste pour le client la 1ère année", f"{(total_annuel - montant_commission):.0f} €"],
    ]
    t2 = Table(tableau_honoraires, colWidths=[10.5*cm, 4.5*cm])
    t2.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#EFE7D8")),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(t2)
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(
        "Rémunération due uniquement en cas de succès de la démarche (aucun frais si aucune économie "
        "n'est obtenue). Les années suivantes, si l'économie est récurrente (exonération, dégrèvement), "
        "elle profite intégralement au client sans commission supplémentaire, sauf accord contraire.",
        body
    ))

    story.append(Paragraph("Signatures", h2))
    date_du_jour = date.today().strftime('%d/%m/%Y')
    tableau_signatures = [
        ["Le prestataire", "Le client"],
        [nom_prestataire, nom_client or "[Nom du client]"],
        [f"Fait le {date_du_jour}", f"Fait le {date_du_jour}"],
        ["Signature :", "Signature :"],
        ["", ""],
        ["", ""],
    ]
    t3 = Table(tableau_signatures, colWidths=[7.5*cm, 7.5*cm], rowHeights=[0.6*cm, 0.6*cm, 0.6*cm, 0.6*cm, 1.6*cm, None])
    t3.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), navy),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, gray),
        ("BOX", (0, 0), (0, -1), 0.4, colors.HexColor("#CCCCCC")),
        ("BOX", (1, 0), (1, -1), 0.4, colors.HexColor("#CCCCCC")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(t3)
    story.append(Spacer(1, 0.3*cm))

    story.append(HRFlowable(width="100%", thickness=0.6, color=gray))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        "Ce document ne constitue pas un conseil fiscal professionnel au sens réglementaire (avocat "
        "fiscaliste, expert-comptable) : il s'agit d'une estimation d'aide à la décision.",
        small
    ))
    story.append(Paragraph(
        "Estimation réalisée à partir des informations déclarées par le client et des seuils DGFIP en "
        "vigueur (impots.gouv.fr, bofip.impots.gouv.fr). Les montants réels dépendent de la décision de "
        "l'administration, qui dispose d'un délai de réponse d'environ 6 mois après le dépôt de la "
        "réclamation. Ce document est un devis indicatif, pas un engagement contractuel définitif.",
        small
    ))

    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=2*cm, rightMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm,
                             title="Devis", author=nom_client or "Client")
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


st.set_page_config(page_title="Recours Cadastre — 06", layout="wide")

# ---------- Protection par mot de passe ----------
# Le mot de passe attendu est lu depuis st.secrets (configuré dans Streamlit
# Community Cloud une fois en ligne) ou, à défaut, depuis une valeur locale
# ci-dessous pour les tests sur ton ordinateur. Change MOT_DE_PASSE_LOCAL
# avant de mettre l'outil en ligne.
MOT_DE_PASSE_LOCAL = "change-moi"

def mot_de_passe_attendu() -> str:
    try:
        return st.secrets["APP_PASSWORD"]
    except Exception:
        return MOT_DE_PASSE_LOCAL

if "authentifie" not in st.session_state:
    st.session_state.authentifie = False

if not st.session_state.authentifie:
    st.title("Recours Cadastre — accès privé")
    mdp_saisi = st.text_input("Mot de passe", type="password")
    if st.button("Se connecter"):
        if mdp_saisi == mot_de_passe_attendu():
            st.session_state.authentifie = True
            st.rerun()
        else:
            st.error("Mot de passe incorrect.")
    st.stop()  # bloque tout le reste de la page tant que non authentifié

regles = charger_regles()

st.title("Recours Cadastre — analyse d'un dossier")
st.caption("06 · Taxe foncière · Application locale — les fichiers restent sur cet ordinateur")

with st.sidebar:
    if st.button("Se déconnecter"):
        st.session_state.authentifie = False
        st.rerun()
    st.divider()
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

if "dernier_dossier" not in st.session_state:
    st.session_state.dernier_dossier = None

tab_analyse, tab_devis, tab_clients = st.tabs(["📋 Analyse d'un dossier", "💶 Devis client", "👥 Mes clients"])

with tab_analyse:
    col_gauche, col_droite = st.columns([1, 1])

    with col_gauche:
        st.subheader("1. Identité du client")
        nom_client = st.text_input("Nom du client")
        adresse = st.text_input("Adresse du bien")

        st.subheader("2. Pièces du dossier")
        st.caption("Documents à demander au client — voir le détail dans le PDF de présentation.")

        st.markdown("**Documents obligatoires**")
        fichier_avis = st.file_uploader("Avis de taxe foncière — année en cours (PDF)", type="pdf", key="avis")
        fichier_avis_precedent = st.file_uploader("Avis de taxe foncière — année précédente, si disponible (PDF)", type="pdf", key="avis_precedent")
        fichier_fiche = st.file_uploader("Fiche d'évaluation cadastrale détaillée (PDF)", type="pdf", key="fiche")
        fichier_avis_revenu = st.file_uploader("Dernier avis d'impôt sur le revenu (PDF)", type="pdf", key="avis_revenu")

        st.markdown("**Documents selon la situation du client**")
        fichier_permis = st.file_uploader("Permis de construire / déclaration de travaux — si construction récente (PDF)", type="pdf", key="permis")
        fichier_justificatif_social = st.file_uploader("Justificatif ASPA / ASI / AAH — si concerné (PDF)", type="pdf", key="justif_social")
        fichiers_factures_travaux = st.file_uploader(
            "Factures de travaux d'économie d'énergie — si concerné (PDF, plusieurs fichiers possibles)",
            type="pdf", key="factures_travaux", accept_multiple_files=True,
        )

        documents_recus = {
            "avis_annee_en_cours": fichier_avis is not None,
            "avis_annee_precedente": fichier_avis_precedent is not None,
            "fiche_evaluation_cadastrale": fichier_fiche is not None,
            "avis_impot_revenu": fichier_avis_revenu is not None,
            "permis_construire": fichier_permis is not None,
            "justificatif_aspa_asi_aah": fichier_justificatif_social is not None,
            "factures_travaux_energetiques": bool(fichiers_factures_travaux),
        }
        nb_recus = sum(documents_recus.values())
        st.caption(f"{nb_recus} / 7 documents déposés pour ce dossier.")

        cle_api_disponible = bool(st.secrets.get("ANTHROPIC_API_KEY")) if hasattr(st, "secrets") else False
        utiliser_ia = st.checkbox(
            "Activer la lecture assistée par IA (plus robuste aux mises en page variées, quelques centimes par document)",
            value=cle_api_disponible, disabled=not cle_api_disponible,
        )
        if not cle_api_disponible:
            st.caption("Pour activer : ajoute ANTHROPIC_API_KEY dans les secrets de l'application "
                       "(clé obtenue sur console.anthropic.com).")

        surface_reelle = st.number_input("Surface réelle mesurée (m²), si connue", min_value=0.0, value=0.0, step=1.0)
        elements_disparus_texte = st.text_input("Éléments déclarés disparus par le client (séparés par des virgules)")
        elements_disparus = [e.strip() for e in elements_disparus_texte.split(",") if e.strip()]

        st.subheader("3. Exonérations et dégrèvements potentiels")
        st.caption("Seuils 2026 (article 1417 du CGI, en vigueur depuis le 01/01/2026) : 12 679€ pour 1 part, +3 386€ par demi-part supplémentaire.")
        col_a, col_b, col_c = st.columns(3)
        age_client = col_a.number_input("Âge du client", min_value=0, max_value=120, value=0, step=1)
        nb_parts = col_b.number_input("Nombre de parts fiscales", min_value=1.0, value=1.0, step=0.5)
        rfr_client = col_c.number_input("Revenu fiscal de référence (RFR), en €", min_value=0, value=0, step=100)

        col_d, col_e = st.columns(2)
        aspa_asi = col_d.checkbox("Bénéficiaire de l'ASPA ou de l'ASI")
        aah = col_e.checkbox("Bénéficiaire de l'AAH")

        st.caption("Plafonnement en fonction du revenu (résidence principale uniquement) — article 1391 B ter, jamais automatique.")
        col_f, col_g = st.columns(2)
        residence_principale = col_f.checkbox("Le bien est la résidence principale du client")
        montant_tf = col_g.number_input("Montant actuel de la taxe foncière, en €, si connu", min_value=0, value=0, step=50)

        travaux_energetiques = st.checkbox("Travaux d'économie d'énergie réalisés récemment")

        st.caption("Exonération de 2 ans pour construction neuve, reconstruction ou extension (article 1383 du CGI).")
        construction_recente = st.checkbox("Construction, reconstruction ou extension achevée il y a moins de 3 ans")
        annees_construction = None
        if construction_recente:
            annees_construction = st.number_input("Nombre d'années depuis l'achèvement", min_value=0.0, max_value=3.0, value=0.5, step=0.5)

        st.caption("Dispositif de maintien de l'exonération (article 1391-II du CGI) — si le client était exonéré et a perdu l'éligibilité récemment.")
        perte_eligibilite = st.checkbox("Le client était exonéré (âge/ASPA/ASI/AAH) et a dépassé le seuil de revenu récemment")
        annees_depuis_perte = None
        if perte_eligibilite:
            annees_depuis_perte = st.selectbox("Depuis combien d'années ?", [1, 2, 3, 4], index=0)

        exonerations = {
            "age": age_client if age_client > 0 else None,
            "rfr": rfr_client if rfr_client > 0 else None,
            "nb_parts": nb_parts,
            "aspa_asi": aspa_asi,
            "aah": aah,
            "residence_principale": residence_principale,
            "montant_taxe_fonciere": montant_tf if montant_tf > 0 else None,
            "travaux_energetiques": travaux_energetiques,
            "annees_depuis_perte_eligibilite": annees_depuis_perte,
        }

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

                    if utiliser_ia and cle_api_disponible:
                        try:
                            champs_ia = extraire_champs_via_ia(
                                extraction_principale["texte_brut"], st.secrets["ANTHROPIC_API_KEY"]
                            )
                            extraction_principale["champs_candidats"] = fusionner_extractions(
                                extraction_principale["champs_candidats"], champs_ia
                            )
                            extraction_principale["lecture_ia_utilisee"] = True
                        except Exception as e:
                            st.warning(f"Lecture IA indisponible ({type(e).__name__}), poursuite avec les motifs seuls.")
                            extraction_principale["lecture_ia_utilisee"] = False
                    else:
                        extraction_principale["lecture_ia_utilisee"] = False

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
                        exonerations=exonerations,
                        annees_depuis_achevement_construction=annees_construction,
                    )
                    gain = estimer_gain_potentiel(
                        extraction_principale["champs_candidats"], alertes,
                        surface_reelle if surface_reelle > 0 else None
                    )
                    score = score_confiance_global(alertes, extraction_principale["methode_extraction"])
                    conclusion = niveau_doute_global(alertes)
                    montant_recuperable = calculer_montant_recuperable(alertes, exonerations, gain)

                if extraction_principale["methode_extraction"] == "ocr":
                    st.info("Document lu par reconnaissance de caractères (OCR) — vérifier les champs extraits.")
                elif extraction_principale["methode_extraction"] == "echec_ocr_indisponible":
                    st.warning("Document probablement scanné et OCR indisponible sur cette machine. "
                               "Installer pytesseract et pdf2image pour l'activer.")
                if extraction_principale.get("lecture_ia_utilisee"):
                    st.info("Lecture assistée par IA activée sur ce document.")

                c1, c2 = st.columns(2)
                c1.metric("Score de confiance", f"{score}/100")
                c2.metric("Gain annuel estimé", f'{gain["gain_annuel_taxe_estime_euros"]:.0f} €' if gain else "—")

                if "DOUTE" in conclusion:
                    st.warning(conclusion)
                elif "CONFIANCE" in conclusion:
                    st.success(conclusion)
                else:
                    st.info(conclusion)

                st.markdown("**Champs extraits automatiquement**")
                for cle, val in extraction_principale["champs_candidats"].items():
                    valeur = val.get("valeur_candidate")
                    confiance = val.get("confiance")
                    if confiance == "desaccord_entre_lectures":
                        st.warning(f"**{cle}** — désaccord entre les deux lectures : motifs = "
                                   f"'{valeur}', IA = '{val.get('valeur_alternative_ia')}' — à vérifier manuellement.")
                    elif confiance == "confirmee_double_lecture":
                        st.success(f"**{cle}** : {valeur} (confirmé par les deux méthodes de lecture)")
                    else:
                        st.caption(f"{cle} : {valeur if valeur is not None else '—'}")

                st.markdown("**Alertes détectées**")
                if not alertes:
                    st.caption("Aucune alerte détectée.")
                for a in alertes:
                    with st.expander(f"[{a.confiance}] {a.type_erreur}"):
                        st.write(a.description)
                        st.caption(a.action_recommandee)
                        if a.reference_legale:
                            st.markdown(f"**Base légale / source DGFIP :** {a.reference_legale}")

                rapport = {
                    "fichier": extraction_principale["fichier"],
                    "alertes": [{"type_erreur": a.type_erreur} for a in alertes],
                    "conclusion_fonction_de_doute": conclusion,
                }
                enregistrer_dossier(rapport)

                client_id = enregistrer_nouveau_client(
                    nom_client, adresse,
                    [{"type_erreur": a.type_erreur, "confiance": a.confiance, "reference_legale": a.reference_legale} for a in alertes],
                    montant_recuperable["total_annuel_estime"], score, conclusion,
                    documents_recus,
                )
                st.session_state.dernier_client_id = client_id

                # Sauvegarde pour l'onglet Devis, qui réutilise ce dossier
                st.session_state.dernier_dossier = {
                    "nom_client": nom_client,
                    "adresse": adresse,
                    "champs": extraction_principale["champs_candidats"],
                    "alertes": alertes,
                    "gain": gain,
                    "score": score,
                    "conclusion": conclusion,
                    "montant_recuperable": montant_recuperable,
                }

                col_dl1, col_dl2 = st.columns(2)

                courrier = genererCourrier(nom_client, adresse, alertes)
                col_dl1.download_button(
                    "Télécharger le courrier de réclamation",
                    data=courrier,
                    file_name=f"reclamation_{(nom_client or 'client').replace(' ', '_')}.txt",
                    use_container_width=True,
                )

                rapport_pdf = genererRapportJustificationPDF(
                    nom_client, adresse, extraction_principale["champs_candidats"],
                    alertes, gain, score, conclusion
                )
                col_dl2.download_button(
                    "Télécharger le rapport de justification (PDF)",
                    data=rapport_pdf,
                    file_name=f"rapport_justification_{(nom_client or 'client').replace(' ', '_')}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                )

                st.success("Dossier enregistré dans « Mes clients » — rends-toi dans l'onglet « Devis client » "
                          "pour générer le devis.")
        else:
            st.caption("Dépose les documents à gauche puis clique sur « Lancer l'analyse ».")

with tab_devis:
    st.subheader("Devis — économies estimées et honoraires")

    dossier = st.session_state.dernier_dossier
    if not dossier:
        st.info("Lance d'abord une analyse dans l'onglet « Analyse d'un dossier » : le devis se construit "
                "automatiquement à partir des postes détectés.")
    else:
        st.markdown(f"**Client :** {dossier['nom_client'] or '—'}  \n**Adresse :** {dossier['adresse'] or '—'}")

        postes = dossier["montant_recuperable"]["postes"]
        total_annuel = dossier["montant_recuperable"]["total_annuel_estime"]

        if not postes:
            st.warning("Aucun poste chiffrable n'a été détecté sur ce dossier (il manque peut-être le "
                       "montant actuel de la taxe foncière, à saisir dans l'onglet Analyse). Le devis a "
                       "besoin d'au moins un montant pour être généré.")
        else:
            st.markdown("**Postes détectés**")
            lignes_affichage = [{"Poste": p["libelle"], "Économie annuelle estimée": f'{p["montant_annuel_estime"]:.0f} €'} for p in postes]
            st.table(lignes_affichage)

            st.metric("Total annuel estimé pour le client", f"{total_annuel:.0f} €")

            pourcentage_commission = st.slider("Pourcentage de commission", min_value=0, max_value=50, value=30, step=5)
            montant_commission = total_annuel * pourcentage_commission / 100

            col_x, col_y = st.columns(2)
            col_x.metric("Ta commission (1ère année)", f"{montant_commission:.0f} €")
            col_y.metric("Reste pour le client (1ère année)", f"{(total_annuel - montant_commission):.0f} €")

            st.caption(dossier["montant_recuperable"]["avertissement"])

            devis_pdf = genererDevisPDF(
                dossier["nom_client"], dossier["adresse"], postes, total_annuel,
                pourcentage_commission, montant_commission, NOM_PRESTATAIRE
            )
            st.download_button(
                "Télécharger le devis (PDF)",
                data=devis_pdf,
                file_name=f"devis_{(dossier['nom_client'] or 'client').replace(' ', '_')}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )

with tab_clients:
    st.subheader("Mes clients")
    st.caption("Base persistante — pense à exporter une sauvegarde régulièrement (bouton en bas de page).")

    clients = charger_clients()
    stats_portefeuille = statistiques_portefeuille()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Clients", stats_portefeuille["nb_clients"])
    c2.metric("Économie totale estimée", f'{stats_portefeuille["total_annuel_estime_portefeuille"]:.0f} €')
    c3.metric("Gagnés", stats_portefeuille["nb_gagnes"])
    c4.metric("En attente", stats_portefeuille["nb_en_attente"])

    st.divider()
    st.markdown("### ⏰ Tableau de bord des délais")

    clients_avec_delai = []
    for c in clients:
        delai = calculer_delai(c.get("date_envoi_reclamation", ""))
        if delai:
            clients_avec_delai.append((c, delai))

    if not clients_avec_delai:
        st.caption("Aucune réclamation avec date d'envoi renseignée pour l'instant.")
    else:
        clients_avec_delai.sort(key=lambda x: x[1]["jours_restants"])
        for c, delai in clients_avec_delai:
            if delai["depasse"]:
                st.error(f"**{c['nom']}** — délai dépassé depuis le {delai['date_echeance']} "
                         f"({abs(delai['jours_restants'])} jours de retard) — à relancer.")
            elif delai["urgent"]:
                st.warning(f"**{c['nom']}** — réponse attendue avant le {delai['date_echeance']} "
                           f"({delai['jours_restants']} jours restants).")
            else:
                st.info(f"**{c['nom']}** — réponse attendue avant le {delai['date_echeance']} "
                        f"({delai['jours_restants']} jours restants).")

    st.divider()
    st.markdown("### Liste complète")

    if not clients:
        st.info("Aucun client enregistré pour l'instant. Lance une analyse dans le premier onglet.")
    else:
        for c in sorted(clients, key=lambda x: x.get("total_annuel_estime", 0), reverse=True):
            with st.expander(f"{c['nom']} — {c.get('total_annuel_estime', 0):.0f} €/an estimé — statut : {c['statut']}"):
                st.caption(f"Adresse : {c['adresse'] or '—'}  |  Créé le : {c['date_creation'][:10]}  |  Score : {c.get('score', '—')}/100")
                st.caption(f"Conclusion : {c.get('conclusion', '—')}")

                if c.get("alertes_resume"):
                    st.markdown("**Points détectés :**")
                    for a in c["alertes_resume"]:
                        st.caption(f"• {a['type_erreur'].replace('_', ' ')} ({a['confiance']}) — {a.get('reference_legale', '')}")

                docs = c.get("documents_recus")
                if docs:
                    noms_docs = {
                        "avis_annee_en_cours": "Avis (année en cours)",
                        "avis_annee_precedente": "Avis (année précédente)",
                        "fiche_evaluation_cadastrale": "Fiche d'évaluation cadastrale",
                        "avis_impot_revenu": "Avis d'impôt sur le revenu",
                        "permis_construire": "Permis de construire",
                        "justificatif_aspa_asi_aah": "Justificatif ASPA/ASI/AAH",
                        "factures_travaux_energetiques": "Factures travaux énergétiques",
                    }
                    manquants = [noms_docs[k] for k, v in docs.items() if not v and k in noms_docs]
                    if manquants:
                        st.warning("**Documents manquants :** " + ", ".join(manquants))
                    else:
                        st.success("Tous les documents pertinents ont été déposés pour ce dossier.")

                col_a, col_b, col_c = st.columns(3)
                nouveau_statut = col_a.selectbox(
                    "Statut", ["en_attente", "gagne", "perdu", "non_depose"],
                    index=["en_attente", "gagne", "perdu", "non_depose"].index(c["statut"]),
                    key=f"statut_{c['id']}",
                )
                nouvelle_date = col_b.text_input(
                    "Date d'envoi réclamation (AAAA-MM-JJ)", value=c.get("date_envoi_reclamation", ""),
                    key=f"date_{c['id']}",
                )
                if col_c.button("Mettre à jour", key=f"maj_{c['id']}"):
                    mettre_a_jour_client(c["id"], statut=nouveau_statut, date_envoi_reclamation=nouvelle_date)
                    st.rerun()

                if st.button("🗑️ Supprimer ce client", key=f"suppr_{c['id']}"):
                    supprimer_client(c["id"])
                    st.rerun()

    st.divider()
    if clients:
        sauvegarde_json = json.dumps(clients, indent=2, ensure_ascii=False)
        st.download_button(
            "📥 Exporter une sauvegarde de tous les clients (JSON)",
            data=sauvegarde_json,
            file_name=f"sauvegarde_clients_{date.today().isoformat()}.json",
            mime="application/json",
            use_container_width=True,
        )
