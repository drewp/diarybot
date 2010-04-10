#!bin/python
"""
rewrite of diarybot.py. Use web.py instead of twisted;
ejabberd/mod_rest/mod_motion instead of any XMPP in-process; mongodb
store instead of rdf in files.
"""
from gevent import wsgi, spawn_later
import time, urllib2, web, sys
from dateutil import tz
from web.utils import datestr # just for the debug message
from datetime import datetime
#from xml.utils import iso8601
from pymongo import Connection, DESCENDING
from rdflib import URIRef
import restkit
sys.path.append(".") # buildout's python isn't including curdir, which
                     # i need for autoreload of this module
from rfc3339 import rfc3339

from web.contrib.template import render_genshi
render = render_genshi('.', auto_reload=True)

def getLoginBar():
    openidProxy = restkit.Resource("http://bang:9023/")
    return openidProxy.get("_loginBar",
                 headers={"Cookie" : web.ctx.environ.get('HTTP_COOKIE', '')})


def literalFromUnix(t):
    return Literal(iso8601.tostring(t, time.altzone), # todo: timezones
                   datatype=XS.dateTime)

def unixFromLiteral(x):
    return iso8601.parse(str(x))

def makeBots(application, configFilename):
    g = Graph()
    g.parse(configFilename, format='n3')
    for botJid, password, storeUri in g.query("""
      SELECT ?botJid ?password ?storeUri WHERE {
        [ a db:DiaryBot;
          foaf:jabberID ?botJid;
          db:password ?password;
          db:store ?storeUri ]
      }""", initNs=INIT_NS):
        b = Bot(botJid, password, storeUri)
        b.client.setServiceParent(application)
    # also add http server for forms-based entry


class Bot(object):
    """
    one jabber account; one nag timer
    """
    def __init__(self, name):
        self.mongo = Connection('bang', 27017)['diarybot'][name]
##         self.client = XMPPClient(self.jid, password)
##         self.client.logTraffic = False
##         self.messageProtocol = MessageWatch(self.jid,
##                                                  self.getStatus, self.save)
##         self.messageProtocol.setHandlerParent(self.client)

        self.availableSubscribers = set()
##         PresenceWatch(self.availableSubscribers).setHandlerParent(self.client)

        self.currentNag = None
        self.nagDelay = 86400 * .5 # get this from the config
        self.rescheduleNag()

    def lastUpdateTime(self):
        "seconds, or None if there are no updates"
        lastCreated = self.mongo.find(fields=['created']
                                      ).sort('created', DESCENDING).limit(1)[0]
        return float(lastCreated['created'].strftime("%s"))

        rows = list(self.store.queryData("""
          SELECT ?t WHERE {
            [ a sioc:Post; dc:created ?t ]
          } ORDER BY desc(?t) LIMIT 1"""))
        if not rows:
            return None
        return unixFromLiteral(rows[0][0])

    def getStatus(self):
        """user asked '?'"""
        last = self.lastUpdateTime()
        now = time.time()
        if last is None:
            ago = "never"
        else:
            ago = datestr(datetime.fromtimestamp(last))
        msg = "last update was %s (%s)" % (ago, last)
        if self.currentNag is None:
            msg += "; no nag"
        else:
            msg += "; nag in %s secs" % (self.currentNag.getTime() - now)
        return msg

    def rescheduleNag(self):
        return
        if self.currentNag is not None:
            self.currentNag.cancel()

        last = self.lastUpdateTime()
        if last is None:
            dt = 3
        else:
            dt = max(0, self.nagDelay - (time.time() - self.lastUpdateTime()))
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
            
    def save(self, user, msg):
        """
        user should be JID, resource is not required
        """
        if msg.strip() == '?':
            self.sendMessage(user, self.getStatus())
            return

        now = datetime.now(tz.tzlocal())
        doc = {
            # shouldn't this be getting the time from the jabber message?
            # close enough for now
            'dc:created' : now.isoformat(),
            'sioc:content' : msg,
            'dc:creator' : user,
            'created' : now.astimezone(tz.gettz('UTC')), # mongo format, for sorting. Loses timezone.
            }
        self.mongo.insert(doc)

        for u in self.availableSubscribers: # wrong, should be -all- subscribers
            if u.userhost() == self.jid.userhost():
                continue
            if u.userhost() == user.userhost():
                self.sendMessage(u, "Recorded!")
            else:
                self.sendMessage(u, "%s wrote: %s" % (user.userhost(), msg))

        self.rescheduleNag()

    def sendMessage(self, toJid, msg):
        m = domish.Element((None, "message"))
        m["to"] = toJid.full()
        m["from"] = self.jid.full()
        m["type"] = 'chat'
        m.addElement("body", content=msg)
        self.messageProtocol.send(m)

class MessageWatch:#(MessageProtocol):
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
            self.save(JID(msg['from']), str(msg.body))
            

class PresenceWatch:#(PresenceClientProtocol):
    def __init__(self, availableSubscribers):
        PresenceClientProtocol.__init__(self)
        self.availableSubscribers = availableSubscribers
   
    def availableReceived(self, entity, show=None, statuses=None, priority=0):
        self.availableSubscribers.add(entity)
        print "av", vars()
        
    def unavailableReceived(self, entity, statuses=None):
        self.availableSubscribers.discard(entity)
        print "un", vars()


bots = {'healthbot' : Bot('healthbot')}

class index(object):
    def GET(self):
        web.header('Content-type', 'application/xhtml+xml')


        # for testing!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        web.ctx.environ['HTTP_X_FOAF_AGENT'] = "http://bigasterisk.com/foaf.rdf#drewp"

        agent = URIRef(web.ctx.environ['HTTP_X_FOAF_AGENT'])

        if agent == URIRef("http://bigasterisk.com/kelsi/foaf.rdf#kelsi"):
            bot = URIRef("file:///my/proj/diarybot/data/kelsihealthbot.nt")
        elif agent == URIRef("http://bigasterisk.com/foaf.rdf#drewp"):
            bot = URIRef("file:///my/proj/diarybot/data/healthbot.nt")
            
        
        return render.index(
            bots=bots,
            loginBar=getLoginBar())

class message(object):
    def POST(self):
        # for testing!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        web.ctx.environ['HTTP_X_FOAF_AGENT'] = "http://bigasterisk.com/foaf.rdf#drewp"
        
        bot = bots[web.input().bot]
        bot.save(URIRef(web.ctx.environ['HTTP_X_FOAF_AGENT']), web.input().msg)
        raise web.seeother(".")
    
urls = (r'/', "index",
        r'/message', 'message',
        )

app = web.application(urls, globals(), autoreload=True)
application = app.wsgifunc()

if __name__ == '__main__':
    def gr():
        print "Greetz"
    print dir(spawn_later(3, gr))
    wsgi.WSGIServer(('', 9048), application).serve_forever()

