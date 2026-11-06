"""Arbitrage « à l'oreille » des dictées courtes (23/10).

On demande à Canary et à Parakeet, qui ENTENDENT, la probabilité d'une phrase IMPOSÉE pour le son
(« ce son, c'est plutôt « à la ligne » ou « Yeah » ? »), au lieu de comparer leurs transcriptions.
Ne sert qu'à rattraper une commande ratée : jamais à en retirer une. Mêmes modèles que Djåse,
aucune mémoire en plus. Prototype et mesures : essais/oreille.py, notes.md (23/10).
"""
import re

import numpy as np


class Oreille:
    """Canary (AED) : décodeur forcé jeton par jeton, français, sans ponctuation ni majuscules."""

    def __init__(self, canary):
        self.asr = canary.asr  # NemoConformerAED d'onnx_asr
        self.jetons = self.asr._tokens
        self.morceaux = {t: i for t, i in self.jetons.items() if not t.startswith("<")}
        prefixe = self.asr._transcribe_input.copy()
        prefixe[0, 4] = prefixe[0, 5] = self.jetons["<|fr|>"]
        prefixe[0, 6] = self.jetons["<|nopnc|>"]  # sans ponctuation ni majuscules : on compare les mots
        self.prefixe = prefixe
        self.fin = self.asr._eos_token_id
        forme = {x.name: x.shape for x in self.asr._decoder.get_inputs()}["decoder_mems"]
        self.mems_vides = np.empty((forme[0], 1, 0, forme[3]), dtype=np.float32)

    @staticmethod
    def forme(texte):
        """« Point. » → « point » : minuscules, sans ponctuation (le mode nopnc de Canary)."""
        texte = texte.lower().replace("’", "'")
        texte = re.sub(r"[^\w' -]", " ", texte)
        return re.sub(r"\s+", " ", texte).strip()

    def decouper(self, texte):
        """Texte → jetons comme SentencePiece BPE : on fusionne sans cesse la paire voisine dont le morceau
        fusionné a le plus petit numéro (le numéro du vocabulaire = l'ordre des fusions)."""
        parts = list(" " + texte)  # onnx_asr a déjà remplacé le « ▁ » par une espace
        if not texte or any(c not in self.morceaux for c in parts):
            return None
        while len(parts) > 1:
            rang, i = min((self.morceaux.get(parts[i] + parts[i + 1], 1 << 30), i) for i in range(len(parts) - 1))
            if rang == 1 << 30:
                break
            parts[i:i + 2] = [parts[i] + parts[i + 1]]
        return [self.morceaux[p] for p in parts]

    def entendre(self, audio):
        a = audio[None, :].astype(np.float32)
        lon = np.array([a.shape[1]], dtype=np.int64)
        self.enc, self.masque = self.asr._encode(*self.asr._preprocessor(a, lon))
        # Début commun à tous les candidats (langue, format) : calculé une seule fois par son.
        self.logits0, self.mems0 = self.asr._decode(self.prefixe, self.enc, self.masque, self.mems_vides)
        self.cache = {}

    @staticmethod
    def _log_softmax(v):
        m = v.max()
        return v - m - np.log(np.exp(v - m).sum())

    def note(self, texte):
        """(log-probabilité de la phrase entière, fin comprise ; nombre de jetons notés), None si intraduisible."""
        jetons = self.decouper(self.forme(texte))
        if not jetons:
            return None
        total = 0.0
        for k, t in enumerate(jetons + [self.fin]):
            lp, _ = self._etat(tuple(jetons[:k]))
            total += float(lp[t])
        return total, len(jetons) + 1

    def _etat(self, prefixe):
        """Probabilités du jeton suivant après ce préfixe ; débuts communs calculés une seule fois."""
        if prefixe not in self.cache:
            if not prefixe:
                ids, logits, mems = self.prefixe, self.logits0, self.mems0
            else:
                _, (ids, mems) = self._etat(prefixe[:-1])
                ids = np.concatenate([ids, [[prefixe[-1]]]], axis=1)
                logits, mems = self.asr._decode(ids, self.enc, self.masque, mems)
            self.cache[prefixe] = (self._log_softmax(logits[0, -1]), (ids, mems))
        return self.cache[prefixe]


def _lse(a, b):
    if a == -np.inf:
        return b
    if b == -np.inf:
        return a
    m = max(a, b)
    return m + np.log(np.exp(a - m) + np.exp(b - m))


class OreilleParakeet:
    """Parakeet (TDT) : somme sur tous les chemins à travers le son (algorithme « avant »).
    Chaque pas émet un jeton (ou le blanc) et saute 0 à 4 trames."""

    def __init__(self, parakeet):
        self.asr = parakeet.asr  # NemoConformerTdt
        self.morceaux = {t: i for i, t in self.asr._vocab.items() if not t.startswith("<")}
        self.blanc = self.asr._blank_idx
        self.nv = self.asr._vocab_size
        self.decouper = lambda texte: Oreille.decouper(self, texte)

    @staticmethod
    def forme(texte):
        """Forme naturelle de Parakeet : majuscule au début, point final (« Point. », « À la ligne. »)."""
        t = Oreille.forme(texte)
        return (t[:1].upper() + t[1:] + ".") if t else t

    def entendre(self, audio, rate=16000):
        # 0,5 s de silence autour : sans, Parakeet est sourd sur les dictées brèves (comme dans Djåse).
        silence = np.zeros(rate // 2, np.float32)
        a = np.concatenate([silence, audio.astype(np.float32), silence])[None, :]
        lon = np.array([a.shape[1]], dtype=np.int64)
        enc, lens = self.asr._encode(*self.asr._preprocessor(a, lon))
        self.enc = enc[0, : int(lens[0])]  # (T, D)
        self.cache = {}

    def _sorties(self, prefixe):
        """(log-proba jetons, log-proba sauts) sur toutes les trames, après ce préfixe ; mis en cache."""
        if prefixe not in self.cache:
            # Entrée : le dernier jeton du préfixe (blanc au départ) et l'état laissé par le préfixe d'avant.
            if prefixe:
                prec, etat = prefixe[-1], self._sorties(prefixe[:-1])[1]
            else:
                prec, etat = self.blanc, self.asr._create_state()
            sorties, s1, s2 = self.asr._decoder_joint.run(
                ["outputs", "output_states_1", "output_states_2"],
                {"encoder_outputs": self.enc.T[None, :, :], "targets": [[prec]], "target_length": [1],
                 "input_states_1": etat[0], "input_states_2": etat[1]})
            sorties = np.asarray(sorties).reshape(self.enc.shape[0], -1)
            tok, dur = sorties[:, : self.nv], sorties[:, self.nv:]
            tok = tok - tok.max(1, keepdims=True)
            tok = tok - np.log(np.exp(tok).sum(1, keepdims=True))
            dur = dur - dur.max(1, keepdims=True)
            dur = dur - np.log(np.exp(dur).sum(1, keepdims=True))
            self.cache[prefixe] = ((tok, dur), (s1, s2))
        return self.cache[prefixe]

    def note(self, texte):
        jetons = self.decouper(self.forme(texte))
        if not jetons:
            return None
        T, U = self.enc.shape[0], len(jetons)
        lp_tok, lp_dur = [], []
        for u in range(U + 1):  # prédiction après u jetons émis
            (tok, dur), _ = self._sorties(tuple(jetons[:u]))
            lp_tok.append(tok)
            lp_dur.append(dur)
        D = lp_dur[0].shape[1]
        alpha = np.full((T + D, U + 1), -np.inf)
        alpha[0, 0] = 0.0
        fin = -np.inf
        for t in range(T):
            for u in range(U + 1):
                a = alpha[t, u]
                if a == -np.inf:
                    continue
                for d in range(D):
                    if u < U:  # émettre le jeton suivant (saut 0 permis)
                        v = a + lp_tok[u][t, jetons[u]] + lp_dur[u][t, d]
                        if t + d >= T and u + 1 == U:
                            fin = _lse(fin, v)
                        elif t + d < T:
                            alpha[t + d, u + 1] = _lse(alpha[t + d, u + 1], v)
                    if d >= 1:  # blanc : avance d'au moins une trame
                        v = a + lp_tok[u][t, self.blanc] + lp_dur[u][t, d]
                        if t + d >= T:
                            if u == U:
                                fin = _lse(fin, v)
                        else:
                            alpha[t + d, u] = _lse(alpha[t + d, u], v)
        return float(fin), U + 1


class DeuxOreilles:
    def __init__(self, canary, parakeet):
        self.c, self.p = Oreille(canary), OreilleParakeet(parakeet)
        self._son_c = self._son_p = None  # son déjà encodé : départage puis commandes sans réencoder

    def _entendre_c(self, audio):
        if self._son_c is not audio:
            self.c.entendre(audio)
            self._son_c = audio

    def _entendre_p(self, audio):
        if self._son_p is not audio:
            self.p.entendre(audio)
            self._son_p = audio

    def departager(self, audio, candidats):
        """Parakeet et Canary ne sont pas d'accord : note de chaque transcription par les deux oreilles
        (somme des log-probabilités). « Ouais » : « Well, I think it's a very good thing » -87, « ouais » -5
        (23/10). Renvoie {texte: note} pour les candidats notables."""
        self._entendre_c(audio)
        self._entendre_p(audio)
        notes = {}
        for x in candidats:
            a, b = self.c.note(x), self.p.note(x)
            if a is not None and b is not None:
                notes[x] = a[0] + b[0]
        return notes

    def ecouter(self, audio, commandes, libres, seuil, marge):
        """commandes : {phrase dite : texte rendu}. libres : ce qui a pu être dit d'autre.
        Renvoie (texte rendu ou None, détail pour le journal).
        La commande doit battre toutes les phrases libres (somme des deux oreilles) ET coller au son
        dans CHAQUE oreille (moyenne par jeton ≥ seuil) : sinon « point », 2 jetons, gagne sur du flou."""
        # 1re étape, Canary seul : si aucune commande ne colle au son, on s'arrête là (le cas courant,
        # une vraie phrase brève) sans encoder pour Parakeet ni noter les phrases libres.
        self._entendre_c(audio)
        notes_c = {x: n for x in commandes if (n := self.c.note(x))}
        if not notes_c:
            return None, "aucune commande notable"
        retenues = [x for x, (tot, nb) in notes_c.items() if tot / nb >= seuil]
        if not retenues:
            x = max(notes_c, key=lambda x: notes_c[x][0] / notes_c[x][1])
            return None, f"« {x} » Canary {notes_c[x][0] / notes_c[x][1]:.2f}/jeton"
        self._entendre_p(audio)

        def noter(x):
            a, b = notes_c.get(x) or self.c.note(x), self.p.note(x)
            return None if a is None or b is None else (a[0] + b[0], a[0] / a[1], b[0] / b[1])

        notes = {x: n for x in retenues if (n := noter(x))}
        if not notes:
            return None, "aucune commande notable"
        meilleure = max(notes, key=lambda x: notes[x][0])
        total, moy_c, moy_p = notes[meilleure]
        formes = {Oreille.forme(x) for x in commandes}
        autres = {x: n[0] for x in set(libres) if x and Oreille.forme(x) not in formes and (n := noter(x))}
        rivale = max(autres, key=autres.get, default=None)
        ecart = total - autres[rivale] if rivale else float("inf")
        detail = (f"« {meilleure} » Canary {moy_c:.2f}/jeton, Parakeet {moy_p:.2f}/jeton"
                  + (f", {ecart:+.1f} contre « {rivale} »" if rivale else ""))
        if min(moy_c, moy_p) >= seuil and ecart > marge:
            return commandes[meilleure], detail
        return None, detail
