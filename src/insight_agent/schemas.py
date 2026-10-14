from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

Region = Literal["Ile-de-France", "Nord", "Est", "Ouest", "Sud-Ouest", "Sud-Est"]


class Foyer(BaseModel):
    """
    Foyer schema
    """

    id_foyer: int = Field(gt=0)
    region: Region
    taille_foyer: int = Field(gt=0, le=8)
    poids: float = Field(gt=0)


Format = Literal["hyper", "super", "proxi", "discount", "drive"]


class Enseigne(BaseModel):
    """
    Enseigne schema
    """

    id_enseigne: int = Field(gt=0)
    nom: str
    format: Format


Categorie = Literal["ultra_frais", "eaux", "cafe", "lessive"]
Unite = Literal["kg", "l"]


class Produit(BaseModel):
    """
    Produit schema
    """

    id_produit: int = Field(gt=0)
    libelle: str
    marque: str
    categorie: Categorie
    contenance: float = Field(gt=0)
    unite: Unite


class Achat(BaseModel):
    """
    Achat schema
    """

    id_foyer: int = Field(gt=0)
    id_enseigne: int = Field(gt=0)
    id_produit: int = Field(gt=0)
    date_achat: date
    quantite: int = Field(gt=0)
    montant: float = Field(gt=0)
