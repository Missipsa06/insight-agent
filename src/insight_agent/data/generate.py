"""Génère un panel d'achats synthétique dans DuckDB.

Usage : uv run python -m insight_agent.data.generate
"""

import collections
from datetime import date
from pathlib import Path
from typing import get_args

import duckdb
import numpy as np
from pydantic import BaseModel

from insight_agent.schemas import (
    Achat,
    Enseigne,
    Foyer,
    Produit,
    Region,
)

ENSEIGNES = [
    ("Hyper Géant Sud", "hyper"),
    ("MaxiPrix", "discount"),
    ("ProxiMarket", "proxi"),
    ("Drive Rapide", "drive"),
    ("Supermarché Central", "super"),
    ("Discount Express", "discount"),
    ("Hyper Marché Nord", "hyper"),
]

CATALOGUE = {
    "eaux": (
        "l",
        [0.5, 1.0, 1.5],
        0.40,
        {"Source Claire": 5, "Montagne Pure": 3, "Aqua Vita": 2, "Eau Libre": 1},
    ),
    "ultra_frais": (
        "kg",
        [0.125, 0.5, 1.0],  # pot individuel, pack de 4, pack de 8
        3.50,
        {
            "Laiterie du Val": 6,
            "Ferme Bleue": 3,
            "Yaourtine": 2,
            "Lacto Douceur": 2,
            "Petit Pre": 1,
        },
    ),
    "cafe": (
        "kg",
        [0.25, 0.5, 1.0],
        12.00,
        {"Grand Arome": 5, "Torref Maison": 3, "Cafe Noble": 2, "Moka Express": 1},
    ),
    "lessive": (
        "l",
        [1.0, 1.5, 3.0],
        5.00,
        {"Blanc Eclat": 4, "Lavanova": 3, "Fraicheur Plus": 2, "Eco Linge": 2, "Net Prix": 1},
    ),
}

FACTEUR_FORMAT = {
    "discount": 0.85,  # prix bas, assortiment court
    "hyper": 0.95,  # volumes élevés, prix agressifs
    "drive": 0.97,  # aligné sur l'hyper, plus un léger coût de service
    "super": 1.00,  # référence
    "proxi": 1.15,  # centre-ville, dépannage : on paie la proximité
}

SEED = 42
N_FOYERS = 3_000
N_FOYERS_FRANCE = 30_000_000
N_SEMAINES = 104
ACHATS_PAR_FOYER = 100
FORME_GAMMA = 1.5
PART_ENSEIGNE_PRINCIPALE = 0.7  # 70 % des achats sont faits dans l'enseigne principale du foyer
DATE_DEBUT = date(2024, 1, 1)  # un lundi
DB_PATH = Path("data/panel.duckdb")


def generer_foyers(rng: np.random.Generator) -> list[Foyer]:
    regions = rng.choice(get_args(Region), size=N_FOYERS)
    tailles = rng.choice(
        np.arange(1, 7), size=N_FOYERS, p=[0.37, 0.33, 0.13, 0.11, 0.04, 0.02]
    )  # ordres de grandeur INSEE, arrondis
    poids_bruts = rng.lognormal(mean=0.0, sigma=0.3, size=N_FOYERS)
    poids = poids_bruts * (N_FOYERS_FRANCE / poids_bruts.sum())
    return [
        Foyer(id_foyer=i + 1, region=regions[i], taille_foyer=tailles[i], poids=poids[i])
        for i in range(N_FOYERS)
    ]


def generer_enseignes() -> list[Enseigne]:

    return [
        Enseigne(id_enseigne=i, nom=nom, format=fmt)
        for i, (nom, fmt) in enumerate(ENSEIGNES, start=1)
    ]


def generer_produits(rng: np.random.Generator) -> tuple[list[Produit], dict[int, float]]:
    produits: list[Produit] = []
    prix: dict[int, float] = {}

    for categorie, (unite, contenances, prix_base, marques) in CATALOGUE.items():
        for marque in marques:
            n_produits = rng.integers(5, 11)
            for _ in range(n_produits):
                id_produit = len(produits) + 1  # compteur : 1, 2, 3…
                contenance = rng.choice(contenances)
                produits.append(
                    Produit(
                        id_produit=id_produit,
                        libelle=f"{marque} {contenance} {unite}",
                        marque=marque,
                        categorie=categorie,
                        contenance=contenance,
                        unite=unite,
                    )
                )
                # prix d'UNE unité vendue = prix au kg/l × contenance × variation
                prix[id_produit] = prix_base * contenance * rng.uniform(0.9, 1.1)
    return produits, prix


def generer_achats(
    rng: np.random.Generator,
    foyers: list[Foyer],
    produits: list[Produit],
    prix: dict[int, float],
    enseignes: list[Enseigne],
) -> dict[str, np.ndarray]:
    """Retourne les colonnes de la table achats (une clé par champ de Achat)."""
    # nombre d'achats par foyer : tous les foyers achètent-ils autant ? (gros/petits acheteurs)
    intensite = rng.gamma(shape=FORME_GAMMA, scale=ACHATS_PAR_FOYER / FORME_GAMMA, size=N_FOYERS)
    n_achats = rng.poisson(intensite)
    n = n_achats.sum()
    nb_refs = collections.Counter(p.marque for p in produits)
    poids = []
    for p in produits:
        poids.append(CATALOGUE[p.categorie][3][p.marque] / nb_refs[p.marque])
    poids = np.array(poids)
    probas = poids / sum(poids)
    # ponytail: choix de marque indépendant du foyer (pas de fidélité marque),
    # à revoir pour le KPI fidélité (J3)
    ids_produits = rng.choice([p.id_produit for p in produits], size=n, p=probas)
    ids_foyers = np.repeat([f.id_foyer for f in foyers], n_achats)
    # ponytail: une seule enseigne principale par foyer ; passer à 2 (ex. hyper + proxi)
    # si on veut analyser le cumul d'enseignes
    ids_enseignes = [e.id_enseigne for e in enseignes]
    principale = rng.choice(ids_enseignes, size=N_FOYERS)
    principale_par_achat = np.repeat(principale, n_achats)
    au_hasard = rng.choice(ids_enseignes, size=n)
    fidele = rng.random(n) < PART_ENSEIGNE_PRINCIPALE
    ids_enseignes_achat = np.where(fidele, principale_par_achat, au_hasard)
    # ponytail: dates uniformes, pas de saisonnalité
    jours = rng.integers(0, N_SEMAINES * 7, size=n_achats.sum())
    dates = np.datetime64(DATE_DEBUT) + jours
    quantites = rng.choice([1, 2, 3, 4], size=n, p=[0.75, 0.17, 0.05, 0.03])
    table_prix = np.zeros(max(prix) + 1)
    table_prix[list(prix)] = list(prix.values())
    prix_par_achat = table_prix[ids_produits]
    table_facteur = np.zeros(max(ids_enseignes) + 1)
    for e in enseignes:
        table_facteur[e.id_enseigne] = FACTEUR_FORMAT[e.format]
    facteur_par_achat = table_facteur[ids_enseignes_achat]
    bruit = rng.uniform(0.95, 1.05, size=n)
    montants = np.round(quantites * prix_par_achat * facteur_par_achat * bruit, 2)
    return {
        "id_foyer": ids_foyers,
        "id_produit": ids_produits,
        "id_enseigne": ids_enseignes_achat,
        "date_achat": dates,
        "quantite": quantites,
        "montant": montants,
    }


def _colonnes(modeles: list[BaseModel]) -> dict[str, np.ndarray]:
    lignes = [m.model_dump() for m in modeles]
    return {k: np.array([ligne[k] for ligne in lignes]) for k in lignes[0]}


def ecrire_duckdb(tables: dict[str, dict], chemin: Path = DB_PATH) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.unlink(missing_ok=True)
    with duckdb.connect(str(chemin)) as con:
        for nom, colonnes in tables.items():
            # DuckDB ne lit pas datetime64[D] : on passe en [s] puis on recaste en DATE
            dates = [k for k, v in colonnes.items() if v.dtype.kind == "M"]
            con.register(
                "_src",
                {k: v.astype("datetime64[s]") if k in dates else v for k, v in colonnes.items()},
            )
            con.execute(f"CREATE TABLE {nom} AS SELECT * FROM _src")
            con.unregister("_src")
            for k in dates:
                con.execute(f"ALTER TABLE {nom} ALTER {k} TYPE DATE")


def main(chemin: Path = DB_PATH) -> None:
    rng = np.random.default_rng(SEED)
    foyers = generer_foyers(rng)
    enseignes = generer_enseignes()
    produits, prix = generer_produits(rng)
    achats = generer_achats(rng, foyers, produits, prix, enseignes)
    n_achats = len(achats["id_foyer"])
    echantillon = rng.choice(n_achats, size=1_000, replace=False)
    # Pydantic sur un échantillon : attrape les bugs systématiques (types, contraintes).
    # Les cas rares sont couverts par les contrôles SQL de tests/test_data.py.
    for i in echantillon:
        Achat(**{nom: colonne[i].item() for nom, colonne in achats.items()})

    ecrire_duckdb(
        {
            "foyers": _colonnes(foyers),
            "enseignes": _colonnes(enseignes),
            "produits": _colonnes(produits),
            "achats": achats,
        },
        chemin,
    )
    print(f"{chemin} : {len(foyers)} foyers, {len(produits)} produits, {n_achats} achats")


if __name__ == "__main__":
    main()
