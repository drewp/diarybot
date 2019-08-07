from rdflib import Namespace, RDFS, RDF
SCHEMA = Namespace ("http://schema.org/")
DB = Namespace("http://bigasterisk.com/ns/diaryBot#")

def choiceTree(g, choiceNode, kvToHere, seenKvs):
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
    out['choices'].sort()
    if not out['choices']:
        del out['choices']
        i = frozenset(kv2.items())
        if i in seenKvs:
            raise ValueError('multiple leaf nodes have kv %r' % kv2)
        seenKvs.add(i)
    return out

def structuredInputElementConfig(g, bot):
    config = {'choices': []}
    seenKvs = set()
    for rootChoice in g.objects(bot, DB['structuredEntry']):
        config['choices'].append(choiceTree(g, rootChoice, {}, seenKvs))

    return config

def englishInput(kvs):
    msg = []
    # should be one loop that converts triples and sets
    # ordering, then we can see what triples are left over
    if (DB['record'], SCHEMA['TherapeuticProcedure']) in kvs:
        msg.append('took')

    for k, v in kvs:
        if k == SCHEMA['doseValue']:
            msg.append(v)
    for k, v, in kvs:
        if k == SCHEMA['doseUnit']:
            msg.append(v)
    for k, v in kvs:
        if k == SCHEMA['drug']:
            msg.append('of %s' % v)
    return msg

# maybe this should be json-ld
def mongoListFromKvs(kvs):
    return sorted(kvs.items())


from rdflib.plugins.parsers.ntriples import ParseError, unquote, URI, r_uriref, uriquote, r_literal, Literal

class TermParser(object):
    def __init__(self, n3term):
        self.line = n3term

    def peek(self, token):
        return self.line.startswith(token)

    def eat(self, pattern):
        m = pattern.match(self.line)
        if not m:  # @@ Why can't we get the original pattern?
            # print(dir(pattern))
            # print repr(self.line), type(self.line)
            raise ParseError("Failed to eat %s at %s" % (pattern.pattern, self.line))
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

def parseN3Term(n):
    p = TermParser(n)
    return p.uriref() or p.literal()


def kvFromMongoList(ml):
    return [(parseN3Term(k), parseN3Term(v)) for k, v in ml]
