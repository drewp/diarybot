"""
rewrite of diarybot.py. Use cyclone instead of twisted;
ejabberd/mod_rest/mod_motion instead of any XMPP in-process; mongodb
store instead of rdf in files.
"""

# special reactor
from chatinterface import ChatInterface

from bson import ObjectId
from dateutil import tz
from dateutil.parser import parse
from pprint import pprint
from pymongo import MongoClient
from rdflib import Namespace, RDFS, Graph, URIRef
from structuredinput import structuredInputElementConfig, kvFromMongoList, englishInput, mongoListFromKvs
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, ensureDeferred, Deferred
from typing import Dict, List
import cyclone.web, cyclone.template
import datetime
import logging
import requests
import time, json, re, sys

from datestr import datestr
from loginbar import getLoginBar
from request_handler_fix import FixRequestHandler
from bot import makeBots, Bot

BOT = Namespace('http://bigasterisk.com/bot/')
XS = Namespace("http://www.w3.org/2001/XMLSchema#")
SIOC = Namespace("http://rdfs.org/sioc/ns#")
DC = Namespace("http://purl.org/dc/terms/")
DB = Namespace("http://bigasterisk.com/ns/diaryBot#")
FOAF = Namespace("http://xmlns.com/foaf/0.1/")
BIO = Namespace ("http://vocab.org/bio/0.1/")
SCHEMA = Namespace ("http://schema.org/")
INIT_NS = dict(sioc=SIOC, dc=DC, db=DB, foaf=FOAF, rdfs=RDFS.uri, bio=BIO)

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger()

loader = cyclone.template.Loader('.')

_foafName = {}  # uri : name


def getAgent(request):
    try:
        return URIRef(request.headers['X-Foaf-Agent'])
    except KeyError:
        return None

def visibleBots(bots, agent):
    visible = set()
    for bot in self.settings.bots.values():
        if bot.viewableBy(agent):
            visible.add(bot)
    return sorted(visible, key=lambda b: (len(b.owners), b.name))

class index(FixRequestHandler):
    def get(self):
        self.set_header('Content-type', 'text/html')

        agent = getAgent(self.request)

        visible = visibleBots(self.settings.bots, agent)

        loader.reset()
        self.write(loader.load('index.html').generate(
            bots=visible,
            loginBar=getLoginBar(self.request),
            json=json,
            ))

class message(FixRequestHandler):
    def post(self, botName):
        agent = getAgent(self.request)
        bot = self.settings.bots[botName]
        msg = self.get_argument('msg')
        print('msg %r' % msg)

        uri = bot.save(agent, msg=msg)
        self.write('saved') # self.redirect(uri)

class StructuredInput(FixRequestHandler):
    def post(self, botName):
        agent = getAgent(self.request)
        bot = self.settings.bots[botName]
        kv = json.loads(self.get_argument('kv'))
        print('kv %r' % kv)

        uri = bot.save(agent, kv=kv)
        self.write('saved') #self.redirect(uri)

class Query(object):
    suffix = None
    def makeLink(self, currentQuery):
        levels = (currentQuery.suffix or "").count('/')
        return "./" + "../" * levels + "history"+(self.suffix or "")

    def makeHomeLink(self):
        levels = (self.suffix or "").count('/')
        return "../" * (levels+1)

class OffsetTime(Query):
    def __init__(self, daysAgo, labelAgo, urlSuffix):
        self.name = self.desc = labelAgo
        self.daysAgo = daysAgo
        self.suffix = urlSuffix

    def run(self, mongo):
        end = datetime.datetime.now() - datetime.timedelta(days=self.daysAgo)
        rows = mongo.find({'deleted': {'$exists': False },
                           "created" : {"$lt" : end}}).sort('created', -1).limit(10)
        rows = reversed(list(rows))
        return rows

class Last150(Query):
    name = 'last 150 entries'
    desc = name
    suffix = '/recent'
    def run(self, mongo):
        return mongo.find({'deleted': {'$exists': False }}, limit=150, sort=[('created', -1)])

class Latest(Query):
    name = 'latest entry'
    desc = name
    suffix = '/latest'
    def run(self, mongo):
        return mongo.find({'deleted': {'$exists': False }}, limit=1, sort=[('created', -1)])

class All(Query):
    name = 'all'
    desc = 'history'
    suffix = None
    def run(self, mongo):
        return mongo.find({'deleted': {'$exists': False }}).sort('created', -1)

def uriForDoc(botName, d):
    return URIRef('http://bigasterisk.com/diary/%s/%s' % (botName, d['_id']))

def getDoc(bot, agent):
    if agent not in bot.owners:
        raise ValueError('not owner')
    return bot.mongo.find_one({'_id': ObjectId(docId)}) # including deleted

class EditForm(FixRequestHandler):
    def get(self, botName, docId):
        self.set_header('Content-type', 'text/html')

        bot = self.settings.bots[botName]
        agent = getAgent(self.request)
        row = getDoc(bot, agent)
        self.write(loader.load('editform.html').generate(
            uri=uriForDoc(botName, row),
            row=row,
            created=row['dc:created'],
            creator=row['dc:creator'],
            content=row.get('sioc:content', ''),
            loginBar=getLoginBar(self.request),
        ))

    def post(self, botName, docId):
        if self.get_argument('method', default=None) == 'DELETE':
            self.delete(botName, docId)
            return

        bot = self.settings.bots[botName]
        agent = getAgent(self.request)
        if agent not in bot.owners:
            raise ValueError('not owner')

        if self.get_argument('newTime'):
            dt = parse(self.get_argument('newTime'))
            bot.updateTime(agent, docId, dt)


    def delete(self, botName, docId):
        bot = self.settings.bots[botName]
        agent = getAgent(self.request)
        if agent not in bot.owners:
            raise ValueError('not owner')

        bot.delete(agent, docId)
        self.redirect('https://bigasterisk.com/diary/%s/history/recent' % botName)

class history(FixRequestHandler):
    def get(self, botName, selection=None):
        agent = getAgent(self.request)
        bot = self.settings.bots[botName]
        configGraph = self.settings.configGraph

        if not bot.viewableBy(agent):
            raise ValueError("cannot view %s" % botName)

        queries = [OffsetTime(365, 'a year ago', '/yearAgo'), All(), Last150(), Latest()]
        queries.extend(bot.historyQueries)

        for q in queries:
            if q.suffix == selection:
                rows = list(q.run(bot.mongo))
                query = q
                queries.remove(q)
                break
        else:
            raise ValueError("unknown query %s" % selection)

        if self.get_argument('rdf', ''):
            # this could have been RDFA in the normal page result
            import json
            for r in rows:
                del r['_id']
                del r['created']
            self.set_header('Content-type', 'application/json')
            self.write(json.dumps(rows))
            return

        entries = []
        for row in rows:
            if 'structuredInput' in row:
                kvs = kvFromMongoList(row['structuredInput'])
                words = englishInput(configGraph, dict(kvs))
                if words:
                    msg = "[si] %s" % words
                else:
                    msg = str(kvs)
            else:
                msg = row['sioc:content']
            entries.append((uriForDoc(botName, row), row['dc:created'], row['dc:creator'], msg, row))

        def prettyDate(iso):
            dt = parse(iso)
            msg = dt.strftime("%Y-%m-%d %a %H:%M")
            if bot.birthdate:
                age = dt - bot.birthdate
                ageMsg = "%.1f years" % (age.days / 365)
                if age.days < 2*365:
                    ageMsg = ageMsg + ", or %.1f months," % (age.days / 30.4)
                msg = msg + " (%s old)" % ageMsg
            return msg

        def prettyName(uri):
            return _foafName.get(URIRef(uri), uri)

        def prettyMatch(content, pat):
            try:
                return '1' if re.search(pat, content) else ''
            except Exception: return ''

        d = dict(
            bot=bot,
            agent=agent,
            entries=entries,
            otherQueries=queries,
            query=query,
            prettyName=prettyName,
            prettyDate=prettyDate,
            prettyMatch=prettyMatch,
            unixDate=lambda iso: parse(iso).strftime("%s"),
            loginBar=getLoginBar(self.request))

        if self.get_argument('rcs', ''):
            self.set_header('Content-type', 'text/html')
            import rcsreport
            reload(rcsreport)
            rcsreport.output(entries, self.write)
            return

        self.set_header('Content-type', 'text/html')

        if self.get_argument('entriesOnly',''):
             self.write(loader.load('diaryviewentries.html').generate(**d))
             return
        self.write(loader.load('diaryview.html').generate(**d))

def main():
    from twisted.python import log as twlog
    twlog.startLogging(sys.stdout)

    @inlineCallbacks
    def onMsg(toBot, fromUser, msg):
        log.info(r'onMsg {vars()}')

        for botName, b in bots.items():
            if b.uri == toBot:
                break
        else:
            raise KeyError(f'chat from unknown bot {botName}')

        try:
            if msg == 'chattest':
                yield chat.sendMsg(toBot, fromUser, 'not saving %s test' % b.name)
                return
            uri = b.save(userUri=fromUser, msg=msg)
        except Exception as e:
            yield chat.sendMsg(toBot, fromUser, r'failed to save: {e:r}')
            raise
        yield chat.sendMsg(toBot, fromUser, 'saved %s' % uri)

    configGraph = Graph()
    configGraph.parse("bots-secret.n3", format='n3')
    chat = ChatInterface(onMsg)
    bots = makeBots(chat, configGraph)

    for s,p,o in configGraph.triples((None, FOAF['name'], None)):
        _foafName[s] = o

    reactor.listenTCP(
        9048,
        cyclone.web.Application([
            (r'/', index),
            (r'/dist/(bundle\.js)', cyclone.web.StaticFileHandler, {'path':'dist'}),
            (r'/([^/]+)/message', message),
            (r'/([^/]+)/structuredInput', StructuredInput),
            (r'/([^/]+)/history(/[^/]+)?', history),
            (r'/([^/]+)/([^/]+)', EditForm),
        ],
                                bots=bots,
                                configGraph=configGraph,
                                debug=True),
        interface='::')
    reactor.run()


if __name__ == '__main__':
    main()
