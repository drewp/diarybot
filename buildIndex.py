#!/usr/bin/python
"""
send diarybot entries to search
"""
import requests, json
from pymongo import MongoClient as Connection
from diarybot2 import uriForDoc

for bot in ['aribot', 'asherbot']:
    coll = Connection('bang', 27017)['diarybot'][bot]
    for row in coll.find():
        txt = row['sioc:content']
        uri = uriForDoc(bot, row)

        label = {
            'http://bigasterisk.com/kelsi/foaf.rdf#kelsi' : 'Kelsi',
            'http://bigasterisk.com/foaf.rdf#drewp' : 'Drew',
            }.get(row['dc:creator'], row['dc:creator'])

        doc = dict(uri=uri,
                   title="%s entry by %s at %s" % (bot, label, row['dc:created']),
                   text=txt)
        requests.post("http://bang:9096/index", params={'source': bot}, data=json.dumps(doc)).raise_for_status()
