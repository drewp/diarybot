from rdflib.plugins.parsers.ntriples import ParseError, unquote, URI, r_uriref, uriquote, r_literal, Literal
from rdflib import Namespace, RDFS, RDF, URIRef, Graph
from rdflib.term import Node
from typing import Dict, Set

SCHEMA = Namespace('http://schema.org/')
DB = Namespace('http://bigasterisk.com/ns/diaryBot#')


def choiceTree(g: Graph, choiceNode: Node, kvToHere: Dict, seenKvs: Set):
    out = {'label': g.label(choiceNode), 'choices': []}
    kvs = []
    for s, p, o in g.triples((choiceNode, None, None)):
        if p not in [RDF['type'], RDFS['label'], DB['choice']]:
            kvs.append((p.n3(), o.n3()))
    kv2 = kvToHere.copy()
    if kvs:
        out['kv'] = dict(kvs)
        kv2.update(out['kv'])

    for child in g.objects(choiceNode, DB['choice']):
        out['choices'].append(choiceTree(g, child, kv2, seenKvs))
    # out['choices'].sort()
    if not out['choices']:
        del out['choices']
        i = frozenset(list(kv2.items()))
        if i in seenKvs:
            raise ValueError('multiple leaf nodes have kv %r' % kv2)
        seenKvs.add(i)
    return out


def structuredInputElementConfig(g: Graph, bot: URIRef) -> Dict:
    config = {'choices': []}
    seenKvs = set()
    for rootChoice in sorted(g.objects(bot, DB['structuredEntry'])):
        config['choices'].append(choiceTree(g, rootChoice, {}, seenKvs))

    return config


def englishInput(g: Graph, kvs: Dict[Node, Node]) -> str:
    convs = []
    for conv in g.subjects(RDF.type, DB['NaturalInputConversion']):
        convs.append({
            'reportPred':
            g.value(conv, DB['reportPred']),
            'reportObj':
            g.value(conv, DB['reportObj'], default=None),
            'label':
            g.value(conv, RDFS.label, default=None),
            'prepend':
            g.value(conv, DB['prepend'], default=None),
            'reportOrder':
            g.value(conv, DB['reportOrder'], default=Literal(0)),
        })
    convs.sort(key=lambda c: c['reportOrder'].toPython())

    words = []
    for conv in convs:
        for k, v in kvs.items():
            if k == conv['reportPred']:
                if not conv['reportObj'] or v == conv['reportObj']:
                    if conv['prepend']:
                        words.append(conv['prepend'])
                    if conv['label']:
                        words.append(conv['label'])
                    else:
                        if isinstance(v, URIRef):
                            lab = g.label(v)
                            if lab:
                                words.append(lab)
                            else:
                                words.append(str(v))
                        else:
                            words.append(v)
    # also note here what kv weren't used

    return ' '.join(words)


# maybe this should be json-ld
def mongoListFromKvs(kvs):
    return sorted(kvs.items())


class TermParser(object):
    def __init__(self, n3term: str):
        self.line = n3term

    def peek(self, token: str):
        return self.line.startswith(token)

    def eat(self, pattern):
        m = pattern.match(self.line)
        if not m:  # @@ Why can't we get the original pattern?
            # print(dir(pattern))
            # print repr(self.line), type(self.line)
            raise ParseError('Failed to eat %s at %s' %
                             (pattern.pattern, self.line))
        self.line = self.line[m.end():]
        return m

    def uriref(self):
        if self.peek('<'):
            uri = self.eat(r_uriref).group(1)
            uri = unquote(uri)
            uri = uriquote(uri)
            return URI(uri)
        return False

    def literal(self):
        if self.peek('"'):
            lit, lang, dtype = self.eat(r_literal).groups()
            if lang:
                lang = lang
            else:
                lang = None
            if dtype:
                dtype = unquote(dtype)
                dtype = uriquote(dtype)
                dtype = URI(dtype)
            else:
                dtype = None
            if lang and dtype:
                raise ParseError("Can't have both a language and a datatype")
            lit = unquote(lit)
            return Literal(lit, lang, dtype)
        return False


def parseN3Term(n: str) -> Node:
    p = TermParser(n)
    return p.uriref() or p.literal()


def kvFromMongoList(ml):
    return [(parseN3Term(k), parseN3Term(v)) for k, v in ml]
