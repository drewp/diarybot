#!bin/python
"""
rewrite of diarybot.py. Use web.py instead of twisted;
ejabberd/mod_rest/mod_motion instead of any XMPP in-process; mongodb
store instead of rdf in files.
"""

import time, web, sys
sys.path.insert(0, ".") # buildout's python isn't including curdir, which
                       # i need for autoreload of this module
import twisted
assert twisted.__version__ == '10.0.0'
from twisted.web import server, wsgi
from twisted.python.threadpool import ThreadPool
from twisted.internet import reactor
from twisted.words.xish import domish
from wokkel.client import XMPPClient
from wokkel.xmppim import MessageProtocol, AvailablePresence, PresenceClientProtocol
from twisted.words.protocols.jabber.jid import JID
from rdflib import Namespace, RDFS
from rdflib.Graph import Graph
from dateutil import tz
from web.utils import datestr # just for the debug message
import datetime
from pymongo import Connection, DESCENDING
from rdflib import URIRef
import restkit
from web.contrib.template import render_genshi

render = render_genshi('.', auto_reload=True)

XS = Namespace("http://www.w3.org/2001/XMLSchema#")
SIOC = Namespace("http://rdfs.org/sioc/ns#")
DC = Namespace("http://purl.org/dc/terms/")
DB = Namespace("http://bigasterisk.com/ns/diaryBot#")
FOAF = Namespace("http://xmlns.com/foaf/0.1/")
INIT_NS = dict(sioc=SIOC, dc=DC, db=DB, foaf=FOAF, rdfs=RDFS.RDFSNS)

bots = None # replaced by .tac file

def getLoginBar():
    openidProxy = restkit.Resource("http://bang:9023/")
    return openidProxy.get("_loginBar",
                 headers={"Cookie" : web.ctx.environ.get('HTTP_COOKIE', '')}).body

_agent = {} # jid : uri
def makeBots(application, configFilename):
    bots = {}
    g = Graph()
    g.parse(configFilename, format='n3')
    for botNode, botJid, password, name in g.query("""
      SELECT DISTINCT ?botNode ?botJid ?password ?name WHERE {
        ?botNode a db:DiaryBot;
          rdfs:label ?name;
          foaf:jabberID ?botJid;
          db:password ?password .
      }""", initNs=INIT_NS):
        b = Bot(str(name), botJid, password, list(g.objects(botNode, DB['owner'])))
        b.client.setServiceParent(application)
        bots[str(name)] = b

    for s,p,o in g.triples((None, FOAF['jabberID'], None)):
        _agent[str(o)] = s

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
    def __init__(self, name, botJid, password, owners):
        self.currentNag = None
        self.name = name
        self.owners = owners
        self.repr = "Bot(%r,%r,%r,%r)" % (name, botJid, password, owners)
        self.jid = JID(botJid)
        self.mongo = Connection('bang', 27017)['diarybot'][name]
        print "xmpp client", self.jid
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
            self.mongo.insert(doc)
            
        except Exception, e:
            if userJid is not None:
                self.sendMessage(userJid, "Failed to save: %s" % e)
            raise

        for u in self.availableSubscribers: # wrong, should be -all- subscribers
            if u.userhost() == self.jid.userhost():
                continue
            
            if u == userJid:
                self.sendMessage(u, "Recorded!")
            else:
                # ought to get the foaf full name of this user
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
        print "Disconnected!"

    def onMessage(self, msg):
        if JID(msg['from']).userhost() == self.me.userhost():
            return
        
        if msg["type"] == 'chat' and hasattr(msg, "body") and msg.body:
            userJid = JID(msg['from'])
            user = agentUriFromJid(userJid)
            self.save(userUri=user, msg=str(msg.body), userJid=userJid)
            

class PresenceWatch(PresenceClientProtocol):
    def __init__(self, availableSubscribers):
        PresenceClientProtocol.__init__(self)
        self.availableSubscribers = availableSubscribers
   
    def availableReceived(self, entity, show=None, statuses=None, priority=0):
        self.availableSubscribers.add(entity)
        print "av", vars()
        
    def unavailableReceived(self, entity, statuses=None):
        self.availableSubscribers.discard(entity)
        print "un", vars()


# for testing
#web.ctx.environ['HTTP_X_FOAF_AGENT'] = "http://bigasterisk.com/foaf.rdf#drewp"

class index(object):
    def GET(self):
        web.header('Content-type', 'application/xhtml+xml')

        agent = URIRef(web.ctx.environ['HTTP_X_FOAF_AGENT'])

        visible = set()
        for bot in bots.values():
            if bot.viewableBy(agent):
                visible.add(bot)
        
        return render.index(
            bots=sorted(visible),
            loginBar=getLoginBar()
            )

class message(object):
    def POST(self, botName):
        agent = URIRef(web.ctx.environ['HTTP_X_FOAF_AGENT'])
        bot = bots[botName]
        bot.save(agent, web.input().msg)
        return "saved"

class history(object):
    def GET(self, botName):
        web.header('Content-type', 'application/xhtml+xml')

        agent = URIRef(web.ctx.environ['HTTP_X_FOAF_AGENT'])
        
        bot = bots[botName]

        if not bot.viewableBy(agent):
            raise ValueError("cannot view %s" % botName)

        entries = []
        for row in bot.mongo.find().sort('created', DESCENDING):
            entries.append((row['dc:created'], row['dc:creator'], row['sioc:content']))

        def prettyDate(iso):
            datePart = iso.split('T')[0]
            d = datetime.date(*[int(x, 10) for x in datePart.split('-')])
            return d.strftime("%Y-%m-%d %a")
        
        return render.diaryview(
            bot=bot,
            agent=agent,
            entries=entries,
            prettyDate=prettyDate,
            loginBar=getLoginBar())
    
urls = (
    r'/', "index",
    r'/([^/]+)/message', 'message',
    r'/([^/]+)/history', 'history',
    )

app = web.application(urls, globals(), autoreload=False)
application = app.wsgifunc()

thread_pool = ThreadPool()
thread_pool.start()
reactor.addSystemEventTrigger('after', 'shutdown', thread_pool.stop)

site = server.Site(wsgi.WSGIResource(reactor, thread_pool, application))

