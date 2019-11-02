from rdflib import Literal, Namespace, RDFS, RDF, URIRef, Graph
from rdflib.term import Node
from typing import Dict, Set, List, Tuple, Any
from rdflib_term_parser import parseN3Term

SCHEMA = Namespace('http://schema.org/')
DB = Namespace('http://bigasterisk.com/ns/diaryBot#')


def choiceTree(g: Graph, choiceNode: Node, kvToHere: Dict, seenKvs: Set) -> Dict[str, Any]:
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
def mongoListFromKv(kv: Dict[Node, Node]) -> List[Tuple[Node, Node]]:
    return sorted(kv.items())


def kvFromMongoList(ml: List[Tuple[str, str]]) -> Dict[Node, Node]:
    return dict((parseN3Term(k), parseN3Term(v)) for k, v in ml)
