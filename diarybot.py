import time, urllib2
from twisted.internet import reactor
from twisted.words.xish import domish
from twisted.words.protocols.jabber.jid import JID
from wokkel.xmppim import MessageProtocol, AvailablePresence, PresenceClientProtocol
from wokkel.client import XMPPClient
from web.utils import datestr # just for the debug message
from rdflib import Literal, Namespace, BNode, RDF, URIRef
from rdflib.Graph import Graph
from datetime import datetime
from xml.utils import iso8601

XS = Namespace("http://www.w3.org/2001/XMLSchema#")
SIOC = Namespace("http://rdfs.org/sioc/ns#")
DC = Namespace("http://purl.org/dc/terms/")
DB = Namespace("http://bigasterisk.com/ns/diaryBot#")
FOAF = Namespace("http://xmlns.com/foaf/0.1/")
INIT_NS = dict(sioc=SIOC, dc=DC, db=DB, foaf=FOAF)

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

class Bot(object):
    """
    one jabber account; one nag timer
    """
    def __init__(self, botJid, password, storeUri):
        self.botJid = botJid
        self.storeUri = storeUri
        self.jid = JID(botJid)
        self.client = XMPPClient(self.jid, password)
        self.client.logTraffic = False
        self.messageProtocol = MessageWatch(self.jid,
                                                 self.getStatus, self.save)
        self.messageProtocol.setHandlerParent(self.client)

        self.availableSubscribers = set()
        PresenceWatch(self.availableSubscribers).setHandlerParent(self.client)

        self.currentNag = None
        self.nagDelay = 86400 * .5 # get this from the config
        self.rescheduleNag()

    def _getDataGraph(self):
        g = Graph()
        try:
            g.parse(self.storeUri, format="nt")
        except urllib2.URLError:
            print "%s file missing- starting a new one" % self.storeUri
        return g

    def queryData(self, query):
        g = self._getDataGraph()
        return g.query(query, initNs=INIT_NS)

    def writeStatements(self, stmts):
        g = self._getDataGraph()
        for s in stmts:
            g.add(s)
        g.serialize(self.storeUri, format="nt")
        print "wrote %s" % self.storeUri

    def lastUpdateTime(self):
        "seconds, or None if there are no updates"
        rows = list(self.queryData("""
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
            if u.userhost() == self.botJid:
                continue
            self.sendMessage(u, "What's up?")
            
    def save(self, user, msg):
        """
        user should be JID, resource is not required
        """
        # shouldn't this be getting the time from the jabber message?
        # close enough for now
        post = BNode()

        self.writeStatements([
            (post, RDF.type, SIOC.Post),
            (post, DC.created, literalFromUnix(time.time())),
            (post, SIOC.content, Literal(msg)),
        
            (post, DC.creator, Literal(user)), # todo
            #(post, SIOC.has_creator, user),
             
            # need to connect this post to the right bot-forum
            (URIRef("http://example.com/forum"), SIOC.container_of, post),
             ])

        for u in self.availableSubscribers: # wrong, should be -all- subscribers
            if u.userhost() == self.botJid or u.userhost() == user.userhost():
                continue
            self.sendMessage(u, "%s wrote: %s" % (user.userhost(), msg))

        self.rescheduleNag()

    def sendMessage(self, toJid, msg):
        m = domish.Element((None, "message"))
        m["to"] = toJid.full()
        m["from"] = self.botJid
        m["type"] = 'chat'
        m.addElement("body", content=msg)

        self.messageProtocol.send(m)

class MessageWatch(MessageProtocol):
    def __init__(self, me, getStatus, save):
        self.me = me
        self.getStatus = getStatus
        self.save = save
        
    def connectionMade(self):
        print "Connected!"

        # send initial presence
        self.send(AvailablePresence())

    def connectionLost(self, reason):
        print "Disconnected!"

    def onMessage(self, msg):
        if JID(msg['from']).userhost() == self.me.userhost():
            return
        
        if msg["type"] == 'chat' and hasattr(msg, "body") and msg.body:
            if str(msg.body).strip() == '?':
                ret = self.getStatus()
            else:
                self.save(JID(msg['from']), str(msg.body))
                ret = "Recorded!"
            
            reply = domish.Element((None, "message"))
            reply["to"] = msg["from"]
            reply["from"] = msg["to"]
            reply["type"] = 'chat'
            reply.addElement("body", content=ret)

            self.send(reply)
            

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

