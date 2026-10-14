"""Contrôles qualité de la base panel générée par `insight_agent.data.generate`.

Deux niveaux de validation se complètent :
- dans `generate.main()`, Pydantic valide un échantillon de 1 000 achats : il attrape les bugs
  systématiques (mauvais type, contrainte violée sur toutes les lignes) ;
- ici, des requêtes SQL portent sur 100 % des lignes : elles attrapent les cas rares
  (un montant arrondi à 0, une clé orpheline) et les incohérences entre tables.

La base est générée une seule fois par module, dans un dossier temporaire :
les tests ne touchent jamais `data/panel.duckdb`.
"""

from datetime import date
from typing import get_args

import duckdb
import numpy as np
import pytest

from insight_agent.data import generate as g
from insight_agent.schemas import Categorie, Format, Region, Unite


@pytest.fixture(scope="module")
def con(tmp_path_factory):
    """Génère une base complète dans un dossier temporaire et l'ouvre en lecture seule."""
    chemin = tmp_path_factory.mktemp("panel") / "panel.duckdb"
    g.main(chemin)
    with duckdb.connect(str(chemin), read_only=True) as c:
        yield c


def scalaire(con, sql):
    """Exécute une requête qui renvoie une seule valeur et la retourne."""
    return con.sql(sql).fetchone()[0]


def test_volumes(con):
    """Les tables ont la taille attendue.

    Foyers et enseignes sont fixés par configuration : on attend le compte exact. Le nombre
    d'achats est tiré au hasard (Gamma-Poisson, ~100 actes par foyer) : on attend un ordre de
    grandeur. Un écart signale un paramètre cassé (ex. `scale` de la loi Gamma ×10).
    """
    assert scalaire(con, "SELECT count(*) FROM foyers") == g.N_FOYERS
    assert scalaire(con, "SELECT count(*) FROM enseignes") == len(g.ENSEIGNES)
    assert 250_000 < scalaire(con, "SELECT count(*) FROM achats") < 350_000


def test_aucune_cle_orpheline(con):
    """Chaque achat pointe vers un foyer, un produit et une enseigne qui existent.

    Un achat orphelin disparaîtrait silencieusement des jointures du text-to-SQL (J2) :
    les KPI seraient faux sans aucune erreur visible.
    """
    orphelins = scalaire(
        con,
        """
        SELECT count(*) FROM achats a
        LEFT JOIN foyers f USING (id_foyer)
        LEFT JOIN produits p USING (id_produit)
        LEFT JOIN enseignes e USING (id_enseigne)
        WHERE f.id_foyer IS NULL OR p.id_produit IS NULL OR e.id_enseigne IS NULL
        """,
    )
    assert orphelins == 0


def test_cles_primaires_uniques(con):
    """Les identifiants des tables de dimension sont uniques.

    DuckDB ne pose pas de contrainte PRIMARY KEY sur ces tables : un doublon dupliquerait
    les lignes d'achats à chaque jointure et gonflerait tous les KPI.
    """
    for table, cle in [
        ("foyers", "id_foyer"),
        ("produits", "id_produit"),
        ("enseignes", "id_enseigne"),
    ]:
        assert scalaire(con, f"SELECT count(*) - count(DISTINCT {cle}) FROM {table}") == 0


def test_contraintes_achats(con):
    """Les contraintes du schéma `Achat` tiennent sur toutes les lignes, pas sur un échantillon.

    Montant et quantité strictement positifs ; dates de type DATE et comprises dans la période
    générée (N_SEMAINES à partir de DATE_DEBUT).
    """
    assert scalaire(con, "SELECT count(*) FROM achats WHERE montant <= 0 OR quantite <= 0") == 0
    debut, fin = con.sql("SELECT min(date_achat), max(date_achat) FROM achats").fetchone()
    assert isinstance(debut, date)
    assert debut >= g.DATE_DEBUT
    assert (fin - g.DATE_DEBUT).days < g.N_SEMAINES * 7


@pytest.mark.parametrize(
    ("table", "colonne", "valeurs"),
    [
        ("foyers", "region", Region),
        ("enseignes", "format", Format),
        ("produits", "categorie", Categorie),
        ("produits", "unite", Unite),
    ],
)
def test_valeurs_dans_le_referentiel(con, table, colonne, valeurs):
    """Les valeurs en base appartiennent aux référentiels déclarés dans `schemas.py`.

    Les alias `Literal` sont la source de vérité unique : le LLM recevra ces valeurs dans son
    prompt (J2). Une valeur hors référentiel serait introuvable par un filtre `WHERE`.
    """
    en_base = {r[0] for r in con.sql(f"SELECT DISTINCT {colonne} FROM {table}").fetchall()}
    assert en_base <= set(get_args(valeurs))


def test_generation_reproductible():
    """Avec la même seed, la génération produit exactement les mêmes achats.

    Indispensable pour les évals (J7) : un score ne se compare d'un run à l'autre que si
    les données sous-jacentes sont identiques.
    """

    def achats():
        rng = np.random.default_rng(g.SEED)
        foyers = g.generer_foyers(rng)
        produits, prix = g.generer_produits(rng)
        return g.generer_achats(rng, foyers, produits, prix, g.generer_enseignes())

    a, b = achats(), achats()
    assert all(np.array_equal(a[k], b[k]) for k in a)


def test_somme_des_poids(con):
    """La somme des poids des foyers vaut le nombre de foyers en France (N_FOYERS_FRANCE).

    C'est ce qui permet d'extrapoler le panel à la population : nombre d'acheteurs,
    sommes dépensées.
    """
    total = scalaire(con, "SELECT sum(poids) FROM foyers")
    assert total == pytest.approx(g.N_FOYERS_FRANCE)


def test_marque_leader_par_categorie(con):
    """Dans chaque catégorie, la marque leader en actes d'achat est la plus attractive du CATALOGUE.

    Vérifie que l'attractivité se traduit bien en parts de marché : sans elle, toutes les
    marques auraient la même PDM et les KPI du J3 seraient sans intérêt.
    """
    observe = dict(
        con.sql(
            """
        WITH t AS (
        SELECT p.categorie, p.marque, count(*) AS nb
        FROM achats a
        JOIN produits p USING (id_produit)
        GROUP BY p.categorie, p.marque
        )
        SELECT categorie, arg_max(marque, nb)
        FROM t
        GROUP BY categorie
        """
        ).fetchall()
    )
    attendu = {
        cat: max(marques, key=marques.get) for cat, (_, _, _, marques) in g.CATALOGUE.items()
    }
    assert observe == attendu
