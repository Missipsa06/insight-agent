"""Tests des outils KPI, sans LLM : gratuits et déterministes.

Mini-panel construit à la main : chaque valeur attendue se recalcule sur papier (détail dans
les commentaires). On ne réutilise JAMAIS le SQL testé pour produire l'attendu : un test qui
relance la même requête ne prouverait rien.

Périodes, avec des données du lundi 2024-01-01 au dimanche 2025-12-28 :
CAM = 2024-12-30 → 2025-12-28 ; P-1 (= CAM A-1) = 2024-01-01 → 2024-12-29.
"""

import json
from datetime import date

import numpy as np
import pytest

from insight_agent import kpi
from insight_agent.data import generate as g
from insight_agent.kpi import (
    ArgsFidelite,
    ArgsPanierMoyen,
    ArgsPartDeMarche,
    ArgsPenetration,
    ArgsRecrutement,
    ErreurOutil,
    bornes_periode,
    calculer_fidelite,
    calculer_panier_moyen,
    calculer_part_de_marche,
    calculer_penetration,
    calculer_recrutement,
    executer,
)

# fmt: off
# Foyers F1..F4, poids 10/20/30/40 : base totale = 100, donc un poids se lit directement en %.
FOYERS = {"id_foyer": [1, 2, 3, 4], "region": ["Nord", "Est", "Ouest", "Nord"],
          "taille_foyer": [1, 2, 3, 4], "poids": [10.0, 20.0, 30.0, 40.0]}
# E2 a une apostrophe et un accent : le LLM écrira souvent « l'epicerie ».
ENSEIGNES = {"id_enseigne": [1, 2], "nom": ["MaxiPrix", "L'Épicerie"],
             "format": ["discount", "proxi"]}
# P3 : marque avec apostrophe (une f-string casserait la requête).
PRODUITS = {"id_produit": [1, 2, 3, 4],
            "libelle": ["Source Claire 1 l", "Montagne Pure 1.5 l", "Côte d'Azur 0.5 l",
                        "Grand Arome 0.25 kg"],
            "marque": ["Source Claire", "Montagne Pure", "Côte d'Azur", "Grand Arome"],
            "categorie": ["eaux", "eaux", "eaux", "cafe"],
            "contenance": [1.0, 1.5, 0.5, 0.25], "unite": ["l", "l", "l", "kg"]}
ACHATS = [  # (foyer, produit, enseigne, date, quantité, montant)
    # --- CAM
    (1, 1, 1, "2025-03-03", 2, 2.00),  # a1 ┐ même acte : F1, 03-03, MaxiPrix
    (1, 2, 1, "2025-03-03", 1, 1.50),  # a2 ┘
    (1, 1, 1, "2025-06-02", 1, 1.00),  # a3
    (2, 2, 2, "2025-04-07", 2, 3.00),  # a4
    (2, 1, 2, "2025-12-28", 1, 1.00),  # a5 : dernier jour de données (un dimanche)
    (3, 3, 1, "2025-05-05", 1, 4.00),  # a6 ┐ même acte : F3, 05-05, MaxiPrix
    (3, 4, 1, "2025-05-05", 1, 5.00),  # a7 ┘ (café)
    # --- P-1 ; F4 n'achète qu'ici
    (1, 1, 1, "2024-01-01", 1, 1.00),  # b1 : premier jour de données (un lundi)
    (3, 1, 2, "2024-06-03", 1, 1.00),  # b2
    (4, 2, 1, "2024-07-01", 1, 2.00),  # b3
]
# fmt: on


@pytest.fixture(scope="module")
def con(tmp_path_factory):
    """Écrit le mini-panel avec la fonction du générateur (mêmes types, ENUM compris)."""
    colonnes = list(zip(*ACHATS, strict=True))
    achats = {
        "id_foyer": np.array(colonnes[0]),
        "id_produit": np.array(colonnes[1]),
        "id_enseigne": np.array(colonnes[2]),
        "date_achat": np.array(colonnes[3], dtype="datetime64[D]"),
        "quantite": np.array(colonnes[4]),
        "montant": np.array(colonnes[5]),
    }
    tables = {
        "foyers": FOYERS,
        "enseignes": ENSEIGNES,
        "produits": PRODUITS,
        "achats": achats,
    }
    chemin = tmp_path_factory.mktemp("mini") / "mini.duckdb"
    g.ecrire_duckdb(
        {nom: {k: np.array(v) for k, v in t.items()} for nom, t in tables.items()}, chemin
    )
    with kpi.connecter(chemin) as c:
        yield c


# --- Périodes (fonction pure) ---------------------------------------------------------------

ETENDUE = (date(2024, 1, 1), date(2025, 12, 28))


@pytest.mark.parametrize(
    ("periode", "attendu"),
    [
        ("CAM", (date(2024, 12, 30), date(2025, 12, 28))),
        ("P-1", (date(2024, 1, 1), date(2024, 12, 29))),
        # 2024-12-30 est le lundi de la semaine ISO 1 de 2025 : ici YTD = CAM
        ("YTD", (date(2024, 12, 30), date(2025, 12, 28))),
    ],
)
def test_bornes_periode(periode, attendu):
    assert bornes_periode(ETENDUE, periode) == attendu


def test_semaine_en_cours_exclue():
    """Données jusqu'au mercredi 2025-12-31 : le CAM s'arrête au dimanche complet d'avant."""
    assert bornes_periode((date(2024, 1, 1), date(2025, 12, 31)), "CAM")[1] == date(2025, 12, 28)


def test_periode_hors_donnees():
    with pytest.raises(ErreurOutil) as e:
        bornes_periode((date(2024, 6, 3), date(2025, 12, 28)), "P-1")
    assert e.value.code == "PERIODE_HORS_DONNEES"


# --- Pénétration ----------------------------------------------------------------------------


def test_penetration(con):
    # CAM : acheteurs Source Claire = F1 (10) + F2 (20) → 30 / 100. A-1 : F1 + F3 → 40 %.
    r = calculer_penetration(con, ArgsPenetration(marque="Source Claire"))
    assert r["penetration_pct"] == 30.0
    assert r["acheteurs_panel"] == 2  # F1 a acheté 2 fois : compté une seule fois
    assert r["penetration_pct_a1"] == 40.0
    assert r["evolution_penetration_pct"] == -10.0
    assert r["base_faible"] is True


@pytest.mark.parametrize(("saisie", "attendu"), [("l'epicerie", 20.0), ("MAXIPRIX", 10.0)])
def test_penetration_par_enseigne(con, saisie, attendu):
    # L'Épicerie : F2 seul (a5) ; MaxiPrix : F1 seul (a1, a3). Casse et accents tolérés.
    r = calculer_penetration(con, ArgsPenetration(marque="source claire", enseigne=saisie))
    assert r["penetration_pct"] == attendu


def test_zero_legitime_n_est_pas_une_erreur(con):
    """Grand Arome existe mais n'a aucun acheteur en P-1 : 0 %, pas une erreur.
    Et P-1 n'a pas de période A-1 dans les données : pas d'évolution, pas d'erreur non plus."""
    r = calculer_penetration(con, ArgsPenetration(marque="Grand Arome", periode="P-1"))
    assert r["penetration_pct"] == 0.0
    assert "evolution_penetration_pct" not in r


# --- Part de marché -------------------------------------------------------------------------


def test_pdm_valeur(con):
    # eaux CAM, montant × poids : a1 20 + a2 15 + a3 10 + a4 60 + a5 20 + a6 120 = 245
    # Source Claire = a1 + a3 + a5 = 50 → 20,41 %. A-1 : (10 + 30) / 120 = 33,33 %.
    r = calculer_part_de_marche(con, ArgsPartDeMarche(marque="Source Claire"))
    assert r["categorie"] == "eaux"  # déduite de la marque
    assert (r["ventes_marque"], r["ventes_categorie"]) == (50, 245)
    assert r["pdm_pct"] == 20.41
    assert r["evolution_pdm_pct"] == pytest.approx(20.41 - 33.33)


def test_pdm_volume(con):
    # quantité × contenance × poids : a1 20 + a2 15 + a3 10 + a4 60 + a5 20 + a6 15 = 140 ; SC = 50
    r = calculer_part_de_marche(con, ArgsPartDeMarche(marque="Source Claire", mesure="volume"))
    assert r["pdm_pct"] == 35.71
    assert r["unite"] == "l"


def test_pdm_enseigne_filtre_aussi_le_denominateur(con):
    # MaxiPrix seulement : eaux = a1 20 + a2 15 + a3 10 + a6 120 = 165 ; SC = 30 → 18,18 %.
    # (Avec le dénominateur toutes enseignes, on trouverait 30 / 245 = 12,24 % : faux.)
    r = calculer_part_de_marche(con, ArgsPartDeMarche(marque="Source Claire", enseigne="MaxiPrix"))
    assert r["pdm_pct"] == 18.18


# --- Panier moyen ---------------------------------------------------------------------------


def test_panier_moyen_marque(con):
    # Actes avec SC : (F1, 03-03) 10, (F1, 06-02) 10, (F2, 12-28) 20 → 40 ; dépense 50 ;
    # acheteurs F1 + F2 = 30. Panier 50/40, fréquence 40/30, dépense par acheteur 50/30.
    r = calculer_panier_moyen(con, ArgsPanierMoyen(marque="Source Claire"))
    assert (r["depense"], r["actes"], r["acheteurs"]) == (50, 40, 30)
    assert (r["panier_moyen"], r["frequence"], r["depense_par_acheteur"]) == (1.25, 1.33, 1.67)


def test_panier_moyen_compte_les_actes_pas_les_lignes(con):
    # Tous produits, CAM : dépense 395 ; 5 actes distincts (poids 10+10+20+20+30 = 90) pour
    # 7 lignes (poids 130). Panier = 395 / 90 = 4,39 ; par ligne on trouverait 3,04 : faux.
    r = calculer_panier_moyen(con, ArgsPanierMoyen())
    assert (r["depense"], r["actes"], r["acheteurs"]) == (395, 90, 60)
    assert r["panier_moyen"] == 4.39
    assert r["depense_par_acheteur"] == pytest.approx(r["frequence"] * r["panier_moyen"], rel=0.01)


# --- Recrutement ----------------------------------------------------------------------------


def test_recrutement(con):
    # SC : CAM = {F1, F2}, A-1 = {F1, F3}. Recruté F2 (20), perdu F3 (30), conservé F1 (10).
    r = calculer_recrutement(con, ArgsRecrutement(marque="Source Claire"))
    assert (r["recrutes"], r["perdus"], r["conserves"]) == (20, 30, 10)
    assert (r["acheteurs_courant"], r["acheteurs_reference"], r["solde_net"]) == (30, 40, -10)
    assert r["taux_recrutement_pct"] == 66.67


# --- Fidélité -------------------------------------------------------------------------------


def test_fidelite(con):
    # Acheteurs SC : F1 (eaux 4,5 € dont SC 3 €, poids 10) et F2 (eaux 4 € dont SC 1 €, poids 20)
    # → nourriture (30 + 20) / (45 + 80) = 40 %. Les deux achètent aussi Montagne Pure : 0 exclusif.
    r = calculer_fidelite(con, ArgsFidelite(marque="Source Claire"))
    assert (r["taux_nourriture_pct"], r["exclusifs_pct"]) == (40.0, 0.0)


@pytest.mark.parametrize("saisie", ["cote d'azur", "Côte d’Azur"])  # sans accent ; apostrophe ’
def test_fidelite_marque_a_apostrophe(con, saisie):
    # F3 n'achète que Côte d'Azur en eaux (a6) : 100 % de nourriture, 100 % d'exclusifs.
    r = calculer_fidelite(con, ArgsFidelite(marque=saisie))
    assert r["marque"] == "Côte d'Azur"
    assert (r["taux_nourriture_pct"], r["exclusifs_pct"]) == (100.0, 100.0)


# --- Erreurs renvoyées au LLM ---------------------------------------------------------------


def test_marque_inconnue_suggere(con):
    with pytest.raises(ErreurOutil) as e:
        calculer_penetration(con, ArgsPenetration(marque="Source Clair"))
    assert e.value.code == "MARQUE_INCONNUE"
    assert e.value.suggestions == ["Source Claire"]


def test_injection_sql_ne_passe_pas(con):
    # Avec une f-string, ce texte élargirait le filtre à toutes les marques (PDM 100 %).
    with pytest.raises(ErreurOutil) as e:
        calculer_part_de_marche(con, ArgsPartDeMarche(marque="x' OR '1'='1"))
    assert e.value.code == "MARQUE_INCONNUE"


def test_enseigne_inconnue(con):
    with pytest.raises(ErreurOutil) as e:
        calculer_penetration(con, ArgsPenetration(marque="Source Claire", enseigne="Lidle"))
    assert e.value.code == "ENSEIGNE_INCONNUE"


def test_base_faible_selon_seuil(con, monkeypatch):
    monkeypatch.setattr(kpi, "SEUIL_BASE_FAIBLE", 2)  # SC a 2 acheteurs panel sur le CAM
    assert (
        calculer_penetration(con, ArgsPenetration(marque="Source Claire"))["base_faible"] is False
    )


# --- Registre et exécution d'un appel du LLM ------------------------------------------------


@pytest.mark.parametrize(
    ("nom", "arguments", "code"),
    [
        ("calculer_meteo", "{}", "OUTIL_INCONNU"),
        (
            "calculer_penetration",
            '{"marque": "Source Claire"',
            "ARGUMENTS_INVALIDES",
        ),  # JSON tronqué
        ("calculer_penetration", '{"periode": "CAM"}', "ARGUMENTS_INVALIDES"),  # marque manquante
        ("calculer_penetration", '{"marque": "SC", "periode": "T3"}', "ARGUMENTS_INVALIDES"),
        (
            "calculer_recrutement",
            '{"marque": "Source Claire", "periode": "P-1"}',
            "ARGUMENTS_INVALIDES",
        ),
        # argument inventé : refusé, pas ignoré (sinon « chez Lidl » serait perdu en silence)
        (
            "calculer_penetration",
            '{"marque": "Source Claire", "magasin": "Lidl"}',
            "ARGUMENTS_INVALIDES",
        ),
        ("calculer_fidelite", '{"marque": "Evian"}', "MARQUE_INCONNUE"),
    ],
)
def test_executer_renvoie_des_erreurs_structurees(con, nom, arguments, code):
    resultat = executer(con, nom, arguments)
    assert resultat["error"] is True
    assert resultat["code"] == code
    json.dumps(resultat)  # le résultat repart au LLM : il doit être sérialisable


def test_executer_appel_valide(con):
    resultat = executer(con, "calculer_penetration", '{"marque": "source claire"}')
    assert "error" not in resultat
    assert resultat["penetration_pct"] == 30.0
    json.dumps(resultat)


def test_definitions_outils():
    """Le schéma vu par le LLM vient des modèles Pydantic : il ne peut pas diverger du code."""
    definitions = {d["function"]["name"]: d["function"] for d in kpi.definitions_outils()}
    assert set(definitions) == set(kpi.REGISTRE)
    recrutement = definitions["calculer_recrutement"]["parameters"]
    assert recrutement["properties"]["periode"]["enum"] == ["CAM", "YTD"]  # pas de P-1
    assert recrutement["additionalProperties"] is False
    assert recrutement["required"] == ["marque"]


# --- Cohérence avec le text-to-SQL du J2, sur le panel généré -------------------------------


def test_coherent_avec_le_few_shot_du_j2(tmp_path_factory):
    """La pénétration de l'outil = celle du SQL de référence du few-shot J2 (même définition)."""
    chemin = tmp_path_factory.mktemp("panel") / "panel.duckdb"
    g.main(chemin)
    with kpi.connecter(chemin) as c:
        outil = calculer_penetration(c, ArgsPenetration(marque="Source Claire"))
        few_shot = c.execute(
            """
            SELECT SUM(f.poids) / (SELECT SUM(poids) FROM foyers) FROM foyers f
            WHERE f.id_foyer IN (
                SELECT DISTINCT a.id_foyer FROM achats a JOIN produits p USING (id_produit)
                WHERE p.marque = 'Source Claire'
                  AND a.date_achat BETWEEN DATE '2024-12-30' AND DATE '2025-12-28')
            """
        ).fetchone()[0]
    assert outil["penetration_pct"] == round(100 * few_shot, 2)
