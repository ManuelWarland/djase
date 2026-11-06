"""Où Djåse range chaque chose (version grand public, décision du 05/11/2026).

- PROGRAMME : ce qui est livré avec Djåse (page de l'interface, icône, llama-server, dictionnaire de départ neutre).
  Dossier du code en développement ; dossier de l'exe (`sys._MEIPASS`) une fois assemblé par PyInstaller.
- DONNEES : tout ce qui est propre à la personne qui dicte (réglages, historique, journal, audio gardé, dictionnaire,
  textes rapides) : `%LOCALAPPDATA%\\Djåse`. Jamais dans le dossier du programme : une installation publiée reste
  vierge, et le dossier d'un programme installé n'est pas modifiable.
- MODELES : les modèles de reconnaissance et de langue (plusieurs Go). Le dossier `modeles` du code s'il existe
  (développement, PC de Manu), sinon `%LOCALAPPDATA%\\Djåse\\modeles` (téléchargés au premier lancement).

L'import ne déplace RIEN : `preparer()` est appelé par dictee.main(), après le verrou « un seul Djåse » et hors mode
test (un ancien Djåse encore en marche aurait sinon continué d'écrire dans le dossier du code, relecture du 05/11).
"""
import os
import shutil
import sys
from pathlib import Path

FIGE = bool(getattr(sys, "frozen", False))
CODE = Path(__file__).resolve().parent
PROGRAMME = Path(getattr(sys, "_MEIPASS", CODE)) if FIGE else CODE

DONNEES = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Djåse"
DONNEES.mkdir(parents=True, exist_ok=True)

if os.environ.get("DJASE_MODELES"):  # désigné à la main (essai de l'exe avec les modèles déjà présents sur le PC)
    MODELES = Path(os.environ["DJASE_MODELES"])
elif (CODE / "modeles").is_dir() and not FIGE:
    MODELES = CODE / "modeles"
else:
    MODELES = DONNEES / "modeles"

# Données de la personne, déplacées une fois du dossier du code vers DONNEES (développement seulement)
FICHIERS = ["reglages.json", "journal.txt", "journal.ancien.txt", "historique.jsonl", "textes_rapides.json"]
DOSSIERS = ["audio", "echantillons"]


def _deplacer(source, cible):
    """Copie sous un nom provisoire, renommée seulement une fois complète, puis source effacée : une copie interrompue
    entre deux disques (E: vers C:) ne laisse jamais un fichier coupé à la place du bon (relecture du 05/11)."""
    partiel = cible.with_name(cible.name + ".partiel")
    shutil.copy2(source, partiel)
    os.replace(partiel, cible)
    source.unlink()


def _mis_de_cote(chemin):
    """Nom libre pour garder une copie restée côté code : historique.non_migre.jsonl, …non_migre-2…"""
    n = 1
    while True:
        cible = chemin.with_name(f"{chemin.stem}.non_migre{'' if n == 1 else f'-{n}'}{chemin.suffix}")
        if not cible.exists():
            return cible
        n += 1


def preparer():
    """Déplace les données restées dans le dossier du code et pose le dictionnaire de départ s'il manque.
    Rien n'est écrasé ni ignoré en silence. Rend ce qui a été fait, pour le journal."""
    faits = []
    if not FIGE:
        for nom in FICHIERS:
            source, cible = CODE / nom, DONNEES / nom
            if not source.exists():
                continue
            try:
                if cible.exists() and nom.startswith("journal"):
                    # deux journaux (une ligne écrite avant le déplacement) : l'ancien d'abord, puis le nouveau
                    partiel = cible.with_name(cible.name + ".partiel")
                    partiel.write_bytes(source.read_bytes() + cible.read_bytes())
                    os.replace(partiel, cible)
                    source.unlink()
                    faits.append(f"{nom} : les deux journaux réunis dans {DONNEES}")
                elif cible.exists():
                    garde = _mis_de_cote(source)
                    source.rename(garde)
                    faits.append(f"{nom} présent des deux côtés : celui de {DONNEES} est utilisé, "
                                 f"l'autre gardé sous {garde}")
                else:
                    _deplacer(source, cible)
                    faits.append(f"{nom} déplacé vers {DONNEES}")
            except OSError as e:
                faits.append(f"{nom} PAS déplacé ({e}) : reste dans {CODE}")
        for nom in DOSSIERS:
            source, cible = CODE / nom, DONNEES / nom
            if not source.is_dir():
                continue
            # fichier par fichier : une copie interrompue reprend au lancement suivant, rien n'est écrasé
            deplaces, doublons, echecs = 0, 0, 0
            for f in source.rglob("*"):
                if not f.is_file():
                    continue
                dest = cible / f.relative_to(source)
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if f.name.endswith(".partiel"):
                        continue
                    if dest.exists():
                        _deplacer(f, _mis_de_cote(dest))  # les deux gardés côte à côte, rien d'écrasé
                        doublons += 1
                    else:
                        _deplacer(f, dest)
                        deplaces += 1
                except OSError:
                    echecs += 1
            if deplaces or doublons or echecs:
                faits.append(f"{nom} : {deplaces} fichier(s) déplacé(s) vers {DONNEES}"
                             + (f", {doublons} doublon(s) gardé(s) à côté sous un nom .non_migre" if doublons else "")
                             + (f", {echecs} échec(s) : restent dans {CODE}, nouvel essai au prochain lancement"
                                if echecs else ""))
            try:
                if not any(p.is_file() for p in source.rglob("*")):
                    shutil.rmtree(source)  # seulement des sous-dossiers vides (aucun fichier, avec ou sans extension)
            except OSError:
                pass
    dico = DONNEES / "dictionnaire.json"
    # Version publiée : dictionnaire de départ NEUTRE (dictionnaire.defaut.json) ; en développement, celui du dépôt
    # (le dictionnaire de Manu, qui reste sur son PC)
    depart = PROGRAMME / ("dictionnaire.defaut.json" if FIGE else "dictionnaire.json")
    if not dico.exists() and depart.exists():
        try:
            shutil.copy2(depart, dico)
            faits.append(f"dictionnaire de départ copié dans {DONNEES}")
        except OSError as e:
            faits.append(f"dictionnaire de départ PAS copié ({e})")
    return faits
