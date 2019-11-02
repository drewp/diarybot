from rdflib.plugins.parsers.ntriples import ParseError, unquote, URI, r_uriref, uriquote, r_literal, Literal
from rdflib.term import Node


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
