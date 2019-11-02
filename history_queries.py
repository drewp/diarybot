import datetime


class Query(object):
    suffix = None

    def makeLink(self, currentQuery):
        levels = (currentQuery.suffix or '').count('/')
        return './' + '../' * levels + 'history' + (self.suffix or '')

    def makeHomeLink(self):
        levels = (self.suffix or '').count('/')
        return '../' * (levels + 1)


class OffsetTime(Query):
    def __init__(self, daysAgo, labelAgo, urlSuffix):
        self.name = self.desc = labelAgo
        self.daysAgo = daysAgo
        self.suffix = urlSuffix

    def run(self, mongo):
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

    def run(self, mongo):
        return mongo.find({'deleted': {
            '$exists': False
        }},
                          limit=150,
                          sort=[('created', -1)])


class Latest(Query):
    name = 'latest entry'
    desc = name
    suffix = '/latest'

    def run(self, mongo):
        return mongo.find({'deleted': {
            '$exists': False
        }},
                          limit=1,
                          sort=[('created', -1)])


class All(Query):
    name = 'all'
    desc = 'history'
    suffix = None

    def run(self, mongo):
        return mongo.find({'deleted': {'$exists': False}}).sort('created', -1)
