import jsonlib, time, urllib2
from twisted.internet import reactor
from twisted.words.xish import domish
from wokkel.xmppim import MessageProtocol, AvailablePresence, PresenceClientProtocol
from twisted.words.protocols.jabber import jid
from wokkel.client import XMPPClient
from twisted.words.protocols.jabber.jid import JID
from web.utils import datestr
from rdflib import Literal, Namespace, BNode, RDF
from rdflib.Graph import Graph
from datetime import datetime, timedelta
from xml.utils import iso8601
XS = Namespace("http://www.w3.org/2001/XMLSchema#")
SIOC = Namespace("http://rdfs.org/sioc/ns#")
DC = Namespace("http://purl.org/dc/terms/")
DB = Namespace("http://bigasterisk.com/ns/diaryBot#")
FOAF = Namespace("http://xmlns.com/foaf/0.1/")
INIT_NS = dict(sioc=SIOC, dc=DC, db=DB, foaf=FOAF)


def literalFromUnix(t):
    return Literal(iso8601.tostring(t, time.altzone), # todo
                   datatype=XS.dateTime)

def unixFromLiteral(x):
    return iso8601.parse(str(x))

def makeBots(application, configFilename):
    g = Graph()
    g.parse(configFilename, format='n3')
    for botJid, password in g.query("""
      SELECT ?botJid ?password WHERE {
        [ a db:DiaryBot;
          foaf:jabberID ?botJid;
          db:password ?password ]
      }""", initNs=INIT_NS):
        b = Bot(botJid, password)
        b.client.setServiceParent(application)

class Bot(object):
    """
    one jabber account; one nag timer
    """
    def __init__(self, botJid, password):
        self.botJid = botJid
        self.jid = jid.internJID(botJid)
        self.client = XMPPClient(self.jid, password)
        self.client.logTraffic = False
        self.messageProtocol = HealthBotProtocol(self.getStatus, self.save)
        self.messageProtocol.setHandlerParent(self.client)

        Nag().setHandlerParent(self.client)

        self.currentNag = None
        self.nagDelay = 86400 * .5
        self.rescheduleNag()

    def getConfigGraph(self):
        g = Graph()
        g.parse("config.nt", format="nt")
        return g

    def getDataGraph(self):
        g = Graph()
        try:
            g.parse("data.nt", format="nt")
        except urllib2.URLError:
            print "data.nt file missing- starting a new one"
        return g

    def saveDataGraph(self, g):
        g.serialize("data.nt", format="nt")
        print "wrote data.nt"

    def lastUpdateTime(self):
        "seconds, or None if there are no updates"
        g = self.getDataGraph()
        rows = list(g.query("""
          SELECT ?t WHERE {
            [ a sioc:Post; dc:created ?t ]
          } ORDER BY desc(?t) LIMIT 1""", initNs=INIT_NS))
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
        print repr(last), dt
        self.currentNag = reactor.callLater(dt, self.sendNag)

    def sendNag(self):
        self.currentNag = None

        # this should send to all available users of the bot
        msg = domish.Element((None, "message"))
        msg["to"] = u'drewp@jabber.bigasterisk.com/Coccinella@dash'
        msg["from"] = self.botJid
        msg["type"] = 'chat'
        msg.addElement("body", content="what's up?")

        self.messageProtocol.send(msg)

    def save(self, user, msg):
        # shouldn't this be getting the time from the jabber message?
        # close enough for now
        g = self.getDataGraph()
        post = BNode()

        g.add((post, RDF.type, SIOC.Post))
        g.add((post, DC.created, literalFromUnix(time.time())))
        g.add((post, SIOC.content, Literal(msg)))
        
        g.add((post, DC.creator, Literal(user))) # todo
        #g.add((post, SIOC.has_creator, user))

        # need to connect this post to the right bot-forum

        self.saveDataGraph(g)

        self.rescheduleNag()

class HealthBotProtocol(MessageProtocol):
    def __init__(self, getStatus, save):
        self.getStatus = getStatus
        self.save = save
        
    def connectionMade(self):
        print "Connected!"

        # send initial presence
        self.send(AvailablePresence())

    def connectionLost(self, reason):
        print "Disconnected!"

    def onMessage(self, msg):
        if msg["type"] == 'chat' and hasattr(msg, "body") and msg.body:
            if str(msg.body).strip() == '?':
                ret = self.getStatus()
            else:
                self.save(msg['from'], str(msg.body))
                # i guess this forwards the body to all the other users
                # of the bot who got a nag message
                ret = "ok thanks"
            
            reply = domish.Element((None, "message"))
            reply["to"] = msg["from"]
            reply["from"] = msg["to"]
            reply["type"] = 'chat'
            reply.addElement("body", content=ret)

            self.send(reply)
            

class Nag(PresenceClientProtocol):
    def __init__(self):
        PresenceClientProtocol.__init__(self)
   
    def availableReceived(self, entity, show=None, statuses=None, priority=0):
        print "av", vars()
        
    def unavailableReceived(self, entity, statuses=None):
        print "un", vars()

