import datetime
import re
from pymongo.collection import Collection
from pymongo.cursor import Cursor


class Query(object):
    suffix = None

    def makeLink(self, currentQuery) -> str:
        levels = (currentQuery.suffix or '').count('/')
        return './' + '../' * levels + 'history' + (self.suffix or '')

    def makeHomeLink(self) -> str:
        levels = (self.suffix or '').count('/')
        return '../' * (levels + 1)


class OffsetTime(Query):
    def __init__(self, daysAgo: int, labelAgo: str, urlSuffix: str):
        self.name = self.desc = labelAgo
        self.daysAgo = daysAgo
        self.suffix = urlSuffix

    def run(self, mongo: Collection) -> Cursor:
        end = datetime.datetime.now() - datetime.timedelta(days=self.daysAgo)
        rows = mongo.find({
            'deleted': {
                '$exists': False
            },
            'created': {
                '$lt': end
            }
        }).sort('created', -1).limit(10)
        rows = reversed(list(rows))
        return rows


class Last150(Query):
    name = 'last 150 entries'
    desc = name
    suffix = '/recent'

    def run(self, mongo: Collection) -> Cursor:
        return mongo.find({'deleted': {
            '$exists': False
        }},
                          limit=150,
                          sort=[('created', -1)])

class Bedtimes(Query):
    name = 'bedtimes'
    desc = name
    suffix = '/bedtimes'

    def run(self, mongo: Collection) -> Cursor:
        return mongo.find({
            'deleted': {'$exists': False},
            '$or': [
                {'sioc:content':
                 re.compile('^bed$', re.I)},
                {'structuredInput': [
                    "<http://bigasterisk.com/ns/diaryBot#activity>",
                    "<http://bigasterisk.com/ns/diaryBot#bed>"
                    ]},
            ]
        },
                          limit=300,
                          sort=[('created', -1)])


class Latest(Query):
    name = 'latest entry'
    desc = name
    suffix = '/latest'

    def run(self, mongo: Collection) -> Cursor:
        return mongo.find({'deleted': {
            '$exists': False
        }},
                          limit=1,
                          sort=[('created', -1)])


class All(Query):
    name = 'all'
    desc = 'history'
    suffix = None

    def run(self, mongo: Collection) -> Cursor:
        return mongo.find({'deleted': {'$exists': False}}).sort('created', -1)
