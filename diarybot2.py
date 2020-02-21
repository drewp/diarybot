import importlib
import json
import re
import sys
import docopt
import os

# sets twisted's global reactor
from chatinterface import ChatInterface, NoChat

from bson import ObjectId
from dateutil.parser import parse
from rdflib import Namespace, Graph, URIRef, RDF
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks
import cyclone.template
import cyclone.web
from twisted.internet.defer import ensureDeferred

from bot import makeBots, Bot
from history_queries import All, Last150, OffsetTime, Latest
from loginbar import getLoginBar
from request_handler_fix import FixRequestHandler
from standardservice.logsetup import log, verboseLogging
from structuredinput import kvFromMongoList, englishInput

BOT = Namespace('http://bigasterisk.com/bot/')
DB = Namespace('http://bigasterisk.com/ns/diaryBot#')
FOAF = Namespace('http://xmlns.com/foaf/0.1/')

loader = cyclone.template.Loader('.')

_foafName = {}  # uri : name


def visibleBots(bots, agent: URIRef):
    visible = set()
    for bot in bots.values():
        try:
            bot.assertUserCanRead(agent)
        except ValueError:
            continue
        visible.add(bot)
    return sorted(visible, key=lambda b: (len(b.owners), b.name))


def getDoc(bot, agent, docId):
    bot.assertUserCanRead(agent)
    return bot.mongo.find_one({'_id': ObjectId(docId)})  # including deleted


def prettyDate(iso, birthdate=None):
    dt = parse(iso)
    msg = dt.strftime('%Y-%m-%d %a %H:%M')
    if birthdate:
        age = dt - birthdate
        ageMsg = '%.1f years' % (age.days / 365)
        if age.days < 2 * 365:
            ageMsg = ageMsg + ', or %.1f months,' % (age.days / 30.4)
        msg = msg + ' (%s old)' % ageMsg
    return msg


class DiaryBotRequest(FixRequestHandler):
    def getAgent(self):
        if 'DIARYBOT_AGENT' in os.environ:
            return URIRef(os.environ['DIARYBOT_AGENT'])
        try:
            return URIRef(self.request.headers['X-Foaf-Agent'])
        except KeyError:
            return None

    def redirectToHistoryPage(self, bot):
        self.redirect('https://bigasterisk.com/diary/%s/history/recent' %
                      bot.name)


class index(DiaryBotRequest):
    def get(self):
        self.set_header('Content-type', 'text/html')

        agent = self.getAgent()

        loader.reset()
        self.write(
            loader.load('index.html').generate(
                bots=visibleBots(self.settings.bots, agent),
                loginBar=getLoginBar(self.request),
                json=json,
            ))


def makeHttps(uri):
    return uri.replace('http://bigasterisk.com/', 'https://bigasterisk.com/')


class message(DiaryBotRequest):
    @inlineCallbacks
    def post(self, botName):
        agent = self.getAgent()
        bot = self.settings.bots[botName]
        uri = yield bot.save(agent, msg=self.get_argument('msg'))
        self.redirect(makeHttps(uri))


class StructuredInput(DiaryBotRequest):
    @inlineCallbacks
    def post(self, botName):
        agent = self.getAgent()
        bot = self.settings.bots[botName]
        kv = json.loads(self.get_argument('kv'))

        uri = yield bot.save(agent, kv=kv)
        self.redirect(makeHttps(uri))


class EditForm(DiaryBotRequest):
    def get(self, botName, docId):
        bot = self.settings.bots[botName]
        agent = self.getAgent()
        row = getDoc(bot, agent, docId)

        self.set_header('Content-type', 'text/html')
        self.write(
            loader.load('editform.html').generate(
                uri=bot.uriForDoc(row),
                botName=bot.name,
                row=row,
                created=row['dc:created'],
                creator=row['dc:creator'],
                content=row.get('sioc:content', ''),
                loginBar=getLoginBar(self.request),
            ))

    def post(self, botName, docId):
        bot = self.settings.bots[botName]

        if self.get_argument('method', default=None) == 'DELETE':
            self.delete(botName, docId)
        else:
            if self.get_argument('newTime'):
                dt = parse(self.get_argument('newTime'))
                bot.updateTime(self.getAgent(), docId, dt)

            self.redirectToHistoryPage(bot)

    def delete(self, botName, docId):
        bot = self.settings.bots[botName]
        bot.delete(self.getAgent(), docId)
        self.redirectToHistoryPage(bot)


class history(DiaryBotRequest):
    def writeRdf(self, rows):
        # this could have been RDFA in the normal page result
        for r in rows:
            del r['_id']
            del r['created']
        self.set_header('Content-type', 'application/json')
        self.write(json.dumps(rows))

    def get(self, botName, selection=None):
        agent = self.getAgent()
        bot = self.settings.bots[botName]

        bot.assertUserCanRead(agent)

        queries = [
            OffsetTime(365, 'a year ago', '/yearAgo'),
            All(),
            Last150(),
            Latest()
        ]
        queries.extend(bot.historyQueries)

        for q in queries:
            if q.suffix == selection:
                rows = list(q.run(bot.mongo))
                query = q
                queries.remove(q)
                break
        else:
            raise ValueError('unknown query %s' % selection)

        if self.get_argument('rdf', ''):
            self.writeRdf(rows)
            return

        entries = []
        for row in rows:
            if 'structuredInput' in row:
                kvs = kvFromMongoList(row['structuredInput'])
                words = englishInput(self.settings.configGraph, kvs)
                if words:
                    msg = '[si] %s' % words
                else:
                    msg = str(kvs)
            else:
                msg = row['sioc:content']
            entries.append((bot.uriForDoc(row), row['dc:created'],
                            row['dc:creator'], msg, row))

        def prettyName(uri):
            return _foafName.get(URIRef(uri), uri)

        def prettyMatch(content, pat):
            try:
                return '1' if re.search(pat, content) else ''
            except Exception:
                return ''

        d = dict(bot=bot,
                 agent=agent,
                 entries=entries,
                 otherQueries=queries,
                 query=query,
                 prettyName=prettyName,
                 prettyDate=lambda iso: prettyDate(iso, bot.birthdate),
                 prettyMatch=prettyMatch,
                 unixDate=lambda iso: parse(iso).strftime('%s'),
                 loginBar=getLoginBar(self.request))

        if self.get_argument('rcs', ''):
            self.set_header('Content-type', 'text/html')
            import rcsreport
            importlib.reload(rcsreport)
            rcsreport.output(entries, self.write)
            return

        self.set_header('Content-type', 'text/html')

        if self.get_argument('entriesOnly', ''):
            self.write(loader.load('diaryviewentries.html').generate(**d))
            return
        self.write(loader.load('history.html').generate(**d))


class IncomingChatHandler:
    def lateInit(self, bots, chat):
        self.bots = bots
        self.chat = chat

    @inlineCallbacks
    def onMsg(self, toBot: Bot, fromUser: URIRef, msg: str):
        log.info(r'onMsg {vars()}')

        try:
            if msg == 'chattest':
                yield self.chat.sendMsg(toBot, fromUser,
                                        'not saving %s test' % toBot.name)
                return
            uri = yield toBot.save(userUri=fromUser, msg=msg)
        except Exception as e:
            yield self.chat.sendMsg(toBot, fromUser, r'failed to save: {e:r}')
            raise
        yield self.chat.sendMsg(toBot, fromUser, 'saved %s' % uri)


def main():
    arg = docopt.docopt("""
    Usage: diarybot2.py [options]

    -v                    Verbose
    --no-chat             Don't talk to slack at all
    --drew-bot            Limit to just drewp healthbot
    """)
    verboseLogging(arg['-v'])

    configGraph = Graph()
    configGraph.parse('bots-secret.n3', format='n3')
    if arg['--drew-bot']:
        os.environ['DIARYBOT_AGENT'] = 'http://bigasterisk.com/foaf.rdf#drewp'
        for botSubj in configGraph.subjects(RDF.type, DB['DiaryBot']):
            if botSubj != BOT['healthBot']:
                print(f'remove {botSubj}')
                configGraph.remove((botSubj, RDF.type, DB['DiaryBot']))

    ich = None
    if not arg['--no-chat']:
        ich = IncomingChatHandler()
        chat = ChatInterface(ich.onMsg)
    else:
        chat = NoChat()
    bots = makeBots(chat, configGraph)
    if ich:
        ich.lateInit(bots, chat)

    for s, p, o in configGraph.triples((None, FOAF['name'], None)):
        _foafName[s] = o

    reactor.listenTCP(
        9048,
        cyclone.web.Application([
            (r'/', index),
            (r'/dist/(bundle\.js)', cyclone.web.StaticFileHandler, {
                'path': 'dist'
            }),
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
