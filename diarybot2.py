#!bin/python
"""
rewrite of diarybot.py. Use web.py instead of twisted;
ejabberd/mod_rest/mod_motion instead of any XMPP in-process; mongodb
store instead of rdf in files.
"""
from __future__ import division
import time, web, sys
sys.path.insert(0, ".") # buildout's python isn't including curdir, which
                       # i need for autoreload of this module
import twisted
#assert twisted.__version__ == '10.0.0'
from twisted.web import server, wsgi
from twisted.python.threadpool import ThreadPool
from twisted.internet import reactor
from twisted.words.xish import domish
from wokkel.client import XMPPClient
from wokkel.xmppim import MessageProtocol, AvailablePresence, PresenceClientProtocol
from twisted.words.protocols.jabber.jid import JID
from rdflib import Namespace, RDFS
from rdflib import Graph
from dateutil import tz
from dateutil.parser import parse
from web.utils import datestr # just for the debug message
import datetime
from pymongo import Connection, DESCENDING
from rdflib import URIRef
import restkit, logging
from web.contrib.template import render_genshi

import rdflib
from rdflib import plugin
plugin.register(
  "sparql", rdflib.query.Processor,
  "rdfextras.sparql.processor", "Processor")
plugin.register(
  "sparql", rdflib.query.Result,
  "rdfextras.sparql.query", "SPARQLQueryResult") 

render = render_genshi('.', auto_reload=True)

XS = Namespace("http://www.w3.org/2001/XMLSchema#")
SIOC = Namespace("http://rdfs.org/sioc/ns#")
DC = Namespace("http://purl.org/dc/terms/")
DB = Namespace("http://bigasterisk.com/ns/diaryBot#")
FOAF = Namespace("http://xmlns.com/foaf/0.1/")
BIO = Namespace ("http://vocab.org/bio/0.1/")
INIT_NS = dict(sioc=SIOC, dc=DC, db=DB, foaf=FOAF, rdfs=RDFS.uri, bio=BIO)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger()
bots = None # replaced by .tac file

def getLoginBar():
    openidProxy = restkit.Resource("http://bang:9023/")
    return openidProxy.get("_loginBar",
                 headers={"Cookie" : web.ctx.environ.get('HTTP_COOKIE', '')}).body_string()

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
          foaf:jabberID ?botJid;
          db:password ?password .
        OPTIONAL {
         ?botNode bio:event [ a bio:Birth; bio:date ?birthdate ] .
        }
      }""", initNs=INIT_NS):
        if birthdate is not None:
            birthdate = parse(birthdate).replace(tzinfo=tz.gettz('UTC'))
        b = Bot(str(name), botJid, password,
                list(g.objects(botNode, DB['owner'])),
                birthdate=birthdate)
        b.client.setServiceParent(application)
        bots[str(name)] = b

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
    def __init__(self, name, botJid, password, owners, birthdate=None):
        self.currentNag = None
        self.name = name
        self.owners = owners
        self.birthdate = birthdate
        self.repr = "Bot(%r,%r,%r,%r)" % (name, botJid, password, owners)
        self.jid = JID(botJid)
        self.mongo = Connection('bang', 27017)['diarybot'][name]
        log.info("xmpp client %s", self.jid)
        self.client = XMPPClient(self.jid, password)
        self.client.logTraffic = False
        self.messageProtocol = MessageWatch(self.jid,
                                                 self.getStatus, self.save)
        self.messageProtocol.setHandlerParent(self.client)

        self.availableSubscribers = set()
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
            fields=['created']).sort('created', DESCENDING).limit(1)
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
            ago = datestr(datetime.datetime.fromtimestamp(last))
        msg = "last update was %s (%s)" % (ago, last)
        if self.currentNag is None:
            msg += "; no nag"
        else:
            msg += "; nag in %s secs" % (self.currentNag.getTime() - now)
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
            self.mongo.insert(doc, safe=True)
            
        except Exception, e:
            if userJid is not None:
                self.sendMessage(userJid, "Failed to save: %s" % e)
            raise

        notified = set()
        if userJid is not None:
            self.sendMessage(userJid, "Recorded!")
            notified.add(userJid.userhost())

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
            self.sendMessage(u, "%s wrote: %s" % (userUri, msg))

        self.rescheduleNag()

    def sendMessage(self, toJid, msg):
        m = domish.Element((None, "message"))
        m["to"] = toJid.full()
        m["from"] = self.jid.full()
        m["type"] = 'chat'
        m.addElement("body", content=msg)
        self.messageProtocol.send(m)

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
                self.save(userUri=user, msg=str(msg.body), userJid=userJid)
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


# for testing
#web.ctx.environ['HTTP_X_FOAF_AGENT'] = "http://bigasterisk.com/foaf.rdf#drewp"

class index(object):
    def GET(self):
        web.header('Content-type', 'text/html')

        agent = URIRef(web.ctx.environ['HTTP_X_FOAF_AGENT'])

        visible = set()
        for bot in bots.values():
            if bot.viewableBy(agent):
                visible.add(bot)
        
        return render.index(
            bots=sorted(visible),
            loginBar=getLoginBar()
            )

def tryToCorrectQuotes(s):
    # i don't even know how this got here. Pasting the source text
    # makes pretty utf8 quotes, but after a post and a web.py access,
    # I get 0x91-0x93 chars
    return s.replace("\x91", "'").replace("\x92", "'").replace("\x93", '"')
        
class message(object):
    def POST(self, botName):
        agent = URIRef(web.ctx.environ['HTTP_X_FOAF_AGENT'])
        bot = bots[botName]
        # next line has a problem with certain chars in the input
        msg = web.input(_unicode=False).msg

        msg = tryToCorrectQuotes(msg)

        bot.save(agent, msg)
        return "saved"

class Query(object):
    def makeLink(self, currentQuery):
        levels = (currentQuery.suffix or "").count('/')
        return "./" + "../" * levels + "history"+(self.suffix or "")
    
    def makeHomeLink(self):
        levels = (self.suffix or "").count('/')
        return "../" * (levels+1)
        
    
class YearAgo(Query):
    name = 'a year ago'
    desc = name
    suffix = '/yearAgo'
    def run(self, mongo):
        rows = mongo.find({"created" : {
            "$lt" : datetime.datetime.now() - datetime.timedelta(days=365),
            "$gt" : datetime.datetime.now() - datetime.timedelta(days=365+7)
            }}).sort('created', DESCENDING)
        rows = reversed(list(rows))
        return rows

class Last50(Query):
    name = 'last 50 entries'
    desc = name
    suffix = '/recent'
    def run(self, mongo):
        return mongo.find(limit=50, sort=[('created', DESCENDING)])

class Latest(Query):
    name = 'latest entry'
    desc = name
    suffix = '/latest'
    def run(self, mongo):
        return mongo.find(limit=1, sort=[('created', DESCENDING)])

class All(Query):
    name = 'all'
    desc = 'history'
    suffix = None
    def run(self, mongo):
        return mongo.find().sort('created', DESCENDING)

class history(object):
    def GET(self, botName, selection=None):

        agent = URIRef(web.ctx.environ['HTTP_X_FOAF_AGENT'])
        
        bot = bots[botName]

        if not bot.viewableBy(agent):
            raise ValueError("cannot view %s" % botName)

        queries = [YearAgo(), All(), Last50(), Latest()]
        for q in queries:
            if q.suffix == selection:
                rows = list(q.run(bot.mongo))
                query = q
                queries.remove(q)
                break
        else:
            raise ValueError("unknown query %s" % selection)

        if web.input().get('rdf',''):
            # this could have been RDFA in the normal page result
            import json
            for r in rows:
                del r['_id']
                del r['created']
            web.header('Content-type', 'application/json')
            return json.dumps(rows)

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

        d = dict(
            bot=bot,
            agent=agent,
            entries=entries,
            otherQueries=queries,
            query=query,
            prettyName=prettyName,
            prettyDate=prettyDate,
            loginBar=getLoginBar())
        web.header('Content-type', 'application/xhtml+xml')

        if web.input().get('entriesOnly',''):
            return render.diaryviewentries(**d)
        return render.diaryview(**d)
    
urls = (
    r'/', "index",
    r'/([^/]+)/message', 'message',
    r'/([^/]+)/history(/yearAgo|/recent|/latest)?', 'history',
    )

app = web.application(urls, globals(), autoreload=False)
application = app.wsgifunc()

thread_pool = ThreadPool()
thread_pool.start()
reactor.addSystemEventTrigger('after', 'shutdown', thread_pool.stop)

site = server.Site(wsgi.WSGIResource(reactor, thread_pool, application))

