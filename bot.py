import datetime
import logging
import time

from bson import ObjectId
from dateutil import tz
from dateutil.parser import parse
from pymongo import MongoClient
from rdflib import Namespace, RDFS, URIRef, Graph
from rdflib.term import Node
from structuredinput import structuredInputElementConfig, kvFromMongoList, englishInput, mongoListFromKv
from twisted.internet import reactor
from twisted.internet.defer import ensureDeferred, Deferred
from typing import Dict, Optional, Tuple, Set, List
import requests

from chatinterface import ChatInterface
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
log = logging.getLogger('bot')


def makeBots(chat, configGraph):
    g = configGraph
    bots: Dict[str, Bot] = {}

    for botNode, name, birthdate in g.query("""
      SELECT DISTINCT ?botNode ?name ?birthdate WHERE {
        ?botNode a db:DiaryBot;
          rdfs:label ?name .
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
            owners=set(g.objects(botNode, DB['owner'])),
            chat=chat,
            birthdate=birthdate,
            structuredInput=structuredInputElementConfig(g, botNode),
        )
        bots[str(name)] = b

        b.historyQueries = []
        for hq in g.objects(botNode, DB['historyQuery']):
            b.historyQueries.append(
                OffsetTime(daysAgo=int(g.value(hq, DB['daysAgo'])),
                           labelAgo=g.value(hq, RDFS['label']),
                           urlSuffix=str(g.value(hq, DB['urlSuffix']))))

    return bots


class Bot:
    """one slack account; one nag timer."""
    def __init__(
            self,
            uri: URIRef,
            configGraph: Graph,
            name: str,
            owners: Set[URIRef],
            chat: ChatInterface,
            birthdate: Optional[datetime.datetime],
            structuredInput: Optional[Dict],
            slack=True,
    ):
        self.uri = uri
        self.configGraph = configGraph
        self.currentNag = None
        self.name = name
        self.owners = owners
        self.birthdate = birthdate
        self.structuredInput = structuredInput
        self.chat = chat
        self.repr = 'Bot(uri=%r,name=%r)' % (self.uri, self.name)
        self.mongo = MongoClient('bang5', 27017)['diarybot'][self.name]

        self.availableSubscribers = set()

        self.nagDelay = 86400 * .5  # get this from the config
        self.rescheduleNag()

        def finish():
            log.info('Bot.finish')
            token = self.configGraph.value(self.uri,
                                           DB['slackBotUserOauth']).toPython()
            d = self.chat.initBot(self, token)
            d.addErrback(log.error)
            return d

        reactor.callLater(0, finish)

    def __repr__(self):
        return self.repr

    def assertUserCanWrite(self, user: URIRef) -> None:
        if user not in self.owners:
            raise ValueError('not owner')

    def assertUserCanRead(self, user: URIRef) -> None:
        if user not in self.owners:
            raise ValueError('not owner')

    def uriForDoc(self, d) -> URIRef:
        return URIRef('http://bigasterisk.com/diary/%s/%s' %
                      (self.name, d['_id']))

    def lastUpdateTime(self) -> Optional[float]:
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

    def getStatus(self) -> str:
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

    def doseStatuses(self) -> List[str]:
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
            dt = 10
        else:
            dt = max(10, self.nagDelay - (time.time() - self.lastUpdateTime()))

        def go():
            return ensureDeferred(self.sendNag())

        log.info(f'rescheduleNag {self.name} to {dt}')
        self.currentNag = reactor.callLater(dt, go)

    async def sendNag(self):
        self.currentNag = None
        msg = "What's up?"
        reachedAtLeastOOne = False
        for owner in self.owners:
            if await self.chat.userIsOnline(owner):
                await self.chat.sendMsg(self, owner, msg)
                reachedAtLeastOOne = True
        if not reachedAtLeastOOne:
            self.rescheduleNag()

    def _mongoDoc(self,
                  userUri: URIRef,
                  msg: str = None,
                  kv: Optional[Dict[Node, Node]] = None) -> Tuple[Dict, str]:
        now = datetime.datetime.now(tz.tzlocal())
        doc = {
            # close enough for now
            'dc:created': now.isoformat(),
            'dc:creator': userUri,
            # mongo format, for sorting. Loses timezone.
            'created': now.astimezone(tz.gettz('UTC')),
        }

        if msg is not None and msg.strip():
            doc['sioc:content'] = msg
        elif kv is not None:
            doc['structuredInput'] = mongoListFromKv(kv)
            msg = 'structured input: %r' % englishInput(self.configGraph, kv)
        else:
            raise TypeError
        return doc, msg

    def save(self, userUri: URIRef, msg: Optional[str] = None, kv: Optional[Dict[str, str]] = None) -> Deferred:
        return ensureDeferred(self._save(userUri, msg, kv))

    async def _save(self, user: URIRef, msg: Optional[str] = None, kv: Optional[Dict[str, str]] = None) -> URIRef:
        print('user %r sends msg %r kv %r' %
              (user, msg, kv))  # this log has saved me before

        self.assertUserCanWrite(user)

        doc, formatMsg = self._mongoDoc(user, msg, kv)

        newId = self.mongo.insert_one(doc).inserted_id
        newUri = self.uriForDoc({'_id': newId})

        try:
            await self._tellEveryone(doc, formatMsg)
            self.rescheduleNag()
        except Exception as e:
            log.error(e)
            log.info("failed alerts don't stop save from succeeding")

        return newUri

    def delete(self, user: URIRef, docId):
        self.assertUserCanWrite(user)

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
                'dc:creator': user,
                'created': now.astimezone(tz.gettz('UTC')),
                'deleted': True,
            },
            '$unset': {
                'sioc:content': '',
                'structuredInput': '',
            },
        })

    def updateTime(self, user: URIRef, docId, newTime):
        self.assertUserCanWrite(user)

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
                'dc:creator': user,
                'created': newTime.astimezone(tz.gettz('UTC')),
            },
        })

    async def _tellEveryone(self, doc: Dict, formatMsg: str) -> None:
        user: URIRef = doc['dc:creator']

        msg = '%s wrote: %s' % (user, formatMsg)

        for otherOwner in self.owners:
            if otherOwner == user:
                continue
            if await self.chat.userIsOnline(otherOwner):
                await self.chat.sendMsg(self, otherOwner, msg)
            else:
                requests.post('http://bang5:9040/',
                              data={
                                  'user': otherOwner,
                                  'msg': msg,
                                  'mode': 'email'
                              })
