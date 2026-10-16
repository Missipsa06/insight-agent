"""Tests de la chaîne text-to-SQL, sans appel LLM : gratuits et déterministes.

Ce qu'ils protègent : aucune requête générée par le LLM ne doit pouvoir modifier la base,
lire un fichier du disque, ni renvoyer un résultat géant au LLM commentateur.
"""

import duckdb
import pytest

from insight_agent.data import generate as g
from insight_agent.text_to_sql import executer, valider_sql


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    """Une base générée dans un dossier temporaire (jamais data/panel.duckdb)."""
    chemin = tmp_path_factory.mktemp("panel") / "panel.duckdb"
    g.main(chemin)
    return chemin


# --- valider_sql : ce que le LLM a le droit d'écrire -----------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) FROM achats",
        "WITH t AS (SELECT * FROM produits) SELECT marque FROM t",
        "  select sum(poids) from foyers ;  ",
    ],
)
def test_valider_accepte_les_lectures(sql):
    assert valider_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM achats",
        "DROP TABLE foyers",
        "INSERT INTO enseignes VALUES (99, 'x', 'hyper')",
        "UPDATE produits SET marque = 'x'",
        "CREATE TABLE t AS SELECT 1",
        "SELECT 1; DROP TABLE achats",  # injection par instructions multiples
        "ATTACH 'autre.duckdb' AS autre",
        "COPY achats TO 'fuite.csv'",
    ],
)
def test_valider_rejette_tout_sauf_un_select(sql):
    with pytest.raises(ValueError):
        valider_sql(sql)


def test_valider_rejette_le_sql_invalide():
    with pytest.raises(ValueError):
        valider_sql("SELEC * FORM achats")


# --- executer : les garanties dures, même si la validation laissait passer quelque chose ---


def test_executer_renvoie_un_resultat(base):
    r = executer("SELECT categorie, count(*) AS n FROM produits GROUP BY 1", base)
    assert r.colonnes == ["categorie", "n"]
    assert r.total == 4


def test_executer_plafonne_les_lignes(base):
    r = executer("SELECT * FROM achats", base, max_lignes=50)
    assert len(r.lignes) == 50
    assert r.total > 50  # le total réel est conservé pour prévenir le LLM de la troncature


def test_executer_refuse_l_ecriture(base):
    # la connexion est en lecture seule : c'est la garantie dure, indépendante de valider_sql
    with pytest.raises(duckdb.Error):
        executer("DELETE FROM achats", base)


def test_executer_refuse_de_lire_un_fichier(base):
    # un SELECT valide qui lit le disque : seule l'option enable_external_access=false l'arrête
    with pytest.raises(duckdb.Error):
        executer("SELECT * FROM read_csv('pyproject.toml')", base)
