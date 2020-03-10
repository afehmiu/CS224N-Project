import numpy as np
import os.path
import pickle
import sys
from nltk import word_tokenize
import string
from nltk.stem import SnowballStemmer


############ THIS IS THE NLTK-BASED PARAPHRASE GENERATOR !!!! ##########
def add_to_dict_of_set(key, value, dict_set):
    if key in dict_set:
        dict_set[key].add(value)
    else:
        dict_set[key] = {value}


def clean_paraphrase(paraphrase_dict):
    stemmer = SnowballStemmer("english")
    paraphrase_dict_clean = dict()
    print("Size: %d" % len(paraphrase_dict))

    for phrase, paraphrases in paraphrase_dict.items():
        new_paraphrases = set()
        for paraphrase in paraphrases:
            if stemmer.stem(phrase) != stemmer.stem(paraphrase):
                new_paraphrases.add(paraphrase)
        if len(new_paraphrases):
            paraphrase_dict_clean[phrase] = new_paraphrases
    print("Size: %d" % len(paraphrase_dict_clean))
    return paraphrase_dict_clean


############ THIS IS THE PPDB-BASED PARAPHRASE GENERATOR !!!! ###########
def string_clean(words):
    table = str.maketrans('', '', string.punctuation)
    stripped = [w.translate(table) for w in words]
    return stripped


class PPDB(object):
    def __init__(self, sentence, ppdb, isTxt = True):
        self.sentence = sentence
        self.ppdb = ppdb
        self.ppdb_paraphrases = ppdb_paraphrases = {}

        self.tokens = []
        if isTxt:
            with open(sentence, "r") as f_sentence:
                lines = f_sentence.readlines()
                for line in lines:
                    tokens = word_tokenize(line)
                #tokens = [string_clean(x.split()) for x in words]
                self.tokens = tokens
        else:
            tokens = word_tokenize(sentence)
            self.tokens = tokens

    def read_lexicon(self):
        print
        "Abstract Function"

    def search_baseword(self, inputword):
        return inputword in self.ppdb_paraphrases.keys()

    def add_paraphrases(self, baseword, ppword, score):
        if baseword == ppword:
            return
        if self.search_baseword(baseword):
            if ppword in self.ppdb_paraphrases[baseword]:
                return
            self.ppdb_paraphrases[baseword].append((ppword,score))
            # self.ppdb_paraphrases[baseword] += [ppword, score]
        else:
            self.ppdb_paraphrases[baseword] = [(ppword, score)]

    def save_ppdb(self, filename, sortDict = True):
        if sortDict:
            self.sort_paraphrases()
        #TODO: fix this part if we need to save the ppdb results
        with open(filename, "w") as f_save:
            n = 0
            for token in self.tokens:
                if token == "UNK":
                    write_line = "</s> </s>\n"
                elif token in self.ppdb_paraphrases.keys():
                    write_line = str(token) + " "
                    for ppword in self.ppdb_paraphrases[token]:
                        write_line += ppword + " "
                        write_line += "</s>\n"
                else:
                    write_line = str(token) + " </s>\n"
                    f_save.write(write_line)
                    n += 1
                    if n % 1000 == 0:
                        f_save.flush()

    def sort_paraphrases(self):
        dict = self.ppdb_paraphrases
        self.ppdb_paraphrases = {k: v for k, v in sorted(dict.items(), key=lambda item: item[1])}


class PPDBE(PPDB):
    def __init__(self, sentence, ppdb, isTxt = True):
        super(PPDBE, self).__init__(sentence, ppdb, isTxt= isTxt)

    def read_lexicon(self):
        with open(self.ppdb, "r") as ppdb_f:
            lines = ppdb_f.readlines()
            n = 0
            for line in lines:
                if line.split("|||")[1].strip() in self.tokens:
                    baseword = line.split("|||")[1].strip()
                    ppword = line.split("|||")[2].strip()
                    score = line.split("|||")[3].split(" ")[1].split("=")[1]

                    if (line.split("|||")[-1].strip() == "Equivalence") \
                            or (line.split("|||")[-1].strip() == "ForwardEntailment") \
                            or (line.split("|||")[-1].strip() == "ReverseEntailment"):
                        self.add_paraphrases(baseword, ppword, score)

                elif line.split("|||")[2].strip() in self.tokens:
                    baseword = line.split("|||")[1].strip()
                    ppword = line.split("|||")[2].strip()
                    score = line.split("|||")[3].split(" ")[1].split("=")[1]

                    if (line.split("|||")[-1].strip() == "Equivalence") \
                            or (line.split("|||")[-1].strip() == "ForwardEntailment") \
                            or (line.split("|||")[-1].strip() == "ReverseEntailment"):
                        self.add_paraphrases(ppword, baseword, score)

    def save_ppdb(self):
        "write ppdb2e"
        super(PPDBE, self).save_ppdb("ppdb_E.txt")

    def get_n_paraphrases(self, num_paraphrases):
        self.sort_paraphrases()
        n_paraphrases = {}
        for token in self.tokens:
            if token in self.ppdb_paraphrases.keys():
                for i in range(num_paraphrases):
                    if token in n_paraphrases.keys():
                        try:
                            n_paraphrases[token].add(self.ppdb_paraphrases[token][i][0])
                        except:
                            print("less than n paraphrases!!!")
                    else:
                        try:
                            n_paraphrases[token] = {self.ppdb_paraphrases[token][i][0]}
                        except:
                            print("less than n paraphrases!!! ")

        return n_paraphrases

    def gen_paraphrase_questions(self, num_replacement, num_paraphrases):

        n_paraphrases = self.get_n_paraphrases(num_paraphrases)
        paraphrases = []

        cnt = 0
        i = 0
        while cnt <= num_replacement:
            for token, idx in enumerate(self.tokens):
                p = 0
                if token in n_paraphrases.keys():
                    while p < len(n_paraphrases[token]):
                        paraphrases[i] = self.tokens
                        paraphrases[i][idx] = n_paraphrases[token][p]
                        p += 1
                        i += 1
                    cnt += 1

        return paraphrases


if __name__ == "__main__":
    if len(sys.argv) > 1:
        vocab = sys.argv[1]
        lexicon = sys.argv[2]
    else:
        vocab = "vocab2.txt"
        lexicon = "ppdb-2.0-tldr"
    ppdb = PPDBE(vocab, lexicon, isTxt=True)
    ppdb.read_lexicon()
    # ppdb.save_ppdb()
    print(ppdb.get_n_paraphrases(3))
    # print(ppdb.ppdb_paraphrases)
