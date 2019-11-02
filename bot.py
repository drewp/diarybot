from bson import ObjectId
from dateutil import tz
from dateutil.parser import parse
from pymongo import MongoClient
from rdflib import Namespace, RDFS, URIRef
from structuredinput import structuredInputElementConfig, kvFromMongoList, englishInput, mongoListFromKvs
from twisted.internet import reactor
from twisted.internet.defer import ensureDeferred
from typing import Dict
import datetime
import logging
import requests
import time

from datestr import datestr
from history_queries import OffsetTime

BOT = Namespace('http://bigasterisk.com/bot/')
XS = Namespace('http://www.w3.org/2001/XMLSchema#')
SIOC = Namespace('http://rdfs.org/sioc/ns#')
DC = Namespace('http://purl.org/dc/terms/')
DB = Namespace('http://bigasterisk.com/ns/diaryBot#')
FOAF = Namespace('http://xmlns.com/foaf/0.1/')
BIO = Namespace('http://vocab.org/bio/0.1/')
SCHEMA = Namespace('http://schema.org/')
INIT_NS = dict(sioc=SIOC, dc=DC, db=DB, foaf=FOAF, rdfs=RDFS.uri, bio=BIO)

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger()


def uriForDoc(botName, d):
    return URIRef('http://bigasterisk.com/diary/%s/%s' % (botName, d['_id']))


def makeBots(chat, configGraph):
    g = configGraph
    bots = {}

    for botNode, password, name, birthdate in g.query("""
      SELECT DISTINCT ?botNode ?password ?name ?birthdate WHERE {
        ?botNode a db:DiaryBot;
          rdfs:label ?name .
        OPTIONAL {
          ?botNode db:password ?password .
        }
        OPTIONAL {
         ?botNode bio:event [ a bio:Birth; bio:date ?birthdate ] .
        }
      }""",
                                                      initNs=INIT_NS):
        if birthdate is not None:
            birthdate = parse(birthdate).replace(tzinfo=tz.gettz('UTC'))

        b = Bot(
            botNode,
            g,
            str(name),
            password,
            set(g.objects(botNode, DB['owner'])),
            birthdate=birthdate,
            structuredInput=structuredInputElementConfig(g, botNode),
            chat=chat,
        )
        bots[str(name)] = b

        b.historyQueries = []
        for hq in g.objects(botNode, DB['historyQuery']):
            b.historyQueries.append(
                OffsetTime(daysAgo=int(g.value(hq, DB['daysAgo'])),
                           labelAgo=g.value(hq, RDFS['label']),
                           urlSuffix=str(g.value(hq, DB['urlSuffix']))))

    return bots


class Bot(object):
    """one jabber account; one nag timer."""
    def __init__(self,
                 uri,
                 configGraph,
                 name,
                 password,
                 owners,
                 birthdate=None,
                 structuredInput=None,
                 chat=None):
        self.uri = uri
        self.configGraph = configGraph
        self.currentNag = None
        self.name = name
        self.owners = owners
        self.birthdate = birthdate
        self.structuredInput = structuredInput or []
        self.chat = chat
        self.repr = 'Bot(uri=%r,name=%r)' % (self.uri, self.name)
        self.mongo = MongoClient('bang', 27017)['diarybot'][self.name]

        self.availableSubscribers = set()

        self.nagDelay = 86400 * .5  # get this from the config
        self.rescheduleNag()

        def finish():
            log.info('Bot.finish')
            token = self.configGraph.value(self.uri,
                                           DB['slackBotUserOauth']).toPython()
            d = ensureDeferred(self.chat.initBot(self, token))
            d.addErrback(log.error)
            return d

        reactor.callLater(1, finish)

    def __repr__(self):
        return self.repr

    def viewableBy(self, user):
        return user in self.owners

    def lastUpdateTime(self):
        """seconds, or None if there are no updates."""
        nonDeleted = {'deleted': {'$exists': False}}
        lastCreated = self.mongo.find(nonDeleted,
                                      projection=['created'
                                                  ]).sort('created',
                                                          -1).limit(1)
        lastCreated = list(lastCreated)
        if not lastCreated:
            return None
        return float(lastCreated[0]['created'].strftime('%s'))

    def getStatus(self):
        """user asked '?'."""
        last = self.lastUpdateTime()
        now = time.time()
        if last is None:
            ago = 'never'
        else:
            last_d = datetime.datetime.fromtimestamp(last).replace(
                tzinfo=tz.tzutc())
            now = datetime.datetime.now(tz.tzutc()).replace(tzinfo=tz.tzutc())
            dt = (now - last_d).total_seconds()
            if 59 < dt < 86400:
                ago = '%.2f hours ago' % (dt / 3600)
            else:
                ago = datestr(last_d.replace(tzinfo=None))  # right zone?
        msg = 'last update was %s' % ago
        if self.currentNag is None:
            msg += '; no nag'
        else:
            msg += '; nag in %s secs' % round(
                self.currentNag.getTime() - time.time(), 1)

        msg += ' \n%s' % ('\n'.join(self.doseStatuses()))

        return msg

    def doseStatuses(self):
        """lines like 'last foo was 1.5h ago, take next at 15:10'."""
        now = datetime.datetime.now(tz.tzutc()).replace(tzinfo=tz.tzutc())
        reports = []

        drugsSeen = set()
        for doc in self.mongo.find({
                'deleted': {
                    '$exists': False
                },
                'structuredInput': {
                    '$exists': True
                },
                'created': {
                    '$gt': now - datetime.timedelta(hours=20)
                }
        }).sort('created', -1):
            kvs = kvFromMongoList(doc['structuredInput'])
            kvs = dict(kvs)
            if SCHEMA['drug'] in kvs:
                if kvs[SCHEMA['drug']] not in drugsSeen:
                    drugsSeen.add(kvs[SCHEMA['drug']])
                    msg = englishInput(self.configGraph, kvs)
                    if msg:
                        createdZ = doc['created'].replace(tzinfo=tz.tzutc())
                        secAgo = (now - createdZ).total_seconds()
                        # and link to the entry
                        msg += ' %.2f hours ago' % (secAgo / 3600.)
                        reports.append(msg)
        return reports

    def rescheduleNag(self):
        if self.currentNag is not None and not self.currentNag.cancelled:
            self.currentNag.cancel()

        last = self.lastUpdateTime()
        if last is None:
            dt = 3
        else:
            dt = max(2, self.nagDelay - (time.time() - self.lastUpdateTime()))

        def go():
            return ensureDeferred(self.sendNag())

        self.currentNag = reactor.callLater(dt, go)

    async def sendNag(self):
        self.currentNag = None
        msg = "What's up?"
        reachedSomeone = False
        for owner in self.owners:
            if await self.chat.userIsOnline(owner):
                await self.chat.sendMsg(self, owner, msg)
                reachedSomeone = True
        if not reachedSomeone:
            self.rescheduleNag()

    def _mongoDoc(self, userUri: URIRef, msg: str = None, kv=None) -> Dict:
        now = datetime.datetime.now(tz.tzlocal())
        doc = {
            # close enough for now
            'dc:created': now.isoformat(),
            'dc:creator': userUri,
            # mongo format, for sorting. Loses timezone.
            'created': now.astimezone(tz.gettz('UTC')),
        }

        if msg is not None:
            doc['sioc:content'] = msg
        elif kv is not None:
            doc['structuredInput'] = mongoListFromKvs(kv)
            msg = 'structured input: %r' % englishInput(self.configGraph, kv)
        else:
            raise TypeError
        return doc

    async def save(self, userUri: URIRef, msg: str = None, kv=None):
        if userUri not in self.owners:
            raise ValueError('forbidden')

        doc = self._mongoDoc(userUri, msg, kv)

        newId = self.mongo.insert_one(doc).inserted_id
        newUri = uriForDoc(self.name, {'_id': newId})

        try:
            await self._tellEveryone(doc)
            self.rescheduleNag()
        except Exception as e:
            log.error(e)
            log.info("failed alerts don't stop save from succeeding")

        return newUri

    def delete(self, userUri, docId):
        now = datetime.datetime.now(tz.tzlocal())

        oldRow = self.mongo.find_one({
            '_id': ObjectId(docId),
            'deleted': {
                '$exists': False
            }
        })
        if 'history' in oldRow:
            del oldRow['history']
        del oldRow['_id']
        self.mongo.find_one_and_update({'_id': ObjectId(docId)}, {
            '$push': {
                'history': oldRow
            },
            '$set': {
                'dc:created': now.isoformat(),
                'dc:creator': userUri,
                'created': now.astimezone(tz.gettz('UTC')),
                'deleted': True,
            },
            '$unset': {
                'sioc:content': '',
                'structuredInput': '',
            },
        })

    def updateTime(self, userUri, docId, newTime):
        oldRow = self.mongo.find_one({
            '_id': ObjectId(docId),
            'deleted': {
                '$exists': False
            }
        })
        if 'history' in oldRow:
            del oldRow['history']
        del oldRow['_id']
        self.mongo.find_one_and_update({'_id': ObjectId(docId)}, {
            '$push': {
                'history': oldRow
            },
            '$set': {
                'dc:created': newTime.isoformat(),
                'dc:creator': userUri,
                'created': newTime.astimezone(tz.gettz('UTC')),
            },
        })

    async def _tellEveryone(self, doc):
        userUri: URIRef = doc['dc:creator']

        content = doc['sioc:content']
        if not content:
            kvs = kvFromMongoList(doc['structuredInput'])
            content = englishInput(self.configGraph, kvs)
        msg = '%s wrote: %s' % (userUri, content)

        for otherOwner in self.owners:
            if otherOwner == userUri:
                continue
            if await self.chat.userIsOnline(otherOwner):
                await self.chat.sendMsg(self, otherOwner, msg)
            else:
                requests.post('http://bang:9040/',
                              data={
                                  'user': otherOwner,
                                  'msg': msg,
                                  'mode': 'email'
                              })
