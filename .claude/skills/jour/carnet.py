"""Extrait du Carnet le texte d'un jour ou d'une leçon.

Usage : uv run python .claude/skills/jour/carnet.py J1      (un jour)
        uv run python .claude/skills/jour/carnet.py 11.03   (une leçon)
"""

import json
import sys
from html.parser import HTMLParser
from pathlib import Path

CARNET = Path(__file__).parents[3] / "Carnet_AI_Engineering.html"
IGNORES = ("style", "script", "button", "svg")


class Extracteur(HTMLParser):
    def __init__(self, cible: str):
        super().__init__()
        jour = cible.upper().startswith("J")
        self.cle, self.valeur = ("data-day", cible.upper()) if jour else ("data-code", cible)
        self.tag, self.profondeur = None, 0
        self.out: list[str] = []
        self.ignore = 0
        self.quiz: list[str] | None = None
        self.pre = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.tag is None:
            if a.get(self.cle) == self.valeur:
                self.tag, self.profondeur = tag, 1
            return
        if tag == self.tag:
            self.profondeur += 1
        if tag == "script" and "quiz-data" in a.get("class", ""):
            self.quiz = []
        elif tag in IGNORES:
            self.ignore += 1
        elif tag in ("h1", "h2", "h3", "h4"):
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "pre":
            self.pre = True
            self.out.append("\n```\n")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag in ("td", "th"):
            self.out.append(" | ")
        elif tag == "div" and "panel" in a.get("class", "").split():
            self.out.append("\n\n>>> ENCADRÉ")

    def handle_endtag(self, tag):
        if self.tag is None:
            return
        if tag == self.tag:
            self.profondeur -= 1
            if self.profondeur == 0:
                self.tag = None
                return
        if tag == "script" and self.quiz is not None:
            quiz = json.loads("".join(self.quiz))
            self.out.append("\n\n## Quiz\n" + json.dumps(quiz, ensure_ascii=False, indent=1))
            self.quiz = None
        elif tag in IGNORES:
            self.ignore = max(0, self.ignore - 1)
        elif tag == "pre":
            self.pre = False
            self.out.append("\n```\n")
        elif tag in ("p", "tr", "div", "h1", "h2", "h3", "h4"):
            self.out.append("\n")

    def handle_data(self, data):
        if self.tag is None or self.ignore:
            return
        if self.quiz is not None:
            self.quiz.append(data)
        elif self.pre:
            self.out.append(data)
        elif data.strip():
            avant = " " if data[0].isspace() else ""
            apres = " " if data[-1].isspace() else ""
            self.out.append(avant + " ".join(data.split()) + apres)


if __name__ == "__main__":
    e = Extracteur(sys.argv[1])
    e.feed(CARNET.read_text(encoding="utf-8"))
    texte = "".join(e.out)
    while "\n\n\n" in texte:
        texte = texte.replace("\n\n\n", "\n\n")
    sys.stdout.reconfigure(encoding="utf-8")
    print(texte.strip() or f"Rien trouvé pour {sys.argv[1]}")
