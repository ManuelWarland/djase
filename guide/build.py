# -*- coding: utf-8 -*-
"""Construit le mode d'emploi PDF de Djåse (recette reprise du guide de CmdImg).

Usage :  .venv\\Scripts\\python.exe guide\\build.py
Sortie : guide/sortie/Djase_Mode_emploi.html et guide/sortie/Djase_Mode_emploi.pdf (livré avec Djåse)

Captures : guide/captures/*.png, prises sur guide/maquette.py (données inventées, jamais celles de l'utilisateur).
Le script s'arrête si un bloc déborde de sa page à l'impression.
Texte public : règles d'écriture de Manu (pas de tiret long, vouvoiement).
"""
import html
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ICI = Path(__file__).resolve().parent
RACINE = ICI.parent
CHROME = next(p for p in (Path(r"C:/Program Files/Google/Chrome/Application/chrome.exe"),
                          Path(r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")) if p.exists())
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
        "novembre", "décembre"]
VERSION = re.search(r'#define Version "([^"]+)"', (RACINE / "djase.iss").read_text(encoding="utf-8")).group(1)
AUJ = date.today()
DATE_FR = f"{AUJ.day} {MOIS[AUJ.month - 1]} {AUJ.year}"
CAP = "../captures/"


def k(*touches):
    return "+".join(f"<kbd>{t}</kbd>" for t in touches)


def dit(texte):
    return f'<span class="dit">{texte}</span>'


def res(texte):
    return f'<span class="res">{texte}</span>'


class Doc:
    def __init__(self):
        self.pages, self.som = [], []

    def page(self, pid, dedans, rubrique, titre_som=None, partie=None, onglet=None, classe=""):
        tete = (f'<div class="haut"><span class="marque">Dj<i>å</i>se · Mode d\'emploi</span><span>{rubrique}</span></div>')
        pied = f'<div class="pied"><span>{rubrique}</span><span><b>{{{{PAGE}}}}</b></span></div>'
        ong = f'<div class="onglet" style="top:{onglet}mm">{rubrique}</div>' if onglet else ""
        self.pages.append((pid, f'<section class="page {classe}" id="{pid}">{tete}{ong}<div class="corps">{dedans}</div>{pied}</section>'))
        if titre_som:
            self.som.append((partie, titre_som, pid))

    def brut(self, pid, code):
        self.pages.append((pid, code))


# ---------------------------------------------------------------------------------------------------------------
def couverture(d):
    d.brut("couverture", f'''
<section class="page couv" id="couverture">
  <div class="halo"></div>
  <div class="tete">
    <img src="{CAP}icone.png" alt="">
    <div class="etiquette">Mode d'emploi · version {VERSION}</div>
    <h1>Dj<span>å</span>se</h1>
    <p class="sous">La dictée vocale qui reste sur votre PC. Vous parlez, le texte s'écrit, dans n'importe quel programme de Windows.</p>
  </div>
  <div class="trois">
    <div><h3>100 % local</h3><p>Pas de compte, pas d'abonnement, pas de serveur. Votre voix ne quitte jamais votre ordinateur.</p></div>
    <div><h3>Un texte propre</h3><p>Les « euh » disparaissent, la ponctuation se place, les accords sont vérifiés.</p></div>
    <div><h3>À la voix</h3><p>« à la ligne », « efface », « en gras », « corrige Martin par Marten » : Djåse obéit.</p></div>
  </div>
  <div class="geste-couv">Maintenez {k("Ctrl", "Win")}, parlez, relâchez. C'est tout.</div>
  <div class="bas"><span>Manuel Warland · édition du {DATE_FR}</span><span>Logiciel libre, licence GNU GPL 3</span></div>
</section>''')


def sommaire(d):
    d.page("sommaire", f'''
<div class="surtitre">Pour commencer</div>
<h1 class="t">Djåse en une minute</h1>
<p class="chapeau">Djåse transforme votre voix en texte, là où se trouve votre curseur : un courriel, un message, un document, une conversation avec une IA. Tout se passe sur votre PC.</p>
<div class="g3">
  <div class="boite sombre"><h4>1 · Maintenez</h4><p>Gardez {k("Ctrl", "Win")} enfoncées. Une petite pastille apparaît en bas de l'écran : Djåse écoute.</p></div>
  <div class="boite sombre"><h4>2 · Parlez</h4><p>Naturellement, par phrases entières. Dites la ponctuation si vous voulez la choisir vous-même.</p></div>
  <div class="boite sombre"><h4>3 · Relâchez</h4><p>Le texte, relu et nettoyé, s'écrit à l'endroit du curseur en quelques instants.</p></div>
</div>
<h2 class="t">Sommaire</h2>
<div class="som">{{{{SOMMAIRE}}}}</div>''', "Sommaire")


def installer(d):
    d.page("installer", f'''
<div class="surtitre">Bien démarrer</div>
<h1 class="t">Installer Djåse</h1>
<p class="chapeau">L'installation ne demande pas de droits d'administrateur : Djåse s'installe pour votre compte Windows seulement.</p>
<div class="etape"><div class="num">1</div><div><h4>Lancez l'installateur</h4><p>Double-cliquez sur <code>Djase-{VERSION}-installation.exe</code>. L'installateur affiche la licence (GNU GPL 3), puis propose de <b>lancer Djåse au démarrage de Windows</b>. Laissez la case cochée si vous comptez dicter tous les jours.</p></div></div>
<div class="etape"><div class="num">2</div><div><h4>Premier lancement : les modèles</h4><p>Djåse a besoin de ses modèles de reconnaissance de la voix et de relecture du texte : environ <b>4,3 Go</b>, téléchargés une seule fois depuis Hugging Face. Une fenêtre montre la progression. C'est la seule fois où Djåse utilise Internet.</p></div></div>
<div class="etape"><div class="num">3</div><div><h4>Le chargement</h4><p>À chaque démarrage, Djåse charge ses modèles en mémoire : de quelques secondes à une minute selon le PC. Quand tout est prêt, l'info-bulle de l'icône près de l'horloge indique <b>Djåse · prêt</b>.</p></div></div>
<div class="etape"><div class="num">4</div><div><h4>Votre première dictée</h4><p>Ouvrez le Bloc-notes, cliquez dans la page, maintenez {k("Ctrl", "Win")} et dites : {dit("Bonjour, ceci est ma première dictée point")}. Relâchez.</p></div></div>
<h2 class="t">Bon à savoir</h2>
<div class="g2">
  <div class="info"><b>Téléchargement interrompu ?</b> Coupure du réseau, bouton Annuler, PC éteint : rien n'est perdu. Relancez Djåse, le téléchargement reprend là où il s'était arrêté. Chaque fichier est contrôlé avant d'être utilisé.</div>
  <div class="info"><b>Où va Djåse ?</b> Le programme dans <code>%LOCALAPPDATA%\\Programs\\Djåse</code>, vos données et les modèles dans <code>%LOCALAPPDATA%\\Djåse</code>.</div>
  <div class="att"><b>Place disque.</b> Prévoyez environ 4,5 Go libres sur le disque C: avant le premier lancement. Djåse vérifie la place disponible avant de télécharger.</div>
  <div class="att"><b>Désinstaller.</b> Menu Démarrer, <b>Désinstaller Djåse</b>. Vos réglages, votre historique et votre dictionnaire sont gardés : supprimez le dossier <code>%LOCALAPPDATA%\\Djåse</code> pour tout effacer.</div>
</div>''', "Bien démarrer", "Installer Djåse", "Bien démarrer", onglet=30)


def icone(d):
    d.page("icone", f'''
<div class="surtitre">Bien démarrer</div>
<h1 class="t">L'icône près de l'horloge</h1>
<p class="chapeau">Djåse n'a pas de grande fenêtre ouverte en permanence. Il vit dans une petite icône, à droite de la barre des tâches, à côté de l'horloge.</p>
<h2 class="t">Rendre l'icône toujours visible</h2>
<p class="discret">Windows range souvent les nouvelles icônes derrière la petite flèche <b>^</b>. Pour garder celle de Djåse en vue :</p>
<ol class="n" style="margin-top:2mm">
  <li>ouvrez les <b>Paramètres</b> de Windows ({k("Win", "I")}) ;</li>
  <li>allez dans <b>Personnalisation</b>, puis <b>Barre des tâches</b> ;</li>
  <li>dépliez <b>Autres icônes de la barre d'état système</b> ;</li>
  <li>activez l'interrupteur en face de <b>Djåse</b>.</li>
</ol>
<h2 class="t">Le menu de l'icône</h2>
<p class="discret" style="margin-bottom:2mm">Un clic droit sur l'icône ouvre le menu. Un double-clic ouvre directement la fenêtre de Djåse.</p>
<table class="t">
  <tr><th>Entrée du menu</th><th>Ce qu'elle fait</th></tr>
  <tr><td>Première ligne (grisée)</td><td>L'état de Djåse et la mémoire qu'il occupe, par exemple <i>Djåse · prêt · Qwen prêt · 6,9 Go</i>.</td></tr>
  <tr><td>Ouvrir Djåse</td><td>La fenêtre de Djåse : historique, dictionnaire, réglages…</td></tr>
  <tr><td>Copier la dernière dictée</td><td>Met la dernière dictée dans le presse-papiers, pour la coller où vous voulez avec {k("Ctrl", "V")}.</td></tr>
  <tr><td>Mettre en pause / Reprendre</td><td>En pause, {k("Ctrl", "Win")} ne dicte plus et Qwen libère sa mémoire. Pratique pour un jeu ou un logiciel gourmand.</td></tr>
  <tr><td>Historique… à Réglages…</td><td>Ouvrent la fenêtre directement sur la bonne page.</td></tr>
  <tr><td>Démarrer avec Windows</td><td>Coché : Djåse se lance tout seul à l'ouverture de votre session.</td></tr>
  <tr><td>Ouvrir le dossier</td><td>Le dossier de vos données (<code>%LOCALAPPDATA%\\Djåse</code>).</td></tr>
  <tr><td>Quitter Djåse</td><td>Arrête Djåse et libère toute sa mémoire.</td></tr>
</table>
<div class="info" style="margin-top:4mm"><b>Survolez l'icône</b> pour lire son état : <i>chargement</i>, <i>prêt</i> ou <i>en pause</i>. Son dessin change aussi pendant le chargement et en pause.</div>''',
           "Bien démarrer", "L'icône près de l'horloge", "Bien démarrer", onglet=30)


def dicter(d):
    barres = lambda c="": f'<span class="pastille {c}">' + "".join(f'<i style="height:{h}mm"></i>' for h in (1.6, 3.4, 5, 2.8, 4.2, 2, 1.4)) + "</span>"
    d.page("dicter", f'''
<div class="surtitre">Dicter</div>
<h1 class="t">Les quatre gestes</h1>
<p class="chapeau">Tout passe par {k("Ctrl", "Win")}, les deux touches en bas à gauche du clavier. Djåse marche dans tout programme où vous pouvez taper du texte.</p>
<div class="geste"><div class="touches">{k("Ctrl", "Win")} <span class="petit discret">maintenues</span></div><div><h4>Dicter</h4><p>Parlez tant que vous gardez les touches enfoncées, relâchez pour écrire. Djåse écoute encore 0,3 seconde après le relâchement, pour ne pas couper votre dernier mot.</p></div></div>
<div class="geste"><div class="touches">{k("Ctrl", "Win")} <span class="petit discret">appui bref</span></div><div><h4>Les mains libres</h4><p>Un appui court, puis lâchez : Djåse écoute sans que vous teniez les touches. Appuyez de nouveau sur {k("Ctrl", "Win")} pour terminer, ou sur {k("Échap")} pour tout annuler.</p></div></div>
<div class="geste"><div class="touches">{k("Ctrl", "Win")} puis {k("Maj")}</div><div><h4>Recoller la dernière dictée</h4><p>Gardez {k("Ctrl", "Win")} et appuyez sur {k("Maj")} : la dernière dictée est écrite à nouveau, par exemple dans une autre fenêtre. Ça marche même après un redémarrage de Djåse.</p></div></div>
<div class="geste"><div class="touches">{k("Ctrl", "Win", "Alt")}</div><div><h4>Le mode commande</h4><p>Rien n'est écrit : Djåse attend un ordre, comme {dit("efface tout")} ou {dit("corrige Martin par Marten")}, sans qu'il faille dire « Djåse » devant. {k("Alt")} peut s'ajouter avant ou pendant la dictée.</p></div></div>
<div class="att" style="margin-top:3mm"><b>Annuler une dictée en cours :</b> appuyez sur n'importe quelle autre touche pendant que vous tenez {k("Ctrl", "Win")}. C'est voulu : {k("Ctrl", "Win", "→")} et les autres raccourcis de Windows restent utilisables sans rien écrire.</div>
<h2 class="t">La pastille</h2>
<div class="g2">
  <div class="encart"><div class="bandeau">Ce qu'elle montre</div><div class="dedans">
    <p class="lp">{barres()}<span>Barres blanches : Djåse écoute, elles suivent votre voix.</span></p>
    <p class="lp">{barres("or")}<span>Barres dorées : mode commande.</span></p>
    <p class="lp"><span class="pastille txt">Effacé</span><span>Un court message après une commande, avec un petit son (réglable).</span></p>
  </div></div>
  <div class="encart"><div class="bandeau">Pour bien dicter</div><div class="dedans"><ul class="p">
    <li>Parlez <b>par phrases entières</b>, à votre rythme normal : Djåse comprend mieux le sens d'une phrase complète.</li>
    <li>Gardez le micro <b>à la même distance</b>, sans souffler dedans.</li>
    <li>Cliquez d'abord là où le texte doit aller : Djåse écrit <b>à l'endroit du curseur</b>.</li>
    <li>Djåse écoute le <b>micro par défaut</b> de Windows (Paramètres, Système, Son, Entrée).</li>
  </ul></div></div>
</div>''', "Dicter", "Les quatre gestes et la pastille", "Dicter", onglet=80)


def ponctuation(d):
    d.page("ponctuation", f'''
<div class="surtitre">Dicter</div>
<h1 class="t">Ponctuation et mise en page</h1>
<p class="chapeau">Djåse place seul la ponctuation courante. Pour la choisir vous-même, dites-la à sa place, comme si vous lisiez le texte à voix haute.</p>
<div class="g2">
<table class="t">
  <tr><th>Vous dites</th><th>Djåse écrit</th></tr>
  <tr><td>{dit("virgule")}</td><td>{res(",")}</td></tr>
  <tr><td>{dit("point")}</td><td>{res(".")}</td></tr>
  <tr><td>{dit("deux points")} ou {dit("double point")}</td><td>{res(":")}</td></tr>
  <tr><td>{dit("point-virgule")}</td><td>{res(";")}</td></tr>
  <tr><td>{dit("point d'interrogation")}</td><td>{res("?")}</td></tr>
  <tr><td>{dit("point d'exclamation")}</td><td>{res("!")}</td></tr>
  <tr><td>{dit("trois petits points")}</td><td>{res("...")}</td></tr>
  <tr><td>{dit("à la ligne")}</td><td>un retour à la ligne</td></tr>
  <tr><td>{dit("point à la ligne")}</td><td>un point, puis à la ligne</td></tr>
  <tr><td>{dit("nouveau paragraphe")}</td><td>une ligne vide</td></tr>
</table>
<table class="t large">
  <tr><th>Vous dites</th><th>Djåse écrit</th></tr>
  <tr><td>{dit("ouvrez les guillemets")} … {dit("fermez les guillemets")}</td><td>{res("« … »")}</td></tr>
  <tr><td>{dit("guillemets droits")}</td><td>{res('"…"')}</td></tr>
  <tr><td>{dit("ouvrez la parenthèse")} … {dit("fermez la parenthèse")}</td><td>{res("( … )")}</td></tr>
  <tr><td>de même : crochets, accolades, chevrons</td><td>{res("[ ]  { }  < >")}</td></tr>
  <tr><td>{dit("espace")} <span class="petit">(dit seul)</span></td><td>une espace</td></tr>
  <tr><td>{dit("entrée")} ou {dit("envoie")} <span class="petit">(dit seul)</span></td><td>la touche {k("Entrée")}</td></tr>
</table>
</div>
<h2 class="t">Les listes</h2>
<div class="g2">
  <div><p class="discret">Dites {dit("début de liste")}, puis chaque élément séparé par {dit("élément suivant")}, et terminez par {dit("fin de liste")}. Djåse écrit une liste à tirets, sans point au bout des éléments.</p>
  <p class="discret">Exemple dicté : {dit("Liste des courses deux points début de liste pain élément suivant lait élément suivant tomates fin de liste")}</p></div>
  <div class="boite" style="background:var(--creme)"><p class="mono" style="color:var(--encre);white-space:pre-line">Liste des courses :
- pain
- lait
- tomates</p></div>
</div>
<h2 class="t">Une commande dite seule</h2>
<div class="ok">Une dictée qui ne contient <b>que</b> {dit("virgule")}, {dit("point")}, {dit("à la ligne")} ou {dit("entrée")} est toujours comprise comme une commande, même pour un mot aussi court que « point ». Pratique pour ajouter un signe oublié juste après une dictée. Djåse écoute ces dictées très courtes avec plusieurs moteurs à la fois, pour ne pas les rater.</div>''',
           "Dicter", "Ponctuation et mise en page", "Dicter", onglet=80)


def corriger(d):
    d.page("corriger", f'''
<div class="surtitre">Dicter</div>
<h1 class="t">Corriger et mettre en forme à la voix</h1>
<p class="chapeau">Les commandes commencent par « Djåse », pour ne jamais être confondues avec une phrase. Avec {k("Ctrl", "Win", "Alt")} (mode commande), dites-les sans « Djåse ».</p>
<table class="t">
  <tr><th>Vous dites</th><th>Ce qui se passe</th></tr>
  <tr><td>{dit("Djåse, efface")}</td><td>efface le dernier mot écrit</td></tr>
  <tr><td>{dit("Djåse, efface tout")}</td><td>efface toute la dernière dictée</td></tr>
  <tr><td>{dit("Djåse, efface vraiment")}</td><td>retire le mot « vraiment » de la dernière dictée</td></tr>
  <tr><td>{dit("Djåse, corrige Martin par Marten")}</td><td>remplace le mot dans la dernière dictée (ou dans les dictées écrites d'affilée)</td></tr>
  <tr><td>{dit("Djåse, corrige Martin par Marten et retiens")}</td><td>pareil, et la correction est ajoutée au dictionnaire : la prochaine fois, Djåse écrira Marten tout seul</td></tr>
</table>
<p class="petit discret" style="margin-top:1.5mm">À la place de « efface », vous pouvez dire supprime, retire, annule ou enlève ; à la place de « corrige », remplace.</p>
<div class="att" style="margin-top:3mm"><b>Pourquoi « rien effacé » ?</b> Djåse ne corrige que le texte qu'il vient d'écrire, et seulement si vous n'avez rien tapé ni cliqué depuis : sinon, il ne sait plus où se trouve le curseur. Dans ce cas, {dit("Djåse, efface")} efface simplement le mot juste avant le curseur ({k("Ctrl", "Retour arrière")}).</div>
<h2 class="t">Gras, italique, barré, citation</h2>
<div class="g2">
  <div><p class="discret"><b>Dans la phrase.</b> {dit("en gras ceci est important fin du gras")}. Même chose avec <b>en italique</b> … <b>fin de l'italique</b>, <b>en barré</b> … <b>fin du barré</b>, et <b>citation</b> … <b>fin de citation</b>.</p>
  <p class="discret"><b>Après coup</b>, dit seul : {dit("en gras")} met le dernier mot en gras, {dit("tout en gras")} toute la dernière dictée. {dit("en citation")} fait de la dernière dictée une citation.</p></div>
  <table class="t formes">
    <tr><th>Programme</th><th>Résultat</th></tr>
    <tr><td>WhatsApp</td><td>{res("*gras*")} {res("_italique_")} {res("~barré~")}</td></tr>
    <tr><td>Word, Outlook, Gmail, Excel, PowerPoint, Thunderbird</td><td>le texte sans marques ; la citation entre « »</td></tr>
    <tr><td>Les autres (Discord, Obsidian, GitHub…)</td><td>{res("**gras**")} {res("*italique*")} {res("~~barré~~")}</td></tr>
  </table>
</div>
<h2 class="t">Ce que Djåse fait tout seul</h2>
<div class="g3">
  <div class="boite" style="background:var(--creme)"><h4>Il nettoie</h4><p>« euh », « bah », « ben » disparaissent. Une reprise comme « mardi, non, plutôt mercredi » devient « mercredi ».</p></div>
  <div class="boite" style="background:var(--creme)"><h4>Il relit</h4><p>Grammalecte corrige les accords et les conjugaisons dont il est certain. Les heures s'écrivent 14h30.</p></div>
  <div class="boite" style="background:var(--creme)"><h4>Il suit l'appli</h4><p>Chaque programme peut avoir son style (page Style). Deux dictées à la suite sont séparées par une espace.</p></div>
</div>
<p class="petit discret" style="margin-top:2.5mm">Un raccourci dicté s'écrit comme on l'attend : {dit("contrôle plus maj plus S")} donne {res("Ctrl+Maj+S")}.</p>''',
           "Dicter", "Corriger et mettre en forme", "Dicter", onglet=80)


def fenetre1(d):
    d.page("fenetre1", f'''
<div class="surtitre">La fenêtre de Djåse</div>
<h1 class="t">Historique et dictionnaire</h1>
<p class="chapeau">Double-cliquez sur l'icône près de l'horloge. En haut, l'état de Djåse : <b>PRÊT</b>, la mémoire utilisée, l'état de Qwen et de Canary.</p>
<div class="g2" style="grid-template-columns:1.35fr 1fr;align-items:start">
  <div><div class="capture"><img src="{CAP}historique.png" alt=""></div><p class="legende">La page Historique (exemple, données inventées).</p></div>
  <div><h2 class="t" style="margin-top:0">Historique</h2>
  <ul class="p">
    <li>Vos dictées, <b>des plus récentes aux plus anciennes</b>, classées par jour. Le champ du haut cherche un mot.</li>
    <li><b>Entendu</b> : ce que le moteur avait compris, quand le texte final est différent.</li>
    <li><b>Grammaire</b> et <b>Corrigé à la voix</b> : les corrections faites.</li>
    <li><b>Copier</b> remet le texte dans le presse-papiers ; <b>Écouter</b> rejoue le son, s'il est gardé.</li>
    <li>Gardé 1 an, le son 30 jours.</li>
  </ul></div>
</div>
<div class="g2" style="grid-template-columns:1fr 1.35fr;align-items:start;margin-top:5mm">
  <div><h2 class="t" style="margin-top:0">Dictionnaire</h2>
  <ul class="p">
    <li><b>Mots</b> : toujours écrits ainsi. Les noms propres, les marques, les termes de métier (Marten, Chaudfontaine, LibreOffice).</li>
    <li><b>Remplacements</b> : ce que Djåse entend de travers, à gauche, et ce qu'il doit écrire, à droite. Cliquez sur une ligne pour la modifier.</li>
    <li><b>Pour</b> : laissé vide, le vocabulaire vaut partout. Avec un nom de programme (<code>winword.exe</code>) ou un mot du titre de la fenêtre (<code>whatsapp</code>), seulement là.</li>
    <li>Pris en compte <b>dès la dictée suivante</b>.</li>
  </ul></div>
  <div><div class="capture"><img src="{CAP}dictionnaire.png" alt=""></div><p class="legende">La page Dictionnaire.</p></div>
</div>''', "La fenêtre", "Historique et dictionnaire", "La fenêtre de Djåse", onglet=130)


def fenetre2(d):
    d.page("fenetre2", f'''
<div class="surtitre">La fenêtre de Djåse</div>
<h1 class="t">Textes rapides et style</h1>
<div class="g2" style="grid-template-columns:1fr 1.35fr;align-items:start;margin-top:4mm">
  <div><h2 class="t" style="margin-top:0">Textes rapides</h2>
  <p class="discret">Une formule dite, un texte écrit. {dit("insère mon adresse")} donne votre adresse complète, n'importe où dans une dictée.</p>
  <ul class="p" style="margin-top:2mm">
    <li>Choisissez une formule <b>que vous ne dites pas par hasard</b> : « insère mon adresse » plutôt que « mon adresse ».</li>
    <li>Dans le texte, <code>{{date}}</code> donne 12/03/2026, <code>{{date_longue}}</code> jeudi 12 mars 2026 et <code>{{heure}}</code> 16h42, calculés au moment de la dictée.</li>
    <li>Un texte rapide peut tenir sur plusieurs lignes, comme une signature.</li>
  </ul></div>
  <div><div class="capture"><img src="{CAP}textes.png" alt=""></div><p class="legende">La page Textes rapides.</p></div>
</div>
<div class="g2" style="grid-template-columns:1.35fr 1fr;align-items:start;margin-top:5mm">
  <div><div class="capture"><img src="{CAP}style.png" alt=""></div><p class="legende">La page Style.</p></div>
  <div><h2 class="t" style="margin-top:0">Style</h2>
  <p class="discret">Quatre façons d'écrire, chacune montrée avec un exemple :</p>
  <table class="t" style="margin-top:1.5mm">
    <tr><td>Formel</td><td>majuscules et ponctuation complète</td></tr>
    <tr><td>Décontracté</td><td>majuscules, sans point final</td></tr>
    <tr><td>Très décontracté</td><td>sans majuscules, sans virgules ni point final</td></tr>
    <tr><td>Enthousiaste</td><td>le point final devient un point d'exclamation</td></tr>
  </table>
  <p class="discret" style="margin-top:2mm"><b>Exceptions par application</b> : WhatsApp en très décontracté, Word en formel… Un nom de programme ou un mot du titre suffit, « whatsapp » vaut pour l'appli comme pour l'onglet du navigateur. <b>Même style partout</b> ignore les exceptions.</p></div>
</div>''', "La fenêtre", "Textes rapides et style", "La fenêtre de Djåse", onglet=130)


def fenetre3(d):
    d.page("fenetre3", f'''
<div class="surtitre">La fenêtre de Djåse</div>
<h1 class="t">Réglages et journal</h1>
<p class="chapeau">Un réglage marqué d'une étoile prend effet au prochain lancement de Djåse (menu de l'icône, Quitter, puis relancer).</p>
<div class="g2" style="grid-template-columns:1fr 1fr;align-items:start">
  <div><div class="capture"><img src="{CAP}reglages.png" alt=""></div><p class="legende">La page Réglages.</p></div>
  <table class="t reg">
    <tr><th>Réglage</th><th>Pour quoi faire</th></tr>
    <tr><td>Nettoyage par Qwen *</td><td>retire hésitations et reprises. Coupé : Djåse est plus léger (environ 2,5 Go de mémoire au lieu de 7) et plus rapide, le texte un peu moins propre</td></tr>
    <tr><td>Grammaire *</td><td>accords et conjugaisons (Grammalecte)</td></tr>
    <tr><td>Canary pour les dictées courtes *</td><td>mieux comprendre les mots seuls (virgule, espace…) ; environ 1 Go de mémoire en plus</td></tr>
    <tr><td>Oreilles pour les commandes ratées</td><td>réécoute le son d'une dictée courte pour rattraper « à la ligne », « efface »…</td></tr>
    <tr><td>Voie du micro</td><td>pour une carte son à deux entrées : la 1, la 2, ou la plus forte</td></tr>
    <tr><td>Écoute après le relâchement</td><td>à augmenter si votre dernier mot est coupé</td></tr>
    <tr><td>Deux espaces après un point</td><td>l'habitude des machines à écrire</td></tr>
    <tr><td>Son des messages</td><td>un petit son quand la pastille affiche un message</td></tr>
    <tr><td>Garder l'historique, l'audio</td><td>la page Historique et le bouton Écouter</td></tr>
    <tr><td>Démarrer avec Windows</td><td>le même que dans le menu de l'icône</td></tr>
  </table>
</div>
<h2 class="t">Journal</h2>
<p class="discret">Tout ce que fait Djåse, ligne par ligne : chargement des modèles, dictées, commandes comprises ou non, erreurs. Utile pour comprendre un comportement inattendu. Le bouton <b>Copier tout</b> copie le journal, pour le joindre à un signalement de problème.</p>''', "La fenêtre", "Réglages et journal", "La fenêtre de Djåse", onglet=130)


def vie_privee(d):
    d.page("donnees", f'''
<div class="surtitre">Aide</div>
<h1 class="t">Vos données</h1>
<p class="chapeau">Djåse n'envoie rien sur Internet. Après le téléchargement des modèles, vous pouvez même couper la connexion : il dicte pareil.</p>
<div class="g2">
  <div class="encart"><div class="bandeau">Ce qui reste sur votre PC</div><div class="dedans">
  <p>Tout est rangé dans <code>%LOCALAPPDATA%\\Djåse</code> (menu de l'icône, <b>Ouvrir le dossier</b>) :</p>
  <table class="t" style="margin-top:1.5mm">
    <tr><td><code>historique.jsonl</code></td><td>vos dictées, gardées 1 an</td></tr>
    <tr><td><code>audio\\</code></td><td>le son de chaque dictée, gardé 30 jours</td></tr>
    <tr><td><code>dictionnaire.json</code></td><td>vos mots et remplacements</td></tr>
    <tr><td><code>textes_rapides.json</code></td><td>vos textes rapides</td></tr>
    <tr><td><code>reglages.json</code></td><td>vos réglages</td></tr>
    <tr><td><code>journal.txt</code></td><td>le journal</td></tr>
    <tr><td><code>modeles\\</code></td><td>les modèles téléchargés (4,3 Go)</td></tr>
  </table></div></div>
  <div>
    <div class="ok"><b>Rien ne sort.</b> La reconnaissance de la voix, la relecture par Qwen et la grammaire tournent sur votre processeur. Pas de compte, pas de statistiques d'utilisation, pas de mise à jour automatique.</div>
    <div class="info" style="margin-top:3mm"><b>Garder moins longtemps, ou rien.</b> Dans les Réglages, coupez <b>Garder l'audio</b> ou <b>Garder l'historique</b>. Le ménage des vieilles dictées et des vieux sons se fait à chaque lancement de Djåse.</div>
    <div class="info" style="margin-top:3mm"><b>Tout effacer.</b> Quittez Djåse et supprimez le dossier <code>%LOCALAPPDATA%\\Djåse</code>. Les modèles seront retéléchargés au prochain lancement.</div>
    <div class="att" style="margin-top:3mm"><b>Le seul accès à Internet</b> : le téléchargement des modèles, depuis Hugging Face, au premier lancement ou s'il en manque un.</div>
  </div>
</div>
<h2 class="t">Logiciel libre</h2>
<p class="discret">Djåse est publié sous licence GNU GPL 3 : vous pouvez l'utiliser, l'étudier, le modifier et le redistribuer, en laissant les mêmes libertés à ceux qui le reçoivent. Il est fourni sans aucune garantie. Il s'appuie sur les modèles Parakeet, Canary et FastConformer de NVIDIA (CC-BY 4.0), Qwen3 4B Instruct de l'équipe Qwen (Apache 2.0), llama.cpp (MIT) et Grammalecte (GPL 3). La liste complète est dans <code>MENTIONS.md</code>, à côté du programme, et sur la page <b>À propos</b> de la fenêtre.</p>''',
           "Aide", "Vos données", "Aide", onglet=180)


def depannage(d):
    q = lambda t, r: f'<div class="encart"><div class="bandeau">{t}</div><div class="dedans"><p>{r}</p></div></div>'
    d.page("depannage", f'''
<div class="surtitre">Aide</div>
<h1 class="t">Si quelque chose cloche</h1>
<p class="chapeau">La page <b>Journal</b> dit presque toujours ce qui s'est passé. Commencez par y jeter un œil.</p>
<div class="g2">
  {q("Ctrl+Win ne fait rien", "Djåse est-il lancé et prêt ? Survolez l'icône près de l'horloge. En pause, choisissez <b>Reprendre</b> dans son menu. Dans une fenêtre lancée en administrateur (Gestionnaire des tâches, par exemple), Windows empêche Djåse de voir le clavier : cliquez dans une autre fenêtre.")}
  {q("Le texte ne s'écrit pas", "Cliquez d'abord dans la zone de texte : Djåse écrit à l'endroit du curseur. Si une appli refuse le texte, utilisez <b>Copier la dernière dictée</b> (menu de l'icône) puis Ctrl+V.")}
  {q("Rien n'est entendu", "Vérifiez le micro par défaut de Windows : Paramètres, Système, Son, Entrée. Avec une carte son à deux entrées, essayez la <b>Voie du micro</b> 1 ou 2 dans les Réglages.")}
  {q("Le dernier mot est coupé", "Augmentez <b>Écoute après le relâchement</b> dans les Réglages, par exemple à 0,5 seconde. Ou relâchez les touches une fraction de seconde plus tard.")}
  {q("Un mot est toujours mal écrit", "Ajoutez-le dans le <b>Dictionnaire</b> (Mots), ou un remplacement. Plus rapide : dites « Djåse, corrige X par Y et retiens » juste après la dictée.")}
  {q("« Rien effacé (curseur déplacé) »", "Vous avez tapé ou cliqué après la dictée : Djåse ne sait plus où est son texte. Corrigez au clavier, ou dictez à nouveau.")}
  {q("Djåse est lent ou prend trop de mémoire", "Coupez <b>Nettoyage par Qwen</b> dans les Réglages (environ 2,5 Go au lieu de 7), puis relancez Djåse. La pause, dans le menu de l'icône, libère aussi la mémoire de Qwen.")}
  {q("La dictée est annulée toute seule", "Une autre touche a été pressée pendant que vous teniez Ctrl+Win. C'est voulu : les raccourcis de Windows comme Ctrl+Win+flèche ne doivent rien écrire.")}
  {q("Le téléchargement s'est arrêté", "Relancez Djåse : il reprend là où il s'était arrêté, sans tout recommencer. Vérifiez la place libre sur le disque C: (environ 4,5 Go).")}
  {q("« Moteur de dictée en panne »", "Un modèle n'a pas pu se charger. Quittez Djåse et relancez-le. Si ça se répète, copiez le Journal pour le joindre à un signalement de problème.")}
</div>''', "Aide", "Si quelque chose cloche", "Aide", onglet=180)


def memo(d):
    d.page("memo", f'''
<div class="surtitre">À garder sous la main</div>
<h1 class="t">Fiche-mémo</h1>
<div class="g2" style="margin-top:5mm">
  <div class="bloc"><h4>Les gestes</h4><table>
    <tr><td>{k("Ctrl", "Win")} maintenues</td><td>dicter, relâcher pour écrire</td></tr>
    <tr><td>{k("Ctrl", "Win")} appui bref</td><td>mains libres, {k("Ctrl", "Win")} pour finir</td></tr>
    <tr><td>{k("Échap")}</td><td>annuler les mains libres</td></tr>
    <tr><td>{k("Ctrl", "Win")} puis {k("Maj")}</td><td>recoller la dernière dictée</td></tr>
    <tr><td>{k("Ctrl", "Win", "Alt")}</td><td>mode commande</td></tr>
  </table></div>
  <div class="bloc"><h4>Corriger</h4><table>
    <tr><td>{dit("Djåse, efface")}</td><td>le dernier mot</td></tr>
    <tr><td>{dit("Djåse, efface tout")}</td><td>la dernière dictée</td></tr>
    <tr><td>{dit("Djåse, efface X")}</td><td>le mot X</td></tr>
    <tr><td>{dit("Djåse, corrige X par Y")}</td><td>remplacer un mot</td></tr>
    <tr><td>… {dit("et retiens")}</td><td>et l'ajouter au dictionnaire</td></tr>
  </table></div>
  <div class="bloc"><h4>Ponctuation</h4><table>
    <tr><td>{dit("virgule")} {dit("point")}</td><td>, .</td></tr>
    <tr><td>{dit("deux points")} {dit("point-virgule")}</td><td>: ;</td></tr>
    <tr><td>{dit("point d'interrogation")}</td><td>?</td></tr>
    <tr><td>{dit("point d'exclamation")}</td><td>!</td></tr>
    <tr><td>{dit("trois petits points")}</td><td>...</td></tr>
    <tr><td>{dit("ouvrez / fermez les guillemets")}</td><td>« »</td></tr>
    <tr><td>{dit("ouvrez / fermez la parenthèse")}</td><td>( )</td></tr>
  </table></div>
  <div class="bloc"><h4>Mise en page</h4><table>
    <tr><td>{dit("à la ligne")}</td><td>retour à la ligne</td></tr>
    <tr><td>{dit("point à la ligne")}</td><td>point et retour</td></tr>
    <tr><td>{dit("nouveau paragraphe")}</td><td>ligne vide</td></tr>
    <tr><td>{dit("début de liste")}</td><td>liste à tirets</td></tr>
    <tr><td>{dit("élément suivant")}</td><td>tiret suivant</td></tr>
    <tr><td>{dit("fin de liste")}</td><td>fin de la liste</td></tr>
    <tr><td>{dit("entrée")} ou {dit("envoie")}</td><td>touche Entrée</td></tr>
  </table></div>
  <div class="bloc"><h4>Mise en forme</h4><table>
    <tr><td>{dit("en gras … fin du gras")}</td><td>dans la phrase</td></tr>
    <tr><td>{dit("en gras")} / {dit("tout en gras")}</td><td>après coup</td></tr>
    <tr><td>italique, barré</td><td>pareil</td></tr>
    <tr><td>{dit("citation … fin de citation")}</td><td>une citation</td></tr>
  </table></div>
  <div class="bloc"><h4>Où trouver quoi</h4><table>
    <tr><td>Double-clic sur l'icône</td><td>la fenêtre de Djåse</td></tr>
    <tr><td>Clic droit sur l'icône</td><td>pause, copier, quitter</td></tr>
    <tr><td><code>%LOCALAPPDATA%\\Djåse</code></td><td>vos données</td></tr>
    <tr><td>Page Journal</td><td>en cas de souci</td></tr>
  </table></div>
</div>''', "Fiche-mémo", "Fiche-mémo", "Aide", classe="memo")


# ---------------------------------------------------------------------------------------------------------------
def espaces_fr(corps):
    """Espaces insécables du français (avant ? ! : ; », après «), hors balises et blocs de code."""
    def fixer(seg):
        seg = re.sub(r" ([?:!;»])", r"&nbsp;\1", seg)
        return seg.replace("« ", "«&nbsp;")
    return "".join(x if x.startswith("<") else fixer(x) for x in re.split(r"(<[^>]+>)", corps))


def construire():
    d = Doc()
    couverture(d); sommaire(d)
    installer(d); icone(d)
    dicter(d); ponctuation(d); corriger(d)
    fenetre1(d); fenetre2(d); fenetre3(d)
    vie_privee(d); depannage(d); memo(d)

    numero = {pid: i + 1 for i, (pid, _) in enumerate(d.pages)}
    parties, ordre = {}, []
    for partie, titre, pid in d.som:
        if partie not in parties:
            parties[partie] = []
            ordre.append(partie)
        parties[partie].append((titre, pid))
    som = "".join(f"<h3>{html.escape(p)}</h3>" + "".join(
        f'<a href="#{pid}"><span class="d">{html.escape(t)}</span><b>{numero[pid]}</b></a>' for t, pid in parties[p])
        for p in ordre)
    corps = "\n".join(h.replace("{{PAGE}}", str(numero[pid])) for pid, h in d.pages).replace("{{SOMMAIRE}}", som)
    if "{{" in corps:
        sys.exit("ERREUR : marqueur non remplacé : " + re.search(r"\{\{[^}]+\}\}", corps).group(0))
    if re.search("[\u2014\u2013]", corps):
        sys.exit("ERREUR : tiret long dans le texte (règle d'écriture de Manu)")
    corps = espaces_fr(corps)

    controle = """<script>window.addEventListener('load',()=>{document.fonts.ready.then(()=>{const mauvais=[];
document.querySelectorAll('.page .corps').forEach(b=>{const pr=b.getBoundingClientRect();b.querySelectorAll('*').forEach(c=>{const r=c.getBoundingClientRect();
if(r.height>0&&(r.bottom>pr.bottom+1||r.right>pr.right+1)){mauvais.push(b.parentElement.id+' déborde : '+c.tagName+'.'+c.className+' « '+(c.textContent||'').trim().slice(0,40)+' »');}});});
document.querySelectorAll('.encart,.boite,.bloc').forEach(c=>{if(c.scrollHeight>c.clientHeight+2){mauvais.push((c.closest('.page')||{}).id+' coupé dans '+c.className);}});
const o=document.createElement('pre');o.id='debordements';o.style.display='none';o.textContent=JSON.stringify([...new Set(mauvais)]);document.body.appendChild(o);});});</script>"""
    css = (ICI / "style.css").read_text(encoding="utf-8")
    doc = f'''<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Djåse · Mode d'emploi</title>
<style>{css}</style></head><body>{corps}{controle}</body></html>'''

    sortie = ICI / "sortie"
    sortie.mkdir(exist_ok=True)
    f_html, f_pdf = sortie / "Djase_Mode_emploi.html", sortie / "Djase_Mode_emploi.pdf"
    f_html.write_text(doc, encoding="utf-8")
    url = f_html.as_uri()
    dom = subprocess.run([str(CHROME), "--headless=new", "--disable-gpu", "--virtual-time-budget=8000", "--dump-dom", url],
                         capture_output=True, text=True, encoding="utf-8").stdout
    m = re.search(r'<pre id="debordements"[^>]*>(.*?)</pre>', dom, re.S)
    problemes = json.loads(html.unescape(m.group(1))) if m else ["contrôle de débordement non exécuté"]
    subprocess.run([str(CHROME), "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--virtual-time-budget=8000",
                    f"--print-to-pdf={f_pdf}", url], capture_output=True)
    print(f"{len(d.pages)} pages -> {f_pdf}")
    if problemes:
        print("DÉBORDEMENTS :")
        for p in problemes:
            print("  -", p)
        sys.exit(1)


if __name__ == "__main__":
    construire()
