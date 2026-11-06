"""Maquette de la fenêtre de Djåse avec des données INVENTÉES, pour les captures du mode d'emploi.

Sert la vraie page ui/djase.html et répond à ses appels /api/... comme le ferait interface.Serveur, sans rien lire
des données de la personne qui utilise ce PC (historique, dictionnaire, réglages). Les écritures sont gardées en
mémoire seulement.

Usage : python guide/maquette.py [port]   puis   http://127.0.0.1:PORT/?page=historique
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ICI = Path(__file__).resolve().parent
PAGE = ICI.parent / "ui" / "djase.html"

STYLES = {
    "formel": ("Formel", "Majuscules et ponctuation complète.", "Salut, tu es libre demain midi ? On se voit à 12h si ça te va."),
    "decontracte": ("Décontracté", "Majuscules, sans point final.", "Salut, tu es libre demain midi ? On se voit à 12h si ça te va"),
    "tres_decontracte": ("Très décontracté", "Sans majuscules, sans virgules ni point final.",
                         "salut tu es libre demain midi ? on se voit à 12h si ça te va"),
    "enthousiaste": ("Enthousiaste", "Le point final devient un point d'exclamation.",
                     "Salut, tu es libre demain midi ? On se voit à 12h si ça te va !"),
}


def entree(jour, heure, texte, appli="", style="formel", entendu="", grammaire=None, correction=None, audio=True):
    return {"texte": texte, "heure": heure, "jour": jour, "entendu": entendu, "correction": correction,
            "grammaire": grammaire or [], "appli": appli, "style": style,
            "style_nom": STYLES[style][0].lower(), "audio": "exemple.wav" if audio else None}


ETAT = {
    "historique": [
        entree("jeudi 12 mars", "16:42", "Bonjour Claire, je vous envoie le devis corrigé avant vendredi. Bonne journée.",
               "outlook.exe", entendu="Bonjour Claire virgule euh je vous envoie le devis corrigé avant vendredi point bonne journée"),
        entree("jeudi 12 mars", "16:38", "on se retrouve à 19h devant le cinéma", "whatsapp", style="tres_decontracte"),
        entree("jeudi 12 mars", "15:05", "Les factures que j'ai reçues sont rangées dans le dossier Comptabilité.", "winword.exe",
               grammaire=[["reçu", "reçues"]]),
        entree("jeudi 12 mars", "11:20", "Rendez-vous chez le dentiste mardi à 14h30.", "",
               correction=["mercredi", "mardi"]),
        entree("mercredi 11 mars", "21:14", "Merci pour la recette, je la teste ce week-end !", "messenger", style="enthousiaste"),
        entree("mercredi 11 mars", "18:02", "Liste des courses :\n- pain\n- lait\n- tomates", "", audio=False),
    ],
    "dictionnaire": {
        "mots": ["Marten", "Ourthe", "Chaudfontaine", "LibreOffice"],
        "remplacements": {"j'ai mail": "Gmail", "la boulangerie Dubois": "Boulangerie Dubois"},
        "textes_rapides": {"insère mon adresse": "Rue des Tilleuls 12, 4000 Liège",
                           "insère ma signature": "Bien à vous,\nClaire Marten", "insère la date": "{date_longue}"},
        "par_appli": {}, "applis": ["outlook.exe", "whatsapp", "winword.exe", "messenger", "discord", "telegram", "gmail"],
        "motifs": 0,
    },
    "reglages": {
        "nettoyage": True, "grammaire": True, "historique": True, "audio": True, "style": "formel", "style_partout": False,
        "styles_applis": {"whatsapp": "tres_decontracte", "messenger": "enthousiaste"}, "entree": "auto",
        "ecoute_apres": 0.3, "double_espace": False, "canary_court": True, "oreilles": True, "son_messages": True,
        "demarrage": True,
    },
    "journal": [
        "12/03 16:00:01  Djåse 0.1.0 démarre",
        "12/03 16:00:04  Parakeet prêt (0,9 s)",
        "12/03 16:00:09  Canary prêt (5,1 s)",
        "12/03 16:00:14  Qwen prêt (10,2 s)",
        "12/03 16:00:14  Prêt : Ctrl+Win maintenu pour dicter, appui bref pour les mains libres, Ctrl+Win puis Maj pour "
        "recoller la dernière dictée, Ctrl+Win+Alt pour une commande",
        "12/03 16:42:10  Dictée (3,8 s) dans outlook.exe : « Bonjour Claire, je vous envoie le devis corrigé avant vendredi. »",
    ],
}


def reglages():
    return {**ETAT["reglages"], "styles": {k: {"nom": n, "desc": d, "exemple": e} for k, (n, d, e) in STYLES.items()},
            "applis_vues": ETAT["dictionnaire"]["applis"]}


class Gestionnaire(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _envoyer(self, corps, type_="application/json; charset=utf-8"):
        if not isinstance(corps, bytes):
            corps = json.dumps(corps, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", type_)
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def do_GET(self):
        chemin = urlparse(self.path).path
        if chemin == "/":
            return self._envoyer(PAGE.read_bytes(), "text/html; charset=utf-8")
        if chemin == "/icone.png":  # l'icône de l'appli, tirée du .ico livré avec Djåse
            import io
            from PIL import Image
            tampon = io.BytesIO()
            Image.open(ICI.parent / "djase_nuit.ico").save(tampon, format="PNG")
            return self._envoyer(tampon.getvalue(), "image/png")
        if chemin == "/api/etat":
            return self._envoyer({"texte": "Djåse · prêt", "page": None, "etat": "pret", "memoire": "6,9 Go",
                                  "qwen": "prêt", "canary": "prêt", "historique": 1})
        if chemin == "/api/historique":
            return self._envoyer(ETAT["historique"])
        if chemin == "/api/dictionnaire":
            return self._envoyer(ETAT["dictionnaire"])
        if chemin == "/api/reglages":
            return self._envoyer(reglages())
        if chemin == "/api/journal":
            return self._envoyer(ETAT["journal"])
        self.send_error(404)

    def do_POST(self):
        donnees = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        chemin = urlparse(self.path).path
        if chemin == "/api/dictionnaire":
            ETAT["dictionnaire"].update(donnees)
            return self._envoyer(ETAT["dictionnaire"])
        if chemin == "/api/reglages":
            ETAT["reglages"].update(donnees)
            return self._envoyer(reglages())
        return self._envoyer({})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"Maquette : http://127.0.0.1:{port}/?page=historique")
    ThreadingHTTPServer(("127.0.0.1", port), Gestionnaire).serve_forever()
