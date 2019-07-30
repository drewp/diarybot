#!bin/python
"""
rewrite of diarybot.py. Use web.py instead of twisted;
ejabberd/mod_rest/mod_motion instead of any XMPP in-process; mongodb
store instead of rdf in files.
"""
from __future__ import division

CHAT_SUPPORT = False # bitrotted

import time, sys, json, re
import cyclone.web, cyclone.template
from twisted.internet import reactor
from twisted.words.protocols.jabber.jid import JID
from rdflib import Namespace, RDFS, Graph, URIRef
from dateutil import tz
from dateutil.parser import parse
from web.utils import datestr
import datetime
from pymongo import MongoClient
import requests, logging

if CHAT_SUPPORT:
    from twisted.words.xish import domish
    from wokkel.client import XMPPClient
    from wokkel.xmppim import MessageProtocol, AvailablePresence, PresenceClientProtocol

XS = Namespace("http://www.w3.org/2001/XMLSchema#")
SIOC = Namespace("http://rdfs.org/sioc/ns#")
DC = Namespace("http://purl.org/dc/terms/")
DB = Namespace("http://bigasterisk.com/ns/diaryBot#")
FOAF = Namespace("http://xmlns.com/foaf/0.1/")
BIO = Namespace ("http://vocab.org/bio/0.1/")
INIT_NS = dict(sioc=SIOC, dc=DC, db=DB, foaf=FOAF, rdfs=RDFS.uri, bio=BIO)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger()
loader = cyclone.template.Loader('.')

def getLoginBar(request):
    return requests.get(
        "http://bang:9023/_loginBar",
        headers={
            "Cookie" : request.headers.get('cookie', ''),
            'x-site': 'http://bigasterisk.com/openidProxySite/diarybot'
        }).text


_agent = {} # jid : uri
_foafName = {} # uri : name
def makeBots(application, configFilename):
    bots = {}
    g = Graph()
    g.parse(configFilename, format='n3')
    for botNode, botJid, password, name, birthdate in g.query("""
      SELECT DISTINCT ?botNode ?botJid ?password ?name ?birthdate WHERE {
        ?botNode a db:DiaryBot;
          rdfs:label ?name;
          foaf:jabberID ?botJid .
        OPTIONAL {
          ?botNode db:password ?password .
        }
        OPTIONAL {
         ?botNode bio:event [ a bio:Birth; bio:date ?birthdate ] .
        }
      }""", initNs=INIT_NS):
        if birthdate is not None:
            birthdate = parse(birthdate).replace(tzinfo=tz.gettz('UTC'))
        b = Bot(str(name), botJid, password,
                set(g.objects(botNode, DB['owner'])),
                birthdate=birthdate,
                autotexts=sorted(list(map(unicode, g.objects(botNode, DB['autotext']))))
        )
        if hasattr(b, 'client'):
            b.client.setServiceParent(application)
        bots[str(name)] = b

        b.historyQueries = []
        for hq in g.objects(botNode, DB['historyQuery']):
            b.historyQueries.append(
                OffsetTime(daysAgo=int(g.value(hq, DB['daysAgo'])),
                           labelAgo=g.value(hq, RDFS['label']),
                           urlSuffix=str(g.value(hq, DB['urlSuffix']))))

    for s,p,o in g.triples((None, FOAF['jabberID'], None)):
        _agent[str(o)] = s

    for s,p,o in g.triples((None, FOAF['name'], None)):
        _foafName[s] = o

    return bots

def agentUriFromJid(jid):
    # someday this will be a web service for any of my apps to call
    j = jid.userhost()
    return _agent[j]

class NullMongo:
    def __init__(self, *args):
        pass
    def find(self, *args, **kw):
        return self
    sort = limit = __getitem__ = find
    def __iter__(self):
        return iter([])
#Connection = NullMongo

class Bot(object):
    """
    one jabber account; one nag timer
    """
    def __init__(self, name, botJid, password, owners, birthdate=None, autotexts=None):
        self.currentNag = None
        self.name = name
        self.owners = owners
        self.birthdate = birthdate
        self.autotexts = autotexts or []
        self.repr = "Bot(%r,%r,%r,%r)" % (name, botJid, password, owners)
        self.jid = JID(botJid)
        self.mongo = MongoClient('bang', 27017)['diarybot'][name]

        self.availableSubscribers = set()
        if CHAT_SUPPORT:
            log.info("xmpp client %s", self.jid)
            self.client = XMPPClient(self.jid, password)
            self.client.logTraffic = False
            self.messageProtocol = MessageWatch(self.jid,
                                                     self.getStatus, self.save)
            self.messageProtocol.setHandlerParent(self.client)

            PresenceWatch(self.availableSubscribers).setHandlerParent(self.client)

        self.nagDelay = 86400 * .5 # get this from the config
        self.rescheduleNag()

    def __repr__(self):
        return self.repr

    def viewableBy(self, user):
        return user in self.owners

    def lastUpdateTime(self):
        "seconds, or None if there are no updates"
        lastCreated = self.mongo.find(
            projection=['created']).sort('created', -1).limit(1)
        lastCreated = list(lastCreated)
        if not lastCreated:
            return None
        return float(lastCreated[0]['created'].strftime("%s"))

    def getStatus(self):
        """user asked '?'"""
        last = self.lastUpdateTime()
        now = time.time()
        if last is None:
            ago = "never"
        else:
            last_d = datetime.datetime.fromtimestamp(last).replace(tzinfo=tz.tzutc())
            now = datetime.datetime.now(tz.tzutc()).replace(tzinfo=tz.tzutc())
            dt = (now - last_d).total_seconds()
            if 59 < dt < 86400:
                ago = '%.2f hours ago' % (dt / 3600)
            else:
                ago = datestr(last_d.replace(tzinfo=None)) # right zone?
        msg = "last update was %s (%s)" % (ago, last)
        if self.currentNag is None:
            msg += "; no nag"
        else:
            msg += "; nag in %s secs" % (self.currentNag.getTime() - time.time())
        return msg

    def rescheduleNag(self):
        if self.currentNag is not None and not self.currentNag.cancelled:
            self.currentNag.cancel()

        last = self.lastUpdateTime()
        if last is None:
            dt = 3
        else:
            dt = max(2, self.nagDelay - (time.time() - self.lastUpdateTime()))
        self.currentNag = reactor.callLater(dt, self.sendNag)

    def sendNag(self):
        self.currentNag = None
        if not self.availableSubscribers:
            self.rescheduleNag()
            return

        for u in self.availableSubscribers:
            if u.userhost() == self.jid.userhost():
                continue
            self.sendMessage(u, "What's up?")

    def save(self, userUri, msg, userJid=None):
        """
        userJid is for jabber responses, resource is not required
        """
        assert isinstance(msg, unicode)
        if msg == 'error':
            raise NotImplementedError()
        if msg.strip() == '?':
            self.sendMessage(userJid, self.getStatus())
            return
        try:
            # shouldn't this be getting the time from the jabber message?
            now = datetime.datetime.now(tz.tzlocal())
            doc = {
                # close enough for now
                'dc:created' : now.isoformat(),
                'sioc:content' : msg,
                'dc:creator' : userUri,
                'created' : now.astimezone(tz.gettz('UTC')), # mongo format, for sorting. Loses timezone.
                }
            self.mongo.insert(doc)

        except Exception as e:
            if userJid is not None:
                self.sendMessage(userJid, "Failed to save: %s" % e)
            raise

        try:
            self.tellEveryone(userUri, msg, userJid)
            self.rescheduleNag()
        except Exception as e:
            log.error(e)
            log.info("failed alerts don't stop save from succeeding")

    def tellEveryone(self, userUri, msg, userJid):
        notified = set()
        if userJid is not None:
            self.sendMessage(userJid, "Recorded!")
            notified.add(userJid.userhost())

        msg = "%s wrote: %s" % (userUri, msg)

        for otherOwner in self.owners.difference({userUri}):
            requests.post('http://bang:9040/', data={
                'user' : otherOwner,
                'msg' : msg,
                'mode' : 'email'
            })

        for u in self.availableSubscribers: # wrong, should be -all- subscribers
            log.debug("consider send to %s", u)
            uh = u.userhost()
            if uh == self.jid.userhost():
                log.debug("  skip- that's the bot")
                continue

            if uh in notified:
                log.debug("  skip- already notified that userhost")
                continue

            notified.add(uh)

            # ought to get the foaf full name of this user
            log.debug("sending to %s", u)
            self.sendMessage(u, )


    def sendMessage(self, toJid, msg):
        m = domish.Element((None, "message"))
        m["to"] = toJid.full()
        m["from"] = self.jid.full()
        m["type"] = 'email'
        m.addElement("body", content=msg)
        self.messageProtocol.send(m)

if CHAT_SUPPORT:
    class MessageWatch(MessageProtocol):
        def __init__(self, me, getStatus, save):
            self.me = me
            self.getStatus = getStatus
            self.save = save

        def connectionMade(self):
            self.send(AvailablePresence())

        def connectionLost(self, reason):
            log.info("Disconnected!")

        def onMessage(self, msg):
            if JID(msg['from']).userhost() == self.me.userhost():
                return
            try:
                if msg["type"] == 'chat' and msg.body:
                    userJid = JID(msg['from'])
                    user = agentUriFromJid(userJid)
                    self.save(userUri=user, msg=unicode(msg.body), userJid=userJid)
            except (KeyError, AttributeError):
                pass

    class PresenceWatch(PresenceClientProtocol):
        def __init__(self, availableSubscribers):
            PresenceClientProtocol.__init__(self)
            self.availableSubscribers = availableSubscribers

        def availableReceived(self, entity, show=None, statuses=None, priority=0):
            self.availableSubscribers.add(entity)
            log.info("availableReceived %r", vars())

        def unavailableReceived(self, entity, statuses=None):
            self.availableSubscribers.discard(entity)
            log.info("unavailableReceived %r", vars())


def getAgent(request):
    try:
        return URIRef(request.headers['X-Foaf-Agent'])
    except KeyError:
        return None

class index(cyclone.web.RequestHandler):
    def get(self):
        self.set_header('Content-type', 'text/html')

        agent = getAgent(self.request)

        visible = set()
        for bot in self.settings.bots.values():
            if bot.viewableBy(agent):
                visible.add(bot)

        loader.reset()
        self.write(loader.load('index.html').generate(
            bots=sorted(visible, key=lambda b: (len(b.owners), b.name)),
            loginBar=getLoginBar(self.request),
            json=json,
            ))

class message(cyclone.web.RequestHandler):
    def post(self, botName):
        agent = getAgent(self.request)
        bot = self.settings.bots[botName]
        msg = self.get_argument('msg')
        print 'msg %r' % msg

        bot.save(agent, msg)
        self.write("saved")

class Query(object):
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
        rows = mongo.find({"created" : {"$lt" : end}}).sort('created', -1).limit(10)
        rows = reversed(list(rows))
        return rows

class Last50(Query):
    name = 'last 50 entries'
    desc = name
    suffix = '/recent'
    def run(self, mongo):
        return mongo.find(limit=50, sort=[('created', -1)])

class Latest(Query):
    name = 'latest entry'
    desc = name
    suffix = '/latest'
    def run(self, mongo):
        return mongo.find(limit=1, sort=[('created', -1)])

class All(Query):
    name = 'all'
    desc = 'history'
    suffix = None
    def run(self, mongo):
        return mongo.find().sort('created', -1)

class history(cyclone.web.RequestHandler):
    def get(self, botName, selection=None):
        agent = getAgent(self.request)
        bot = self.settings.bots[botName]

        if not bot.viewableBy(agent):
            raise ValueError("cannot view %s" % botName)

        queries = [OffsetTime(365, 'a year ago', '/yearAgo'), All(), Last50(), Latest()]
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
            entries.append((row['dc:created'], row['dc:creator'], row['sioc:content']))

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
        self.set_header('Content-type', 'text/html')

        if self.get_argument('entriesOnly',''):
             self.write(loader.load('diaryviewentries.html').generate(**d))
             return
        self.write(loader.load('diaryview.html').generate(**d))

def main():
    from twisted.python import log as twlog
    twlog.startLogging(sys.stdout)

    bots = makeBots(None, "bots-secret.n3")
    #chat = ChatInterface()

    reactor.listenTCP(
        9048,
        cyclone.web.Application([
            (r'/', index),
            (r'/(elements\.html)', cyclone.web.StaticFileHandler, {'path': '.'}),
            (r'/([^/]+)/message', message),
            (r'/([^/]+)/history(/[^/]+)?', history),
        ]
                                #+ chat.routes()
                                , bots=bots, debug=True),
        interface='::')
    reactor.run()


if __name__ == '__main__':
    main()
